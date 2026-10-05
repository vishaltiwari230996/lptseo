"""Scheduled rank tracking — a 200-query scoreboard with history and a worklist.

``competitors.rank_snapshot`` answered "where do we rank right now" for up to 50
queries, on demand, keyed by calendar date. This module answers "where do we
rank, where did we rank, who is above us, and which of those gaps is worth
closing next" for up to 200 queries on a two-hourly schedule.

Three things make that affordable and durable:

**A pool that rebuilds daily, not every run.** A query set that churns every
sweep produces trend lines that mean nothing, so the pool is rebuilt once a day
and swept twelve times.

**A budget the sweep cannot exceed.** Serper bills per search. The daily counter
is transactional (``state.mutate``) because two sweeps racing through
``load`` + ``save`` would lose counts, and a lost count is money.

**History that rolls up.** Twelve raw points per query per day is the resolution
the owner asked for; keeping it forever is not. Raw points survive a week, then
collapse to one ``{best, worst, last}`` triple per day for six months.
"""
from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, timezone

from . import jobs, sources, state

#: The pool ceiling. A hard bound in code, not a UI suggestion.
MAX_POOL = 200
#: Characters, not queries — unrelated to MAX_POOL despite the same number.
MAX_QUERY_LEN = 200
#: Below this, a Search Console query is noise rather than demand.
MIN_GSC_IMPRESSIONS = 3
#: Per brand, per UTC date. Sits just above the 2,400 a 2-hourly sweep of 200
#: queries needs, so an unplanned extra sweep is absorbed but a loop is not.
MAX_SEARCHES_PER_DAY = 3000
RAW_RETENTION_DAYS = 7
DAILY_RETENTION_DAYS = 180
#: Harvested related/PAA queries kept between rebuilds.
HARVEST_KEEP = 2000

JOB_KIND = "rank-sweep"

POOL_DOC = "rank-pool-{}"
BUDGET_DOC = "rank-budget-{}"
HARVEST_DOC = "rank-harvest-{}"
LATEST_PREFIX = "rank-latest-{}"
HISTORY_PREFIX = "rank-history-{}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(query: str) -> str:
    """Dedup key. GSC, custom entries and harvested PAA all produce the same
    question with different casing and spacing; they are one query."""
    return re.sub(r"\s+", " ", query or "").strip().lower()


def enabled(brand: dict) -> bool:
    """Two kill switches: one per brand, one service-wide and deploy-free."""
    if os.environ.get("SEO_RANK_SWEEP_DISABLED", "0") == "1":
        return False
    return bool(brand.get("rank_tracking_enabled", True))


def budget_status(brand_id: str, today: date | None = None) -> dict:
    day = (today or _now().date()).isoformat()
    doc = state.load(BUDGET_DOC.format(brand_id)) or {}
    spent = int(doc.get("searches", 0)) if doc.get("date") == day else 0
    return {"date": day, "searches": spent, "cap": MAX_SEARCHES_PER_DAY,
            "remaining": max(0, MAX_SEARCHES_PER_DAY - spent)}


def charge(brand_id: str, n: int = 1, today: date | None = None) -> bool:
    """Reserve ``n`` searches against today's ceiling. False = refused, nothing spent.

    Transactional on purpose: ``load`` + ``save`` around a shared counter loses
    increments when the manual "Run now" button overlaps the cron sweep, and a
    lost increment is a Serper bill nobody authorised.
    """
    day = (today or _now().date()).isoformat()

    def change(current: dict) -> tuple[dict, bool]:
        spent = int(current.get("searches", 0)) if current.get("date") == day else 0
        if spent + n > MAX_SEARCHES_PER_DAY:
            return {"date": day, "searches": spent}, False
        return {"date": day, "searches": spent + n}, True

    return state.mutate(BUDGET_DOC.format(brand_id), change)


def record_harvest(brand_id: str, queries: list[str]) -> None:
    """Count related/PAA questions a sweep saw. These arrive free inside every
    SERP response, which is what lets the pool keep growing with real Google
    queries when Search Console is unavailable."""
    if not queries:
        return

    def change(current: dict) -> tuple[dict, None]:
        counts = dict(current.get("counts") or {})
        for raw in queries:
            key = _norm(raw)
            if key and len(key) <= MAX_QUERY_LEN:
                counts[key] = counts.get(key, 0) + 1
        trimmed = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:HARVEST_KEEP])
        return {"counts": trimmed}, None

    state.mutate(HARVEST_DOC.format(brand_id), change)


def _harvest_ranked(brand_id: str) -> list[str]:
    counts = (state.load(HARVEST_DOC.format(brand_id)) or {}).get("counts") or {}
    return [q for q, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def build_pool(brand: dict, rows_fn=None) -> dict:
    """Merge every query source into one capped, deduplicated, provenanced pool.

    Priority order is the whole design: what the owner asked for explicitly,
    then what Search Console proves people search, then what Google itself
    volunteered in related/PAA blocks, then our own strategy seeds. Earlier
    sources win the dedup and survive the cap.

    ``rows_fn(brand) -> (rows, notes)`` matches the deep audit's convention —
    the router owns the GSC property and date window, so this stays testable
    without a Google client.
    """
    from . import competitors

    notes: list[str] = []
    sources_used: list[str] = []
    candidates: list[tuple[str, str, int]] = []  # (query, source, impressions)
    max_impressions: dict[str, int] = {}  # track max impressions per normalized key

    for q in competitors.list_custom_queries(brand["id"]):
        candidates.append((q, "custom", 0))
        key = _norm(q)
        if key:
            max_impressions[key] = max(max_impressions.get(key, 0), 0)
    if candidates:
        sources_used.append("custom")

    rows, gsc_notes = [], []
    if rows_fn:
        try:
            rows, gsc_notes = rows_fn(brand)
        except Exception as exc:  # noqa: BLE001 — Search Console is optional here
            gsc_notes = [f"Search Console: {exc}"]
    notes.extend(gsc_notes)
    if rows:
        totals: dict[str, tuple[str, int]] = {}
        for row in rows:
            key = _norm(row.query)
            label, seen = totals.get(key, (row.query, 0))
            totals[key] = (label, seen + int(row.impressions or 0))
        ranked = sorted(totals.values(), key=lambda pair: (-pair[1], pair[0]))
        for label, impressions in ranked:
            if impressions >= MIN_GSC_IMPRESSIONS:
                candidates.append((label, "gsc", impressions))
                key = _norm(label)
                if key:
                    max_impressions[key] = max(max_impressions.get(key, 0), impressions)
        sources_used.append("gsc")

    harvested = _harvest_ranked(brand["id"])
    for q in harvested:
        candidates.append((q, "harvest", 0))
        key = _norm(q)
        if key:
            max_impressions[key] = max(max_impressions.get(key, 0), 0)
    if harvested:
        sources_used.append("harvest")

    seeds = competitors.tracked_keywords(brand)
    for q in seeds:
        candidates.append((q, "seed", 0))
        key = _norm(q)
        if key:
            max_impressions[key] = max(max_impressions.get(key, 0), 0)
    if seeds:
        sources_used.append("seed")

    previous = {_norm(q["query"]): q for q in (latest_pool(brand["id"]) or {}).get("queries", [])}
    stamp = _now().isoformat(timespec="seconds")

    chosen: dict[str, dict] = {}
    for label, source, impressions in candidates:
        key = _norm(label)
        if not key or len(key) > MAX_QUERY_LEN or key in chosen:
            continue
        if len(chosen) >= MAX_POOL:
            break
        chosen[key] = {
            "query": label.strip(),
            "source": source,
            "impressions": max_impressions.get(key, impressions),
            "added_at": previous.get(key, {}).get("added_at", stamp),
            "active": True,
        }

    # A query that left the pool keeps its row, deactivated: its history is
    # still worth reading, and a deleted row would orphan it.
    for key, old in previous.items():
        if key not in chosen:
            chosen[key] = {**old, "active": False}

    doc = {
        "queries": list(chosen.values()),
        "built_at": stamp,
        "sources_used": sources_used,
        "notes": notes,
    }
    state.save(POOL_DOC.format(brand["id"]), doc)
    return doc


def latest_pool(brand_id: str) -> dict | None:
    return state.load(POOL_DOC.format(brand_id))


def active_queries(brand_id: str) -> list[str]:
    return [q["query"] for q in (latest_pool(brand_id) or {}).get("queries", []) if q.get("active")]
