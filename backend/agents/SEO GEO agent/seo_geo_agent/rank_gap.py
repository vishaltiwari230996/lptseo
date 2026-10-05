"""Why they beat us, for one query.

The worklist says which gap to close; this says what the gap is. It runs on
click rather than on sweep, because fetching two pages and asking an LLM about
them 200 times every two hours would cost more than the rank data itself.

Cached for a day per (query, competitor URL): clicking around the table after
the first look is free.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from . import rank_tracker, sources, state

CACHE_DOC = "rank-gap-{}-{}"
CACHE_HOURS = 24

SYSTEM = (
    "You are an SEO analyst. Given two pages competing for one search query, "
    "say in at most four sentences what the higher-ranking page covers that the "
    "lower-ranking one does not — including any questions it answers that the "
    "other page does not — and what to change. Be concrete. Do not pad."
)


def _key(query: str, url: str) -> str:
    return hashlib.sha1(f"{query}|{url}".encode()).hexdigest()[:16]


def _facts(page) -> dict:
    """Adapt a ``sources.PageFacts`` into the signals this card diffs on.

    ``word_count`` is already computed by the fetcher — never re-derive it
    from ``text.split()``. There is no ``headings`` attribute on PageFacts;
    the heading signal is ``h2 + h3`` (h1 is the page title, not a competing
    section). ``questions`` is PageFacts's own property (``h2 + h3`` entries
    ending in ``?``) — a competitor answering questions we do not is exactly
    the kind of gap this card exists to name.
    """
    return {
        "words": page.word_count,
        "headings": list(page.h2) + list(page.h3),
        "schema": list(page.schema_types),
        "questions": list(page.questions),
    }


def explain(brand: dict, query: str, fetch=None, llm=None, now=None) -> dict | None:
    """The diff plus one LLM sentence-set. ``None`` when the query was never
    swept, or when nothing ranks above us."""
    moment = now or datetime.now(timezone.utc)
    row = next((r for r in rank_tracker.latest_rows(brand["id"])
                if rank_tracker._norm(r["query"]) == rank_tracker._norm(query)), None)
    if not row:
        return None

    position = row.get("position")
    above = [e for e in (row.get("top") or []) if position is None or e["position"] < position]
    if not above:
        return None
    leader = above[0]

    cache_id = CACHE_DOC.format(brand["id"], _key(query, leader["url"]))
    cached = state.load(cache_id)
    if cached:
        age = moment - datetime.fromisoformat(cached["at"])
        if age < timedelta(hours=CACHE_HOURS):
            return {**cached, "cached": True}

    fetch = fetch or sources.fetch_page
    llm = llm or (lambda system, prompt: sources.llm_text(system, prompt, agent_id="a2"))
    notes: list[str] = []

    def facts_for(url: str) -> dict | None:
        if not url:
            return None
        try:
            page = fetch(url)
        except Exception as exc:  # noqa: BLE001 — a blocked page is a note, not a 500
            notes.append(f"{url}: {exc}"[:200])
            return None
        # fetch_page does not raise on an HTTP-level failure — a 403, a 404,
        # a timeout/connect error (status left at 0) — it hands back a normal
        # PageFacts with status set and everything else empty. Treated as
        # ordinary zeros, that is indistinguishable from a genuinely thin
        # page and the LLM would confidently "explain" content that was never
        # read. Anything other than 200 is unknown, not empty.
        status = getattr(page, "status", 200)
        if status != 200:
            notes.append((f"{url}: HTTP {status}" if status else f"{url}: fetch failed")[:200])
            return None
        return _facts(page)

    ours = facts_for(row.get("url") or "")
    theirs = facts_for(leader["url"])

    prompt = (
        f"Query: {query}\n"
        f"Their page (#{leader['position']}, {leader['domain']}): {leader['url']}\n"
        f"  words={(theirs or {}).get('words')}, headings={(theirs or {}).get('headings')}, "
        f"schema={(theirs or {}).get('schema')}, questions={(theirs or {}).get('questions')}\n"
        f"Our page (#{position}): {row.get('url') or 'we do not rank for this query'}\n"
        f"  words={(ours or {}).get('words')}, headings={(ours or {}).get('headings')}, "
        f"schema={(ours or {}).get('schema')}, questions={(ours or {}).get('questions')}\n"
    )
    try:
        narrative = llm(SYSTEM, prompt)
    except Exception as exc:  # noqa: BLE001
        narrative = "Could not generate an explanation — the metrics below still stand."
        notes.append(f"LLM: {exc}"[:200])

    doc = {
        "query": row["query"],
        "our_url": row.get("url") or "",
        "our_position": position,
        "their_url": leader["url"],
        "their_domain": leader["domain"],
        "their_position": leader["position"],
        "metrics": {
            "words": {"ours": (ours or {}).get("words"), "theirs": (theirs or {}).get("words")},
            "headings": {"ours": len((ours or {}).get("headings") or []) if ours else None,
                         "theirs": len((theirs or {}).get("headings") or []) if theirs else None},
            "schema": {"ours": (ours or {}).get("schema"), "theirs": (theirs or {}).get("schema")},
            "questions": {"ours": len((ours or {}).get("questions") or []) if ours else None,
                          "theirs": len((theirs or {}).get("questions") or []) if theirs else None},
        },
        "narrative": narrative,
        "notes": notes,
        "at": moment.isoformat(timespec="seconds"),
    }
    state.save(cache_id, doc)
    return {**doc, "cached": False}
