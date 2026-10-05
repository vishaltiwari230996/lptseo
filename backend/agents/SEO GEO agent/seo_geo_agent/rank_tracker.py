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
#: The sweep's own "did I roll up today" stamp. Deliberately NOT a field on
#: POOL_DOC: build_pool() writes a wholesale fresh dict to that document, so
#: a same-day pool rebuild between two sweeps would silently erase a stamp
#: kept there, forcing a redundant rollup. Sweep state belongs in its own
#: document that only the sweep touches.
ROLLUP_DOC = "rank-rollup-{}"


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


def _epoch_hours(moment: datetime) -> int:
    """Hour resolution keeps a point to a small integer. Twelve points a day
    for 200 queries adds up; ISO strings would triple the document."""
    return int(moment.timestamp() // 3600)


def _hours_to_date(hours: int) -> date:
    return datetime.fromtimestamp(hours * 3600, tz=timezone.utc).date()


def all_history(brand_id: str) -> list[dict]:
    rows, _ = jobs.load_list(HISTORY_PREFIX.format(brand_id))
    return rows


def history_for(brand_id: str, query: str) -> dict | None:
    key = _norm(query)
    return next((r for r in all_history(brand_id) if _norm(r["query"]) == key), None)


def append_history(brand_id: str, results: list[dict], rivals: list[str],
                   now: datetime | None = None) -> None:
    """Add one point per successful result, for us and for each tracked rival.

    Errored results are skipped deliberately. A Serper outage returns an empty
    organic list for every query at once; recording that as ``position: None``
    would write a site-wide collapse into history and light up the worklist
    with 200 phantom regressions.
    """
    stamp = _epoch_hours(now or _now())
    watched = [d.lower() for d in (rivals or [])]
    rows = {_norm(r["query"]): r for r in all_history(brand_id)}

    for result in results:
        if result.get("error"):
            continue
        key = _norm(result["query"])
        row = rows.get(key) or {"query": result["query"], "raw": [], "daily": [], "rivals": {}}
        row["raw"] = row["raw"] + [[stamp, result.get("position")]]
        by_domain = {entry["domain"]: entry["position"] for entry in result.get("top") or []}
        for domain in watched:
            series = row["rivals"].get(domain) or {"raw": [], "daily": []}
            series["raw"] = series["raw"] + [[stamp, by_domain.get(domain)]]
            row["rivals"][domain] = series
        rows[key] = row

    jobs.save_list(HISTORY_PREFIX.format(brand_id), list(rows.values()))


def _roll_series(series: dict, cutoff_hours: int, oldest_day: date) -> dict:
    """Collapse raw points older than the cutoff into one triple per day, then
    drop dailies older than the retention window."""
    keep_raw = [p for p in series.get("raw", []) if p[0] >= cutoff_hours]
    stale = [p for p in series.get("raw", []) if p[0] < cutoff_hours]

    buckets: dict[str, list[tuple[int, int | None]]] = {}
    for hours, position in stale:
        day_str = _hours_to_date(hours).isoformat()
        buckets.setdefault(day_str, []).append((hours, position))

    daily = {day: triple for day, triple in series.get("daily", [])}
    for day, points_with_hours in buckets.items():
        existing = daily.get(day)
        positions = [pos for _, pos in points_with_hours]
        ranked_positions = [pos for pos in positions if pos is not None]

        # Compute best and worst from ranked positions and existing values
        best_candidates = ranked_positions
        if existing and existing.get("best") is not None:
            best_candidates = best_candidates + [existing["best"]]
        best = min(best_candidates) if best_candidates else None

        worst_candidates = ranked_positions
        if existing and existing.get("worst") is not None:
            worst_candidates = worst_candidates + [existing["worst"]]
        worst = max(worst_candidates) if worst_candidates else None

        # last and at: take chronologically final point (sort by hour first)
        sorted_points = sorted(points_with_hours, key=lambda x: x[0])
        last = sorted_points[-1][1]
        at = sorted_points[-1][0]
        if existing and existing.get("at") is not None and at < existing["at"]:
            # Earlier or same point; keep the existing last and at
            last = existing["last"]
            at = existing["at"]

        daily[day] = {"best": best, "worst": worst, "last": last, "at": at}

    kept = sorted((d, t) for d, t in daily.items() if date.fromisoformat(d) >= oldest_day)
    return {"raw": keep_raw, "daily": [[d, t] for d, t in kept]}


def rollup(brand_id: str, today: date | None = None) -> int:
    """Fold the raw tail into dailies and trim both windows. Returns the number of history rows written.

    Idempotent: a second call finds nothing older than the cutoff, so re-running
    it after a restart or an overlapping sweep is safe.
    """
    day = today or _now().date()
    cutoff = _epoch_hours(datetime(day.year, day.month, day.day, tzinfo=timezone.utc)) \
        - RAW_RETENTION_DAYS * 24
    oldest_day = date.fromordinal(day.toordinal() - DAILY_RETENTION_DAYS)

    rows = all_history(brand_id)
    for row in rows:
        rolled = _roll_series(row, cutoff, oldest_day)
        row["raw"], row["daily"] = rolled["raw"], rolled["daily"]
        for domain, series in (row.get("rivals") or {}).items():
            row["rivals"][domain] = _roll_series(series, cutoff, oldest_day)

    jobs.save_list(HISTORY_PREFIX.format(brand_id), rows)
    return len(rows)


def _ours(link: str, domain: str) -> bool:
    """Host-equality, not substring containment.

    ``domain in link`` — what the old rank_snapshot did — says yes to
    ``https://fake-lawpreptutorial.com.spam.io/x``, recording a competitor's
    spam page as our own rank.
    """
    host = sources.domain_of(link)
    return host == domain.lower() or host.endswith("." + domain.lower())


def latest_rows(brand_id: str) -> list[dict]:
    rows, _ = jobs.load_list(LATEST_PREFIX.format(brand_id))
    return rows


def latest_meta(brand_id: str) -> dict | None:
    _, meta = jobs.load_list(LATEST_PREFIX.format(brand_id))
    return meta


def sweep(brand: dict, progress=None, search=None, now=None) -> dict:
    """One pass over every active query. Never raises for a single bad SERP."""
    brand_id = brand["id"]
    moment = now or _now()
    notes: list[str] = []

    if not enabled(brand):
        return {"checked": 0, "ranked": 0, "errors": 0, "blocked": "disabled",
                "at": moment.isoformat(timespec="seconds"),
                "notes": ["Rank tracking is switched off for this brand"]}

    if search is None:
        if not sources.brand_rank_available():
            return {"checked": 0, "ranked": 0, "errors": 0, "blocked": "credentials",
                    "at": moment.isoformat(timespec="seconds"),
                    "notes": ["SEO_SERPER_API_KEY not set — rank tracking needs live SERPs"]}
        # Global Constraints: SERP country is overridable per brand. This is
        # the only caller that holds the brand doc, so the override is
        # plumbed in here, via a closure, rather than changing what a caller
        # passes — an injected ``search`` (every test above) stays a plain
        # ``search(query)`` call with no extra kwargs.
        gl = (brand.get("serp_country") or sources.SERP_COUNTRY).strip().lower()

        def search(query: str) -> dict:
            return sources.brand_rank_search(query, gl=gl)

    queries = active_queries(brand_id)
    if progress:
        progress.phase(f"checking {len(queries)} queries", total=len(queries))

    rivals = [d.lower() for d in (brand.get("competitors") or [])][:8]
    results: list[dict] = []
    harvested: list[str] = []
    ranked = errors = 0
    blocked = None

    for query in queries:
        if not charge(brand_id, 1, today=moment.date()):
            notes.append(f"Daily search budget reached — stopped after {len(results)} queries")
            blocked = "budget"
            if progress:
                progress.note("daily budget reached, stopping")
            break
        row = {"query": query, "position": None, "url": "",
               "checked_at": moment.isoformat(timespec="seconds"), "top": [], "error": None}
        try:
            serp = search(query)
            organic = serp.get("organic") or []
            if not organic:
                raise ValueError("empty SERP")
            row["top"] = [{"position": entry.get("position", n + 1),
                           "domain": sources.domain_of(entry.get("link", "")),
                           "url": entry.get("link", ""),
                           "title": entry.get("title", "")}
                          for n, entry in enumerate(organic)]
            # Read position/url from the already-normalised `top` list, not the
            # raw organic entry a second time — a provider that omits
            # `position` already gets an enumerate fallback for `top`, and
            # reading the raw entry here would throw that fallback away,
            # silently recording a real rank as unranked (position: None).
            ours = next((e for e in row["top"] if _ours(e["url"], brand["domain"])), None)
            if ours:
                row["position"] = ours["position"]
                row["url"] = ours["url"]
            harvested.extend(serp.get("related") or [])
            harvested.extend(serp.get("paa") or [])
            ranked += 1
        except Exception as exc:  # noqa: BLE001 — one bad SERP must not lose the other 199
            row["error"] = f"{exc}"[:200]
            errors += 1
        results.append(row)
        if progress:
            progress.step()

    jobs.save_list(LATEST_PREFIX.format(brand_id), results,
                   meta={"at": moment.isoformat(timespec="seconds"),
                         "ranked": ranked, "errors": errors, "rivals": rivals})
    record_harvest(brand_id, harvested)
    append_history(brand_id, results, rivals, now=moment)

    today_str = moment.date().isoformat()
    last_rollup = (state.load(ROLLUP_DOC.format(brand_id)) or {}).get("on")
    if last_rollup != today_str:
        rollup(brand_id, today=moment.date())
        state.save(ROLLUP_DOC.format(brand_id), {"on": today_str})

    return {"checked": len(results), "ranked": ranked, "errors": errors, "blocked": blocked,
            "at": moment.isoformat(timespec="seconds"), "notes": notes}


#: Below this the query is already won and effort is better spent elsewhere;
#: above the upper bound it is not winnable inside a quarter.
STRIKING_LOW, STRIKING_HIGH = 4, 20
#: Unranked queries stay visible but sink below winnable work.
UNRANKED_BAND = 0.15


def _position_band(position: int | None) -> float:
    """Value of moving this query, by where it currently sits."""
    if position is None:
        return UNRANKED_BAND
    if position < STRIKING_LOW:
        return 0.2          # already on page one's top half
    if position <= STRIKING_HIGH:
        return 1.0          # striking distance — the whole point of the list
    if position <= 40:
        return 0.5
    return 0.1


def delta(brand_id: str, query: str, hours: int) -> int | None:
    """Positions gained since ``hours`` ago. Negative = we fell. None = no basis.

    The baseline is the most recent point at or before the cutoff, and when the
    series is younger than the window, its earliest point instead. Requiring a
    point older than the cutoff would report None for every query during the
    tracker's first week — exactly when the owner is watching hardest.
    """
    row = history_for(brand_id, query)
    if not row or not row.get("raw"):
        return None
    points = [p for p in row["raw"] if p[1] is not None]
    if len(points) < 2 or points[-1][1] is None:
        return None
    now_point = points[-1]
    cutoff = now_point[0] - hours
    older = [p for p in points[:-1] if p[0] <= cutoff]
    baseline = older[-1] if older else points[0]
    return baseline[1] - now_point[1]


def worklist(brand: dict, limit: int = 10) -> list[dict]:
    """Order the losing queries by how much a win is worth times how winnable it is.

    Deliberately arithmetic rather than an LLM judgement: this list decides
    where a person spends their week, so it has to be explainable, stable
    between runs, and testable.
    """
    brand_id = brand["id"]
    impressions = {_norm(q["query"]): int(q.get("impressions") or 0)
                   for q in (latest_pool(brand_id) or {}).get("queries", [])}
    rivals = {d.lower() for d in (brand.get("competitors") or [])}

    rows: list[dict] = []
    for result in latest_rows(brand_id):
        if result.get("error"):
            continue
        position = result.get("position")
        top = result.get("top") or []
        above = [e for e in top if position is None or e["position"] < position]
        leader = above[0] if above else None
        if not leader:
            continue  # nobody is beating us here

        shown = impressions.get(_norm(result["query"]), 0)
        demand = math.log1p(shown) if shown else 1.0
        band = _position_band(position)
        tracked = leader["domain"] in rivals
        # A rival we already profile is a gap we can actually analyse.
        gap = 1.4 if tracked else 1.0
        moved = delta(brand_id, result["query"], hours=7 * 24)
        # A live regression outranks a long-standing weakness.
        trend = 1.6 if (moved is not None and moved < 0) else 1.0

        rows.append({
            "query": result["query"],
            "position": position,
            "impressions": shown,
            "leader": leader["domain"],
            "leader_position": leader["position"],
            "leader_url": leader["url"],
            "tracked_rival": tracked,
            "delta_7d": moved,
            "score": round(demand * band * gap * trend, 3),
            "reason": _reason(position, shown, tracked, moved),
        })

    rows.sort(key=lambda r: -r["score"])
    return rows[:limit]


def _reason(position, impressions, tracked, moved) -> str:
    bits = []
    if position is None:
        bits.append("not ranking")
    elif STRIKING_LOW <= position <= STRIKING_HIGH:
        bits.append(f"#{position} — striking distance")
    else:
        bits.append(f"#{position}")
    if impressions:
        bits.append(f"{impressions:,} impressions/28d")
    if tracked:
        bits.append("a tracked competitor is above us")
    if moved is not None and moved < 0:
        bits.append(f"down {abs(moved)} this week")
    return "; ".join(bits)
