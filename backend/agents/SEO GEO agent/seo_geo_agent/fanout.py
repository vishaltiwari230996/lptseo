"""Query fan-out — which sub-queries an AI answer engine splits a topic into,
and where this brand stands on each one.

Google's AI Mode and ChatGPT don't retrieve for the typed query alone: they
generate a set of related sub-queries and retrieve for each (the ai-seo
playbook's "query fan-out"). Winning the topic means covering that whole set.
This module builds the set from two sources and joins it against what the
agent already knows:

* **SERP evidence** — one live SERP for the head query: its People-Also-Ask
  questions and related searches are Google's own fan-out, verbatim.
* **Model expansion** — the sub-queries an assistant would spawn for the
  topic (typed JSON, ai_seo playbook in the prompt). Degrades to SERP-only.

Coverage is decided entirely in Python from persisted docs — rank rows, the
keyword pool, the site corpus. The model never sees brand data here; it only
proposes queries. Verdicts:

* ``covered`` — a page ranks top-10 for it (tracked or in GSC)
* ``weak``    — we have a page or an impression trail, but no top-10 rank
* ``gap``     — nothing: this is blog fuel

Cached a day per (brand, query), like the rank-gap card: exploring the panel
after the first build is free.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from . import keyword_pool, knowledge, rank_tracker, sources, state
from .sources import CredentialMissing
from .topics import _tokens

DOC = "fanout-{}-{}"
INDEX = "fanouts-{}"
CACHE_HOURS = 24
MAX_SUBQUERIES = 15
RECENT_KEEP = 20
#: Paste cap per bulk map — each NEW query spends one SERP + one model call;
#: anything past the cap is reported as skipped, never silently dropped.
MAX_BULK = 10

SYSTEM = (
    "You are an AI-search analyst. For the topic in QUERY, list the sub-queries a "
    "generative answer engine (Google AI Mode, ChatGPT) would fan out to when a "
    "user asks it — the related questions and retrievals it spawns under the hood. "
    'Answer with JSON only: {"queries": [str]} — 8 to 12 entries, each a real '
    "search query a person in this market would type, no numbering, no duplicates "
    "of the head query itself.\n\nEXPERT PLAYBOOK:\n"
)


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


def _qhash(query: str) -> str:
    return hashlib.sha1(_norm(query).encode()).hexdigest()[:12]


def _gather(query: str, search, llm, notes: list[str]) -> tuple[dict[str, dict], bool]:
    """Candidate sub-queries with provenance, plus whether the live SERP call
    itself succeeded (an empty-but-successful SERP is a different failure from
    missing credentials, and the caller words them differently)."""
    out: dict[str, dict] = {}
    serp_ok = False

    def add(label: str, source: str) -> None:
        key = _norm(label)
        if not key or key == _norm(query) or len(key) > 200:
            return
        row = out.setdefault(key, {"query": label.strip(), "sources": []})
        if source not in row["sources"]:
            row["sources"].append(source)

    try:
        serp = search(query)
        serp_ok = True
        for q in serp.get("paa") or []:
            add(q, "paa")
        for q in serp.get("related") or []:
            add(q, "related")
    except CredentialMissing as exc:
        notes.append(f"Live SERP unavailable ({exc}) — fan-out built without Google's own PAA/related")

    try:
        raw = llm(SYSTEM + knowledge.playbook("ai_seo"), f"QUERY: {query}")
        for q in (raw.get("queries") or [])[:12]:
            if isinstance(q, str):
                add(q, "ai")
    except CredentialMissing as exc:
        notes.append(f"Model unavailable ({exc}) — fan-out is SERP evidence only")

    return out, serp_ok


def _coverage(brand: dict, sub_norm: str) -> dict:
    """Where the brand stands on one sub-query — persisted docs only."""
    position = None
    url = ""
    impressions = 0

    for row in rank_tracker.latest_rows(brand["id"]):
        if rank_tracker._norm(row["query"]) == sub_norm:
            position = row.get("position")
            url = row.get("url") or ""
            break

    pool = keyword_pool.latest(brand["id"]) or {}
    for row in pool.get("keywords") or []:
        if _norm(row.get("keyword", "")) == sub_norm:
            impressions = int(row.get("impressions") or 0)
            if position is None and (row.get("position") or 0) > 0:
                position = round(row["position"])
                url = url or row.get("page", "")
            break

    covered_by = url
    if not covered_by:
        # Token-overlap against the site corpus: which page targets this topic.
        corpus = state.load(f"corpus-{brand['id']}") or {}
        sub_tokens = _tokens(sub_norm)
        best_score = 0
        for page in corpus.get("pages") or []:
            hay = " ".join([page.get("target_query") or "", *(page.get("topics") or [])])
            score = len(sub_tokens & _tokens(hay))
            if score > best_score:
                best_score, covered_by = score, page.get("url", "")
        if best_score < 2:
            covered_by = ""

    if position is not None and position <= 10:
        verdict = "covered"
    elif covered_by or impressions > 0:
        verdict = "weak"
    else:
        verdict = "gap"
    return {"covered_by": covered_by, "position": position,
            "impressions": impressions, "verdict": verdict}


def latest(brand_id: str, query: str) -> dict | None:
    return state.load(DOC.format(brand_id, _qhash(query)))


def recent(brand_id: str) -> list[str]:
    return (state.load(INDEX.format(brand_id)) or {}).get("queries", [])


def build(brand: dict, query: str, search=None, llm=None, now=None) -> dict:
    """The fan-out map for one head query. Network: one SERP + one model call,
    then a day of cache; everything else is local reads."""
    query = query.strip()
    if not query:
        raise ValueError("Give the head query to fan out")
    moment = now or datetime.now(timezone.utc)

    cached = latest(brand["id"], query)
    if cached:
        try:
            age = moment - datetime.fromisoformat(cached["at"])
            if age < timedelta(hours=CACHE_HOURS):
                return {**cached, "cached": True}
        except (KeyError, ValueError):
            pass

    search = search or sources.serper_search
    llm = llm or (lambda system, prompt: sources.llm_json(system, prompt, agent_id="a2"))
    notes: list[str] = []
    candidates, serp_ok = _gather(query, search, llm, notes)
    if not candidates:
        if serp_ok:
            # Google answered but had no PAA/related for this phrasing, and the
            # model could not fill in — a query problem, not a credentials one.
            raise ValueError(
                "Google returned no PAA or related searches for this query, and the model "
                "is unavailable to expand it — try a broader head query (e.g. the topic "
                "without the city or year)")
        raise CredentialMissing(
            "Neither a live SERP nor the model is available — the fan-out needs at least one "
            "(set the SERP credentials or the OpenRouter key)")

    rows = []
    for key, cand in list(candidates.items())[:MAX_SUBQUERIES]:
        rows.append({**cand, **_coverage(brand, key)})
    order = {"gap": 0, "weak": 1, "covered": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], -(r["impressions"] or 0)))

    doc = {
        "query": query,
        "at": moment.isoformat(timespec="seconds"),
        "subqueries": rows,
        "summary": {v: sum(1 for r in rows if r["verdict"] == v) for v in ("covered", "weak", "gap")},
        "notes": notes,
        "cached": False,
    }
    state.save(DOC.format(brand["id"], _qhash(query)), doc)

    def change(current: dict) -> tuple[dict, None]:
        queries = [q for q in current.get("queries", []) if _norm(q) != _norm(query)]
        return {"queries": [query, *queries][:RECENT_KEEP]}, None

    state.mutate(INDEX.format(brand["id"]), change)
    return doc


def remove(brand_id: str, queries: list[str]) -> list[str]:
    """Delete mapped fan-outs (single or many) and drop them from the recent
    list. Returns what remains. Deleting is local-only — nothing to revoke."""
    cleaned = [q for q in (queries or []) if _norm(q)]
    for q in cleaned:
        state.delete(DOC.format(brand_id, _qhash(q)))
    gone = {_norm(q) for q in cleaned}

    def change(current: dict) -> tuple[dict, list[str]]:
        keep = [x for x in current.get("queries", []) if _norm(x) not in gone]
        return {"queries": keep}, keep

    return state.mutate(INDEX.format(brand_id), change)


def build_many(brand: dict, queries: list[str], search=None, llm=None, now=None) -> dict:
    """Map several head queries in one go (the paste box). Sequential on
    purpose — each new query is a SERP + model spend and DataForSEO rate-limits
    bursts; cached ones cost nothing. Per-query outcomes are reported, one bad
    query never sinks the rest, and everything past MAX_BULK is named as
    skipped rather than silently dropped."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for q in queries or []:
        key = _norm(q)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(q.strip())
    results = []
    for q in cleaned[:MAX_BULK]:
        try:
            doc = build(brand, q, search=search, llm=llm, now=now)
            results.append({"query": doc["query"], "ok": True,
                            "summary": doc["summary"], "cached": doc.get("cached", False)})
        except (ValueError, CredentialMissing) as exc:
            results.append({"query": q, "ok": False, "error": str(exc)[:200]})
    skipped = cleaned[MAX_BULK:]
    return {"results": results, "skipped": skipped}
