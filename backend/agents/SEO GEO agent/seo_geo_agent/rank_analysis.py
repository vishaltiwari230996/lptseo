"""Rank analysis — one structured LLM read over the rank digest, never a dump.

The sweep stores 200 rows × top-20 SERPs × history. None of that reaches the
model: ``digests.rank_digest`` reduces it to counts, named movers, dropouts
and competitor pressure, and that digest is the entire prompt. The answer is
3-6 bullets a person can act on this week.

Cached by digest fingerprint: the panel can call this freely — a new LLM call
happens only when a sweep actually changed the picture (or the cache aged
out). Degrades to deterministic bullets built from the same digest when the
model is unavailable, so the card never renders empty over a working tracker.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from . import digests, knowledge, sources, state
from .sources import CredentialMissing

DOC = "rank-analysis-{}"
CACHE_HOURS = 12
MAX_BULLETS = 6

SYSTEM = (
    "You are an SEO rank analyst. DATA is a pre-aggregated digest of live Google "
    "rank tracking for one brand. Answer with JSON only: {\"bullets\": [str]} — "
    "3-6 bullets, each one concrete finding with its numbers from DATA and, where "
    "obvious, the single next action. Order by urgency: dropouts and falls first, "
    "then competitor pressure, then wins worth protecting.\n"
    "Rules: every number comes from DATA; never invent queries, positions or "
    "competitors. No filler bullets — fewer good bullets beat six padded ones.\n\n"
    "EXPERT PLAYBOOK:\n"
)


def _fingerprint(digest: dict) -> str:
    return hashlib.sha1(json.dumps(digest, sort_keys=True).encode()).hexdigest()[:16]


def _deterministic_bullets(digest: dict) -> list[str]:
    """The honest fallback: the digest said it; we just phrase it."""
    bullets: list[str] = []
    for q in digest.get("dropouts_7d", []):
        bullets.append(f"'{q}' dropped out of the results this week — it ranked recently; "
                       "investigate what changed first.")
    for m in digest.get("movers_down", []):
        where = f" — {m['leader']} is now above at #{m['leader_position']}" if m.get("leader") else ""
        bullets.append(f"'{m['query']}' fell #{m['from']}→#{m['to']}{where}.")
    for p in digest.get("pressure", [])[:2]:
        bullets.append(f"{p['domain']} outranks us on {p['above_us_on']} tracked queries — "
                       "the single biggest competitive overlap.")
    if digest.get("striking_4_20"):
        bullets.append(f"{digest['striking_4_20']} queries sit in striking distance (#4-20) — "
                       "content refreshes pay fastest there.")
    for m in digest.get("movers_up", [])[:1]:
        bullets.append(f"'{m['query']}' climbed #{m['from']}→#{m['to']} — whatever changed there is working.")
    return bullets[:MAX_BULLETS]


def latest(brand_id: str) -> dict | None:
    return state.load(DOC.format(brand_id))


def analyse(brand: dict, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    stamp = moment.isoformat(timespec="seconds")
    digest = digests.rank_digest(brand)
    if not digest:
        return {"at": stamp, "bullets": [
            "No rank data yet — run a sweep from the Rank tracker to fill this in."],
            "llm": False, "cached": False, "digest": None}

    fingerprint = _fingerprint(digest)
    cached = latest(brand["id"])
    if cached and cached.get("fingerprint") == fingerprint:
        try:
            age = moment - datetime.fromisoformat(cached["at"])
        except (KeyError, ValueError):
            age = timedelta(hours=CACHE_HOURS + 1)
        if age < timedelta(hours=CACHE_HOURS):
            return {**cached, "cached": True}

    llm_worked = False
    bullets: list[str] = []
    try:
        raw = sources.llm_json(SYSTEM + knowledge.playbook("seo_audit"),
                               f"DATA:\n{json.dumps(digest, ensure_ascii=False)}",
                               agent_id="a2", fast=False)
        bullets = [str(b).strip()[:300] for b in raw.get("bullets", []) if str(b).strip()][:MAX_BULLETS]
        llm_worked = bool(bullets)
    except CredentialMissing:
        pass
    if not bullets:
        bullets = _deterministic_bullets(digest)

    doc = {"at": stamp, "fingerprint": fingerprint, "bullets": bullets,
           "llm": llm_worked, "digest": digest, "cached": False}
    state.save(DOC.format(brand["id"]), doc)
    return doc
