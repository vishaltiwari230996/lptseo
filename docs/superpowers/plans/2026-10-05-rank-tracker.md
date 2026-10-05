# Rank Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the SEO console a Rank tracker panel that sweeps a 200-query pool against live India-localised Google SERPs every two hours, keeps compact per-query rank history for us and our tracked competitors, and ranks the resulting gaps into a worklist with on-demand explanations.

**Architecture:** One new backend module (`rank_tracker.py`) owns pool building, the sweep, storage, rollup and scoring; a second small module (`rank_gap.py`) owns the on-click competitor diff. Both persist through the existing `state` / `jobs.save_list` primitives, and the sweep runs on the existing `jobs.start()` background-thread machinery. A new cron route drives it from Cloud Scheduler on `0 */2 * * *`. The frontend gets one new panel component and one new sidebar section.

**Tech Stack:** Python 3 / FastAPI / pydantic / Firestore (via `seo_geo_agent.state`) / pytest on the backend. Next.js / React / TypeScript / Vitest + @testing-library/react on the frontend. Serper.dev for SERPs, Google Search Console for query demand, OpenRouter (via `sources.llm_text`) for the gap narrative.

**Spec:** `docs/superpowers/specs/2026-10-05-rank-tracker-design.md`

## Global Constraints

- Pool cap `MAX_POOL = 200`. Query length cap `MAX_QUERY_LEN = 200` characters. Minimum GSC impressions to enter the pool: `3`.
- Daily Serper ceiling `MAX_SEARCHES_PER_DAY = 3000`, counted per brand per UTC date, updated through `state.mutate()` (never `load` + `save`).
- SERP locale: `SERP_COUNTRY = "in"`, `SERP_LANGUAGE = "en"`, `SERP_RESULTS = 20`, country-level (no city `location`), overridable per brand via an optional `serp_country` brand field.
- History retention: raw points for `RAW_RETENTION_DAYS = 7`; daily rollups for `DAILY_RETENTION_DAYS = 180`.
- Competitor series are stored only for `brand["competitors"]` (max 8). Full SERP detail lives only in `rank-latest`.
- Every document written through `jobs.save_list()` / `jobs.load_list()` or `state.save()`. Doc ids use `-` separators only (`state.py` maps them 1:1 to filenames offline).
- A missing or failing optional source degrades with a note. It never raises out of a sweep, a pool build, or a route. The only exception is `brand_rank_available() == False`, which is reported as a blocked sweep, not an empty one.
- Every new brand route follows the router's existing conventions: `_brand_or_404`, `Depends(get_current_user)` (`require_creator` for routes that spend Serper credits), and an `Activity` from `trail.records(...)` with `_for(act, brand)`.
- The cron route mirrors `cron_run`: 503 when `SEO_CRON_KEY` is unset, 403 on `hmac.compare_digest` mismatch, 200 all-ok / 207 partial / 502 all-failed.
- Backend tests live in `backend/agents/SEO GEO agent/seo_geo_agent/tests/`, run with `cd backend && python -m pytest`, and inherit the autouse `_isolated_state` fixture (offline, tmp state dir, no Serper key).
- Frontend component tests start with a `// @vitest-environment jsdom` pragma and call `afterEach(cleanup)` explicitly — this project does not run Vitest with `globals: true`.

## Review Focus

- **Substring domain matching gives false positives.** The existing `rank_snapshot` uses `brand["domain"] in r["link"]`, so `lawpreptutorial.com` matches `https://fake-lawpreptutorial.com.spam.io/x` and records a phantom rank. Tested in Task 5.
- **A SERP with zero organic results must not be recorded as a crash to "unranked".** Serper returning an empty `organic` list (rate limit, captcha, outage) across a whole sweep would write a mass rank-loss into history and set the worklist on fire. Expected: such a run is recorded as `error`, not as `position: null`. Tested in Task 5.
- **A sweep that crosses UTC midnight must not double-charge or skip the rollup.** The budget key and the "first sweep of the day" check are both date-derived. Expected: charging uses the date at charge time; the rollup fires on the first sweep whose date differs from the last rollup's. Tested in Tasks 2 and 4.
- **Queries differing only by case, surrounding whitespace, or repeated inner spaces are one query.** GSC, custom entries and harvested PAA will all produce near-duplicates; without normalisation the 200-query cap fills with the same question four times. Tested in Task 3.
- **A query that leaves the pool must not lose or corrupt its history.** Expected: marked `active: false`, skipped by the sweep, history retained and still readable by the history route. Tested in Tasks 3 and 6.

---

## File Structure

**Create**
- `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py` — pool, budget, sweep, storage, rollup, worklist
- `backend/agents/SEO GEO agent/seo_geo_agent/rank_gap.py` — competitor gap diff + narrative
- `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py`
- `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_gap.py`
- `frontend/components/console/seo/ranktracker.tsx`
- `frontend/components/console/seo/ranktracker.test.tsx`

**Modify**
- `backend/agents/SEO GEO agent/seo_geo_agent/sources.py` — locale + depth params on `brand_rank_search`
- `backend/agents/SEO GEO agent/seo_geo_agent/competitors.py` — `rank_snapshot` becomes a shim
- `backend/app/routers/seo_geo.py` — six new routes; remove the `rank_snapshot` call from `cron_run`
- `frontend/lib/api.ts` — types + client functions
- `frontend/components/console/seo/SeoAgent.tsx` — one sidebar entry, one panel mount
- `frontend/app/seo.css` — panel styles
- `backend/.env.example`, `README.md` — new env vars and the scheduler job

---

## Task 1: SERP locale and depth

The spec calls this a prerequisite: until it lands, every rank number is a US SERP.

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/sources.py:393-438`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sources.brand_rank_search(query, client=None, *, gl="in", hl="en", num=20) -> dict` with the unchanged return shape `{"organic": [{"link","title","position"}], "related": [str], "paa": [str], "aio_present": bool}`; module constants `sources.SERP_COUNTRY`, `sources.SERP_LANGUAGE`, `sources.SERP_RESULTS`.

- [ ] **Step 1: Write the failing test**

Append to `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py`:

```python
def test_brand_rank_search_sends_india_locale_and_depth(monkeypatch):
    """A US SERP is the wrong instrument for a Jodhpur CLAT brand."""
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")
    sent = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"organic": []}

    class FakeClient:
        def post(self, url, json=None, headers=None):
            sent.update(json)
            return FakeResponse()

    sources.brand_rank_search("clat coaching", client=FakeClient())

    assert sent["gl"] == "in"
    assert sent["hl"] == "en"
    assert sent["num"] == 20
    assert sent["q"] == "clat coaching"


def test_brand_rank_search_locale_is_overridable(monkeypatch):
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")
    sent = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"organic": []}

    class FakeClient:
        def post(self, url, json=None, headers=None):
            sent.update(json)
            return FakeResponse()

    sources.brand_rank_search("clat", client=FakeClient(), gl="us", num=10)
    assert sent["gl"] == "us"
    assert sent["num"] == 10
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py" -k locale -v
```

Expected: FAIL — `KeyError: 'gl'`.

- [ ] **Step 3: Implement**

In `sources.py`, next to `REAL_SERPER_ENDPOINT` (around line 30), add:

```python
# Rank tracking reads India, in English, at country level. City-level targeting
# would produce a local SERP that misrepresents a nationally-competing brand.
# The DataForSEO path above already pins the same market (location_code 2356).
SERP_COUNTRY = "in"
SERP_LANGUAGE = "en"
#: 20 so positions 11-20 — the striking-distance band the worklist scores on —
#: are visible at all. See the credit-verification step in Task 13 before
#: raising this.
SERP_RESULTS = 20
```

Change the signature and the POST body in `brand_rank_search`:

```python
def brand_rank_search(query: str, client: httpx.Client | None = None, *,
                      gl: str = SERP_COUNTRY, hl: str = SERP_LANGUAGE,
                      num: int = SERP_RESULTS) -> dict:
```

and inside the `cli.post(...)` call replace the json body with:

```python
            json={"q": query, "num": num, "gl": gl, "hl": hl},
```

and the organic slice with `data.get("organic", [])[:num]`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py" -v
```

Expected: PASS, including the pre-existing tests in that file.

- [ ] **Step 5: Run the whole SEO suite to prove nothing regressed**

```bash
cd backend && python -m pytest -m seo -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/sources.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py"
git commit -m "fix(seo): pin rank SERPs to India/en and read 20 results

brand_rank_search sent no gl/hl, so every tracked rank was a US SERP for a
brand that competes in India. num=10 also hid positions 11-20, which is the
band worth working on."
```

---

## Task 2: Module skeleton, constants, and budget guard

**Files:**
- Create: `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py`
- Create: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py`

**Interfaces:**
- Consumes: `state.load/save/mutate`, `sources.SERP_RESULTS`.
- Produces: constants `MAX_POOL=200`, `MAX_QUERY_LEN=200`, `MIN_GSC_IMPRESSIONS=3`, `MAX_SEARCHES_PER_DAY=3000`, `RAW_RETENTION_DAYS=7`, `DAILY_RETENTION_DAYS=180`, `HARVEST_KEEP=2000`, `JOB_KIND="rank-sweep"`; `budget_status(brand_id, today=None) -> dict`; `charge(brand_id, n=1, today=None) -> bool`; `enabled(brand) -> bool`; `_norm(query) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py`:

```python
"""Rank tracker — pool building, budget guard, sweep, history, worklist."""
from __future__ import annotations

from datetime import date

from seo_geo_agent import rank_tracker as rt


def _brand(**over) -> dict:
    brand = {"id": "b1", "name": "Law Prep Tutorial", "domain": "lawpreptutorial.com",
             "seeds": ["clat coaching"], "competitors": ["rival.com"]}
    brand.update(over)
    return brand


def test_budget_starts_empty_and_reports_the_cap():
    status = rt.budget_status("b1", today=date(2026, 10, 5))
    assert status == {"date": "2026-10-05", "searches": 0,
                      "cap": rt.MAX_SEARCHES_PER_DAY,
                      "remaining": rt.MAX_SEARCHES_PER_DAY}


def test_charge_accumulates_within_the_day():
    rt.charge("b1", 10, today=date(2026, 10, 5))
    rt.charge("b1", 5, today=date(2026, 10, 5))
    assert rt.budget_status("b1", today=date(2026, 10, 5))["searches"] == 15


def test_charge_refuses_past_the_cap_and_does_not_partially_spend():
    rt.charge("b1", rt.MAX_SEARCHES_PER_DAY, today=date(2026, 10, 5))
    assert rt.charge("b1", 1, today=date(2026, 10, 5)) is False
    assert rt.budget_status("b1", today=date(2026, 10, 5))["searches"] == rt.MAX_SEARCHES_PER_DAY


def test_budget_resets_on_a_new_date():
    """A sweep that crosses midnight charges against the date at charge time."""
    rt.charge("b1", 2900, today=date(2026, 10, 5))
    assert rt.charge("b1", 1, today=date(2026, 10, 6)) is True
    assert rt.budget_status("b1", today=date(2026, 10, 6))["searches"] == 1


def test_enabled_defaults_true_and_honours_the_brand_flag_and_env(monkeypatch):
    assert rt.enabled(_brand()) is True
    assert rt.enabled(_brand(rank_tracking_enabled=False)) is False
    monkeypatch.setenv("SEO_RANK_SWEEP_DISABLED", "1")
    assert rt.enabled(_brand()) is False


def test_norm_collapses_case_and_whitespace():
    assert rt._norm("  CLAT   Coaching  ") == "clat coaching"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seo_geo_agent.rank_tracker'`.

- [ ] **Step 3: Implement**

Create `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py"
git commit -m "feat(seo): rank_tracker module skeleton with transactional budget guard"
```

---

## Task 3: Query pool builder

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py`

**Interfaces:**
- Consumes: `_norm`, `MAX_POOL`, `MAX_QUERY_LEN`, `MIN_GSC_IMPRESSIONS`, `HARVEST_KEEP`, `POOL_DOC`, `HARVEST_DOC`; `competitors.list_custom_queries(brand_id)`, `competitors.tracked_keywords(brand)`.
- Produces: `build_pool(brand, rows_fn=None) -> dict`; `latest_pool(brand_id) -> dict | None`; `active_queries(brand_id) -> list[str]`; `record_harvest(brand_id, queries) -> None`. Pool doc shape: `{"queries": [{"query","source","impressions","added_at","active"}], "built_at", "sources_used", "notes"}`.

`rows_fn` matches the deep audit's convention (`seo_deep.run(brand, progress, rows_fn=_rows_28d)`): the router owns the GSC property/date logic and passes it in, so this module stays testable with no Google client.

- [ ] **Step 1: Write the failing tests**

Append to `test_rank_tracker.py`:

```python
def _rows(*pairs):
    """Minimal GSC rows: (query, impressions)."""
    class Row:
        def __init__(self, query, impressions):
            self.query, self.impressions = query, impressions
            self.page, self.clicks, self.ctr, self.position = "", 0, 0.0, 0.0
    return [Row(q, i) for q, i in pairs]


def test_pool_puts_custom_queries_first_and_keeps_them_past_the_cap(monkeypatch):
    from seo_geo_agent import competitors
    custom = [f"custom query {n}" for n in range(5)]
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: custom)
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(*[(f"gsc query {n}", 100) for n in range(rt.MAX_POOL)])

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    queries = [q["query"] for q in doc["queries"]]
    assert len(queries) == rt.MAX_POOL
    assert queries[:5] == custom          # custom survives the cap
    assert doc["queries"][0]["source"] == "custom"


def test_pool_sorts_gsc_by_impressions_and_drops_noise(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(("low demand", 5), ("high demand", 900), ("noise", 1))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert [q["query"] for q in doc["queries"]] == ["high demand", "low demand"]
    assert doc["queries"][0]["impressions"] == 900


def test_pool_sums_impressions_for_one_query_across_pages(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(("clat coaching", 60), ("clat coaching", 40), ("other", 80))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert [q["query"] for q in doc["queries"]] == ["clat coaching", "other"]
    assert doc["queries"][0]["impressions"] == 100


def test_pool_dedups_on_case_and_whitespace(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["CLAT Coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: ["clat   coaching"])
    gsc = _rows(("  clat coaching  ", 500))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert len(doc["queries"]) == 1
    assert doc["queries"][0]["source"] == "custom"


def test_pool_degrades_with_a_note_when_search_console_is_missing(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: ["clat coaching"])

    doc = rt.build_pool(_brand(), rows_fn=lambda b: ([], ["Search Console: no access"]))

    assert [q["query"] for q in doc["queries"]] == ["clat coaching"]
    assert any("Search Console" in n for n in doc["notes"])
    assert "gsc" not in doc["sources_used"]


def test_harvested_queries_enter_the_pool_most_frequent_first(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.record_harvest("b1", ["rare question"])
    rt.record_harvest("b1", ["common question", "common question"])
    rt.record_harvest("b1", ["common question"])

    doc = rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    assert [q["query"] for q in doc["queries"]] == ["common question", "rare question"]
    assert doc["queries"][0]["source"] == "harvest"


def test_pool_drops_over_long_queries(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(("x" * (rt.MAX_QUERY_LEN + 1), 900), ("fine", 10))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert [q["query"] for q in doc["queries"]] == ["fine"]


def test_rebuild_marks_dropped_queries_inactive_instead_of_deleting_them(monkeypatch):
    """History stays readable for a query that left the pool."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["going away"])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["still here"])
    doc = rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    by_query = {q["query"]: q for q in doc["queries"]}
    assert by_query["going away"]["active"] is False
    assert by_query["still here"]["active"] is True
    assert rt.active_queries("b1") == ["still here"]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -k pool -v
```

Expected: FAIL — `AttributeError: module 'seo_geo_agent.rank_tracker' has no attribute 'build_pool'`.

- [ ] **Step 3: Implement**

Append to `rank_tracker.py`:

```python
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

    for q in competitors.list_custom_queries(brand["id"]):
        candidates.append((q, "custom", 0))
    if candidates:
        sources_used.append("custom")

    rows, gsc_notes = rows_fn(brand) if rows_fn else ([], [])
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
        sources_used.append("gsc")

    harvested = _harvest_ranked(brand["id"])
    for q in harvested:
        candidates.append((q, "harvest", 0))
    if harvested:
        sources_used.append("harvest")

    seeds = competitors.tracked_keywords(brand)
    for q in seeds:
        candidates.append((q, "seed", 0))
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
            "impressions": impressions,
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -v
```

Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py"
git commit -m "feat(seo): rank tracker query pool — GSC, custom, harvested PAA, seeds"
```

---

## Task 4: History storage and daily rollup

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py`

**Interfaces:**
- Consumes: `jobs.save_list`, `jobs.load_list`, `RAW_RETENTION_DAYS`, `DAILY_RETENTION_DAYS`, `HISTORY_PREFIX`.
- Produces: `append_history(brand_id, results, rivals, now=None) -> None`; `history_for(brand_id, query) -> dict | None`; `all_history(brand_id) -> list[dict]`; `rollup(brand_id, today=None) -> int`; `_epoch_hours(dt) -> int`. History row shape: `{"query": str, "raw": [[int, int|None]], "daily": [[str, {"best","worst","last"}]], "rivals": {domain: {"raw": [...], "daily": [...]}}}`. `results` is the list produced by Task 5's sweep: `[{"query","position","url","top","error"}]`.

- [ ] **Step 1: Write the failing tests**

Append to `test_rank_tracker.py`:

```python
from datetime import datetime, timezone


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def _result(query: str, position, top=None) -> dict:
    return {"query": query, "position": position, "url": "", "top": top or [], "error": None}


def test_append_history_records_one_raw_point_per_run():
    rt.append_history("b1", [_result("clat coaching", 9)], ["rival.com"], now=_at(5, 9))
    rt.append_history("b1", [_result("clat coaching", 7)], ["rival.com"], now=_at(5, 11))

    row = rt.history_for("b1", "clat coaching")
    assert [p[1] for p in row["raw"]] == [9, 7]
    assert row["raw"][0][0] < row["raw"][1][0]


def test_append_history_tracks_rival_positions_only_for_tracked_competitors():
    top = [{"position": 2, "domain": "rival.com", "url": "https://rival.com/a", "title": ""},
           {"position": 3, "domain": "driveby.com", "url": "https://driveby.com/b", "title": ""}]
    rt.append_history("b1", [_result("clat coaching", 9, top)], ["rival.com"], now=_at(5))

    row = rt.history_for("b1", "clat coaching")
    assert list(row["rivals"]) == ["rival.com"]
    assert row["rivals"]["rival.com"]["raw"][0][1] == 2


def test_append_history_skips_errored_results():
    """An empty or failed SERP is missing data, not a rank of None."""
    rt.append_history("b1", [{"query": "q", "position": None, "url": "", "top": [],
                              "error": "serper 429"}], [], now=_at(5))
    assert rt.history_for("b1", "q") is None


def test_rollup_collapses_raw_points_older_than_the_window_into_daily_triples():
    for hour in (8, 12, 16):
        rt.append_history("b1", [_result("q", {8: 11, 12: 7, 16: 9}[hour])], [], now=_at(1, hour))
    rt.append_history("b1", [_result("q", 5)], [], now=_at(20))  # inside the raw window

    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    assert row["daily"] == [["2026-10-01", {"best": 7, "worst": 11, "last": 9}]]
    assert [p[1] for p in row["raw"]] == [5]   # only the recent point survives


def test_rollup_is_idempotent_within_a_day():
    rt.append_history("b1", [_result("q", 4)], [], now=_at(1))
    rt.rollup("b1", today=date(2026, 10, 20))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    assert len(row["daily"]) == 1


def test_rollup_trims_dailies_past_the_retention_window():
    rt.append_history("b1", [_result("q", 4)], [], now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    rt.rollup("b1", today=date(2026, 10, 20))     # 2026-01-01 is 292 days back
    assert rt.history_for("b1", "q")["daily"] == []


def test_history_survives_a_query_leaving_the_pool():
    rt.append_history("b1", [_result("retired query", 12)], [], now=_at(5))
    assert rt.history_for("b1", "retired query")["raw"][0][1] == 12
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -k history -v
```

Expected: FAIL — no attribute `append_history`.

- [ ] **Step 3: Implement**

Append to `rank_tracker.py`:

```python
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

    buckets: dict[str, list[int]] = {}
    for hours, position in stale:
        if position is None:
            continue
        buckets.setdefault(_hours_to_date(hours).isoformat(), []).append(position)

    daily = {day: triple for day, triple in series.get("daily", [])}
    for day, positions in buckets.items():
        existing = daily.get(day)
        best = min(positions + ([existing["best"]] if existing else []))
        worst = max(positions + ([existing["worst"]] if existing else []))
        daily[day] = {"best": best, "worst": worst, "last": positions[-1]}

    kept = sorted((d, t) for d, t in daily.items() if date.fromisoformat(d) >= oldest_day)
    return {"raw": keep_raw, "daily": [[d, t] for d, t in kept]}


def rollup(brand_id: str, today: date | None = None) -> int:
    """Fold the raw tail into dailies and trim both windows. Returns rows touched.

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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -v
```

Expected: PASS (21 tests).

- [ ] **Step 5: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py"
git commit -m "feat(seo): compact rank history with 7-day raw window and 180-day daily rollup"
```

---

## Task 5: The sweep

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py`

**Interfaces:**
- Consumes: `active_queries`, `charge`, `budget_status`, `append_history`, `rollup`, `record_harvest`, `enabled`, `sources.brand_rank_search`, `sources.brand_rank_available`, `sources.domain_of`, `jobs.Progress`.
- Produces: `sweep(brand, progress=None, search=None, now=None) -> dict`; `latest_rows(brand_id) -> list[dict]`; `latest_meta(brand_id) -> dict | None`; `_ours(link, domain) -> bool`. Result row shape: `{"query","position","url","checked_at","top":[{"position","domain","url","title"}],"error"}`. Sweep return: `{"checked", "ranked", "errors", "blocked", "at", "notes"}`.

- [ ] **Step 1: Write the failing tests**

Append to `test_rank_tracker.py`:

```python
def _serp(*entries):
    """entries: (position, url)."""
    return {"organic": [{"position": p, "link": u, "title": f"t{p}"} for p, u in entries],
            "related": [], "paa": [], "aio_present": False}


def test_sweep_records_our_position_and_the_full_top_list(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        return _serp((1, "https://rival.com/a"), (2, "https://lawpreptutorial.com/clat"))

    out = rt.sweep(_brand(), search=search)

    assert out["checked"] == 1 and out["ranked"] == 1 and out["errors"] == 0
    row = rt.latest_rows("b1")[0]
    assert row["position"] == 2
    assert row["url"] == "https://lawpreptutorial.com/clat"
    assert [e["domain"] for e in row["top"]] == ["rival.com", "lawpreptutorial.com"]


def test_sweep_does_not_match_a_lookalike_domain(monkeypatch):
    """`domain in link` matches fake-lawpreptutorial.com.spam.io — a phantom rank."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        return _serp((1, "https://fake-lawpreptutorial.com.spam.io/x"),
                     (2, "https://www.lawpreptutorial.com/clat"))

    rt.sweep(_brand(), search=search)

    row = rt.latest_rows("b1")[0]
    assert row["position"] == 2           # the www. form of our real domain
    assert row["url"].endswith("/clat")


def test_sweep_continues_past_a_query_that_raises(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["good", "bad", "also good"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        if query == "bad":
            raise RuntimeError("serper 429")
        return _serp((3, "https://lawpreptutorial.com/x"))

    out = rt.sweep(_brand(), search=search)

    assert out["checked"] == 3 and out["ranked"] == 2 and out["errors"] == 1
    bad = next(r for r in rt.latest_rows("b1") if r["query"] == "bad")
    assert "429" in bad["error"] and bad["position"] is None


def test_sweep_treats_an_empty_serp_as_an_error_not_an_unranked_result(monkeypatch):
    """A rate-limited provider returns nothing for everything; that is missing
    data, and must not be written into history as a site-wide collapse."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    out = rt.sweep(_brand(), search=lambda query, **kw: _serp())

    assert out["errors"] == 1
    assert rt.latest_rows("b1")[0]["error"] == "empty SERP"
    assert rt.history_for("b1", "q") is None


def test_sweep_refuses_when_the_daily_budget_is_exhausted(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))
    rt.charge("b1", rt.MAX_SEARCHES_PER_DAY)

    calls = []
    out = rt.sweep(_brand(), search=lambda q, **kw: calls.append(q) or _serp())

    assert out["blocked"] == "budget"
    assert calls == []


def test_sweep_refuses_when_rank_tracking_is_disabled_for_the_brand(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    calls = []
    out = rt.sweep(_brand(rank_tracking_enabled=False),
                   search=lambda q, **kw: calls.append(q) or _serp())

    assert out["blocked"] == "disabled" and calls == []


def test_sweep_charges_one_credit_per_query(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["a", "b", "c"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    rt.sweep(_brand(), search=lambda q, **kw: _serp((1, "https://x.com/")))

    assert rt.budget_status("b1")["searches"] == 3


def test_sweep_harvests_related_and_paa_for_the_next_rebuild(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        serp = _serp((1, "https://x.com/"))
        serp["related"] = ["clat syllabus"]
        serp["paa"] = ["how hard is clat"]
        return serp

    rt.sweep(_brand(), search=search)

    assert set(rt._harvest_ranked("b1")) == {"clat syllabus", "how hard is clat"}


def test_sweep_skips_inactive_queries(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["old"])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["new"])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    seen = []
    rt.sweep(_brand(), search=lambda q, **kw: seen.append(q) or _serp((1, "https://x.com/")))

    assert seen == ["new"]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -k sweep -v
```

Expected: FAIL — no attribute `sweep`.

- [ ] **Step 3: Implement**

Append to `rank_tracker.py`:

```python
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
        search = sources.brand_rank_search

    queries = active_queries(brand_id)
    if progress:
        progress.phase(f"checking {len(queries)} queries", total=len(queries))

    rivals = [d.lower() for d in (brand.get("competitors") or [])][:8]
    results: list[dict] = []
    harvested: list[str] = []
    ranked = errors = 0

    for query in queries:
        if not charge(brand_id, 1, today=moment.date()):
            notes.append(f"Daily search budget reached — stopped after {len(results)} queries")
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
            ours = next((e for e in organic if _ours(e.get("link", ""), brand["domain"])), None)
            if ours:
                row["position"] = ours.get("position")
                row["url"] = ours.get("link", "")
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

    last_rollup = (state.load(POOL_DOC.format(brand_id)) or {}).get("rolled_up_on")
    if last_rollup != moment.date().isoformat():
        rollup(brand_id, today=moment.date())
        pool = latest_pool(brand_id) or {}
        pool["rolled_up_on"] = moment.date().isoformat()
        state.save(POOL_DOC.format(brand_id), pool)

    return {"checked": len(results), "ranked": ranked, "errors": errors, "blocked": None,
            "at": moment.isoformat(timespec="seconds"), "notes": notes}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -v
```

Expected: PASS (30 tests).

- [ ] **Step 5: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py"
git commit -m "feat(seo): rank sweep with budget guard, host-exact domain match, per-query fault isolation"
```

---

## Task 6: Worklist scoring

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py`

**Interfaces:**
- Consumes: `latest_rows`, `latest_pool`, `history_for`, `_norm`.
- Produces: `worklist(brand, limit=10) -> list[dict]`; `_position_band(position) -> float`; `delta(brand_id, query, hours) -> int | None`. Worklist row shape: `{"query","position","impressions","leader","leader_position","tracked_rival","delta_7d","score","reason"}`, highest score first.

- [ ] **Step 1: Write the failing tests**

Append to `test_rank_tracker.py`:

```python
def test_position_band_peaks_in_striking_distance():
    assert rt._position_band(8) > rt._position_band(2)     # already won: low value
    assert rt._position_band(8) > rt._position_band(60)    # unwinnable: low value
    assert rt._position_band(None) < rt._position_band(8)  # unranked: floor, not zero
    assert rt._position_band(None) > 0


def _seed_latest(rows, rivals=("rival.com",)):
    from seo_geo_agent import jobs as j
    j.save_list(rt.LATEST_PREFIX.format("b1"), rows,
                meta={"at": "2026-10-05T09:00:00+00:00", "ranked": len(rows),
                      "errors": 0, "rivals": list(rivals)})


def test_worklist_ranks_high_demand_striking_distance_queries_first(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("winnable", 5000), ("obscure", 5),
                                                     ("already won", 9000)), []))
    _seed_latest([
        {"query": "winnable", "position": 8, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "https://rival.com/a", "title": ""}]},
        {"query": "obscure", "position": 7, "url": "", "error": None,
         "top": [{"position": 1, "domain": "other.com", "url": "https://other.com/a", "title": ""}]},
        {"query": "already won", "position": 1, "url": "", "error": None, "top": []},
    ])

    rows = rt.worklist(_brand())

    assert [r["query"] for r in rows][:1] == ["winnable"]
    assert rows[0]["leader"] == "rival.com" and rows[0]["tracked_rival"] is True
    assert "already won" not in [r["query"] for r in rows[:2]]


def test_worklist_excludes_errored_rows():
    _seed_latest([{"query": "broken", "position": None, "url": "",
                   "error": "serper 429", "top": []}])
    assert rt.worklist(_brand()) == []


def test_worklist_promotes_a_query_that_lost_ground_this_week():
    _seed_latest([
        {"query": "slipping", "position": 12, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
        {"query": "steady", "position": 12, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
    ])
    rt.append_history("b1", [_result("slipping", 4), _result("steady", 12)], [],
                      now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    rt.append_history("b1", [_result("slipping", 12), _result("steady", 12)], [],
                      now=datetime(2026, 10, 5, tzinfo=timezone.utc))

    rows = rt.worklist(_brand())

    assert rows[0]["query"] == "slipping"
    assert rows[0]["delta_7d"] == -8      # negative = we fell


def test_worklist_respects_the_limit():
    _seed_latest([
        {"query": f"q{n}", "position": 9, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]}
        for n in range(30)
    ])
    assert len(rt.worklist(_brand(), limit=5)) == 5
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -k worklist -v
```

Expected: FAIL — no attribute `worklist`.

- [ ] **Step 3: Implement**

Append to `rank_tracker.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py" -v
```

Expected: PASS (35 tests).

- [ ] **Step 5: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_tracker.py"
git commit -m "feat(seo): striking-distance worklist scoring"
```

---

## Task 7: Retire the second rank engine

Two sweeps over overlapping query sets would double-bill Serper every cycle.

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/competitors.py:92-120`
- Modify: `backend/app/routers/seo_geo.py:845-855`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_lab.py`

**Interfaces:**
- Consumes: `rank_tracker.latest_rows`, `rank_tracker.latest_meta`.
- Produces: `competitors.rank_snapshot(brand, search=None) -> dict` with its existing return shape `{"snapshots": [{"at", "ranks": {query: {"position", "top"}}}], "suggested_competitors": [str]}`, now derived from rank_tracker's latest run and making zero Serper calls.

- [ ] **Step 1: Write the failing test**

Append to `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_lab.py`:

```python
def test_rank_snapshot_reads_rank_tracker_and_makes_no_serper_calls(monkeypatch):
    """One rank engine. A second live sweep would double the Serper bill."""
    from seo_geo_agent import competitors, jobs, rank_tracker

    jobs.save_list(rank_tracker.LATEST_PREFIX.format("b1"), [
        {"query": "clat coaching", "position": 4, "url": "https://lawpreptutorial.com/c",
         "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "https://rival.com/a", "title": ""},
                 {"position": 4, "domain": "lawpreptutorial.com", "url": "", "title": ""}]},
    ], meta={"at": "2026-10-05T09:00:00+00:00", "ranked": 1, "errors": 0, "rivals": []})

    def boom(*a, **kw):
        raise AssertionError("rank_snapshot must not call a SERP provider")

    monkeypatch.setattr(competitors.sources, "brand_rank_search", boom)
    monkeypatch.setattr(competitors.sources, "serper_search", boom)

    doc = competitors.rank_snapshot({"id": "b1", "domain": "lawpreptutorial.com"})

    assert doc["snapshots"][-1]["ranks"]["clat coaching"]["position"] == 4
    assert "rival.com" in doc["suggested_competitors"]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_seo_lab.py" -k no_serper_calls -v
```

Expected: FAIL — `AssertionError: rank_snapshot must not call a SERP provider`.

- [ ] **Step 3: Implement**

Replace the body of `rank_snapshot` in `competitors.py` with:

```python
def rank_snapshot(brand: dict, search=None) -> dict:
    """Project rank_tracker's latest run into this module's snapshot shape.

    This used to run its own capped Serper sweep. ``rank_tracker`` now sweeps a
    far larger pool on a schedule, and two engines over overlapping queries
    would bill Serper twice for the same answer — so this reads that run instead
    of taking one of its own. The stored shape is unchanged, so the Competitors
    panel, ``rank_shifts`` and ``_domain_stats`` all keep working.
    """
    from . import rank_tracker

    rows = rank_tracker.latest_rows(brand["id"])
    meta = rank_tracker.latest_meta(brand["id"]) or {}
    if not rows:
        return state.load(f"ranks-{brand['id']}") or {"snapshots": [], "suggested_competitors": []}

    ranks: dict[str, dict] = {}
    seen_domains: dict[str, int] = {}
    for row in rows:
        if row.get("error"):
            continue
        top = [entry["domain"] for entry in row.get("top") or []]
        for d in top:
            if d and d != brand["domain"]:
                seen_domains[d] = seen_domains.get(d, 0) + 1
        ranks[row["query"]] = {"position": row.get("position"), "top": top[:5]}

    at = (meta.get("at") or date.today().isoformat())[:10]
    doc = state.load(f"ranks-{brand['id']}") or {"snapshots": []}
    snapshots = [s for s in doc["snapshots"] if s.get("at") != at]
    doc["snapshots"] = (snapshots + [{"at": at, "ranks": ranks}])[-MAX_SNAPSHOTS:]
    doc["suggested_competitors"] = [
        d for d, _ in sorted(seen_domains.items(), key=lambda kv: -kv[1])[:8]
    ]
    state.save(f"ranks-{brand['id']}", doc)
    return doc
```

In `backend/app/routers/seo_geo.py`, inside `cron_run`, delete the whole `try`/`except` block that calls `seo_competitors.rank_snapshot(brand)` and sets `entry["ranks"]` (lines ~848-853). The rank sweep is now its own scheduled job.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest -m seo -q
```

Expected: PASS. If `test_rank_snapshot_uses_brand_rank_search_not_serper_search` (test_seo_lab.py:185) now fails, delete it — it pinned the behaviour this task deliberately replaces, and the new test above supersedes it.

- [ ] **Step 5: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/competitors.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_lab.py" backend/app/routers/seo_geo.py
git commit -m "refactor(seo): one rank engine — rank_snapshot reads rank_tracker instead of sweeping

Two independent Serper sweeps over overlapping query sets billed twice for
the same answer every cycle."
```

---

## Task 8: Gap card

**Files:**
- Create: `backend/agents/SEO GEO agent/seo_geo_agent/rank_gap.py`
- Create: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_gap.py`

**Interfaces:**
- Consumes: `rank_tracker.latest_rows`, `sources.fetch_page`, `sources.llm_text`, `state.load/save`.
- Produces: `rank_gap.explain(brand, query, fetch=None, llm=None, now=None) -> dict` returning `{"query","our_url","their_url","their_domain","their_position","our_position","metrics":{"words":{"ours","theirs"},"headings":{"ours","theirs"},"schema":{"ours","theirs"}},"narrative","cached","at"}`.

`sources.fetch_page(url)` returns a `PageFacts`; read its definition at `sources.py:645` before writing the adapter and use the attributes it actually exposes.

- [ ] **Step 1: Write the failing tests**

Create `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_gap.py`:

```python
"""Gap card — the diff between our ranking page and the one beating it."""
from __future__ import annotations

from datetime import datetime, timezone

from seo_geo_agent import jobs, rank_gap, rank_tracker


def _brand() -> dict:
    return {"id": "b1", "name": "Law Prep Tutorial", "domain": "lawpreptutorial.com"}


def _seed(position=9):
    jobs.save_list(rank_tracker.LATEST_PREFIX.format("b1"), [
        {"query": "clat coaching", "position": position,
         "url": "https://lawpreptutorial.com/clat", "error": None,
         "top": [{"position": 1, "domain": "rival.com",
                  "url": "https://rival.com/clat", "title": "Rival"}]},
    ], meta={"at": "2026-10-05T09:00:00+00:00", "ranked": 1, "errors": 0, "rivals": []})


class FakePage:
    def __init__(self, words, headings, schema):
        self.text = " ".join(["word"] * words)
        self.headings = headings
        self.schema_types = schema


def _fetch(pages):
    return lambda url: pages[url]


def test_explain_diffs_our_page_against_the_leader():
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(400, ["What is CLAT"], []),
        "https://rival.com/clat": FakePage(2200, ["What is CLAT", "Syllabus", "Fees"], ["FAQPage"]),
    }
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "They cover fees and syllabus; we do not.")

    assert doc["their_domain"] == "rival.com"
    assert doc["their_position"] == 1 and doc["our_position"] == 9
    assert doc["metrics"]["words"] == {"ours": 400, "theirs": 2200}
    assert doc["metrics"]["headings"]["theirs"] == 3
    assert doc["metrics"]["schema"]["theirs"] == ["FAQPage"]
    assert "fees" in doc["narrative"]
    assert doc["cached"] is False


def test_explain_caches_for_a_day_and_does_not_call_the_llm_twice():
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(400, [], []),
        "https://rival.com/clat": FakePage(2200, [], []),
    }
    calls = []

    def llm(system, prompt):
        calls.append(prompt)
        return "narrative"

    now = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)
    rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages), llm=llm, now=now)
    again = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages), llm=llm, now=now)

    assert len(calls) == 1
    assert again["cached"] is True


def test_explain_says_so_when_we_do_not_rank_at_all():
    _seed(position=None)
    pages = {"https://rival.com/clat": FakePage(2200, [], [])}
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "narrative")

    assert doc["our_url"] == ""
    assert doc["our_position"] is None
    assert doc["metrics"]["words"]["ours"] is None


def test_explain_404s_cleanly_for_a_query_that_was_never_swept():
    _seed()
    assert rank_gap.explain(_brand(), "never checked", fetch=lambda u: None,
                            llm=lambda s, p: "x") is None


def test_explain_degrades_when_a_page_cannot_be_fetched():
    _seed()

    def fetch(url):
        raise RuntimeError("403 Forbidden")

    doc = rank_gap.explain(_brand(), "clat coaching", fetch=fetch, llm=lambda s, p: "n")
    assert doc["narrative"]
    assert any("403" in n for n in doc["notes"])
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_gap.py" -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seo_geo_agent.rank_gap'`.

- [ ] **Step 3: Implement**

Create `backend/agents/SEO GEO agent/seo_geo_agent/rank_gap.py`:

```python
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
    "lower-ranking one does not, and what to change. Be concrete. Do not pad."
)


def _key(query: str, url: str) -> str:
    return hashlib.sha1(f"{query}|{url}".encode()).hexdigest()[:16]


def _facts(page) -> dict:
    headings = list(getattr(page, "headings", []) or [])
    return {
        "words": len((getattr(page, "text", "") or "").split()),
        "headings": headings,
        "schema": list(getattr(page, "schema_types", []) or []),
    }


def explain(brand: dict, query: str, fetch=None, llm=None, now=None) -> dict | None:
    """The diff plus one LLM sentence-set. ``None`` when the query was never swept."""
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
            return _facts(fetch(url))
        except Exception as exc:  # noqa: BLE001 — a blocked page is a note, not a 500
            notes.append(f"{url}: {exc}"[:200])
            return None

    ours = facts_for(row.get("url") or "")
    theirs = facts_for(leader["url"])

    prompt = (
        f"Query: {query}\n"
        f"Their page (#{leader['position']}, {leader['domain']}): {leader['url']}\n"
        f"  words={(theirs or {}).get('words')}, headings={(theirs or {}).get('headings')}, "
        f"schema={(theirs or {}).get('schema')}\n"
        f"Our page (#{position}): {row.get('url') or 'we do not rank for this query'}\n"
        f"  words={(ours or {}).get('words')}, headings={(ours or {}).get('headings')}, "
        f"schema={(ours or {}).get('schema')}\n"
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
        },
        "narrative": narrative,
        "notes": notes,
        "at": moment.isoformat(timespec="seconds"),
    }
    state.save(cache_id, doc)
    return {**doc, "cached": False}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_gap.py" -v
```

Expected: PASS (5 tests). If `PageFacts` does not expose `headings` or `schema_types`, adjust `_facts` to the attribute names it does expose and update `FakePage` in the test to match.

- [ ] **Step 5: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/rank_gap.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_gap.py"
git commit -m "feat(seo): gap card — cached per-query diff against the page beating us"
```

---

## Task 9: Routes

**Files:**
- Modify: `backend/app/routers/seo_geo.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_routes.py` (create)

**Interfaces:**
- Consumes: everything produced by Tasks 2-8, plus the router's `_brand_or_404`, `_for`, `_rows_28d`, `trail`, `seo_jobs`.
- Produces: the six routes listed in the spec's section 9.

- [ ] **Step 1: Write the failing tests**

Create `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_routes.py`. Read `test_seo_geo.py:503-540` first — it explains why route tests here bring their own minimal TestClient setup, and this file follows that shape:

```python
"""Rank tracker routes — auth, cron contract, and the panel payload."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import get_current_user, require_creator


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "creator"}
    app.dependency_overrides[require_creator] = lambda: {"id": "u1", "role": "creator"}
    from seo_geo_agent import insights
    monkeypatch.setattr(insights, "list_brands", lambda: [
        {"id": "b1", "name": "Law Prep Tutorial", "domain": "lawpreptutorial.com",
         "seeds": [], "competitors": [], "enabled": True},
    ])
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_overview_route_returns_pool_budget_and_worklist(client):
    body = client.get("/api/seo-geo/rank-tracker/b1").json()
    assert set(body) >= {"rows", "worklist", "pool", "budget", "job", "meta"}
    assert body["budget"]["cap"] == 3000


def test_overview_404s_for_an_unknown_brand(client):
    assert client.get("/api/seo-geo/rank-tracker/nope").status_code == 404


def test_cron_is_503_until_the_key_is_configured(client, monkeypatch):
    monkeypatch.delenv("SEO_CRON_KEY", raising=False)
    assert client.post("/api/seo-geo/rank-tracker/cron").status_code == 503


def test_cron_rejects_a_wrong_key(client, monkeypatch):
    monkeypatch.setenv("SEO_CRON_KEY", "right")
    resp = client.post("/api/seo-geo/rank-tracker/cron", headers={"x-cron-key": "wrong"})
    assert resp.status_code == 403


def test_cron_runs_every_enabled_brand_and_reports_ok(client, monkeypatch):
    monkeypatch.setenv("SEO_CRON_KEY", "right")
    from seo_geo_agent import rank_tracker
    monkeypatch.setattr(rank_tracker, "build_pool", lambda brand, rows_fn=None: {"queries": []})
    monkeypatch.setattr(rank_tracker, "sweep",
                        lambda brand, progress=None, search=None, now=None:
                        {"checked": 3, "ranked": 3, "errors": 0, "blocked": None,
                         "at": "2026-10-05T09:00:00+00:00", "notes": []})

    resp = client.post("/api/seo-geo/rank-tracker/cron", headers={"x-cron-key": "right"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_cron_is_502_when_every_brand_fails(client, monkeypatch):
    monkeypatch.setenv("SEO_CRON_KEY", "right")
    from seo_geo_agent import rank_tracker

    def boom(*a, **kw):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(rank_tracker, "build_pool", boom)
    monkeypatch.setattr(rank_tracker, "sweep", boom)

    resp = client.post("/api/seo-geo/rank-tracker/cron", headers={"x-cron-key": "right"})
    assert resp.status_code == 502


def test_history_route_404s_for_an_unswept_query(client):
    assert client.get("/api/seo-geo/rank-tracker/b1/history?query=nothing").status_code == 404


def test_gap_route_404s_for_an_unswept_query(client):
    resp = client.post("/api/seo-geo/rank-tracker/b1/gap", json={"query": "nothing"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_routes.py" -v
```

Expected: FAIL — 404 on every rank-tracker path (routes not registered).

- [ ] **Step 3: Implement**

In `seo_geo.py`, add to the agent imports:

```python
from seo_geo_agent import rank_gap as seo_rank_gap
from seo_geo_agent import rank_tracker as seo_rank
```

Add after the page-speed section, before the `# ----- cron -----` block:

```python
# --------------------------- rank tracker ---------------------------
# The scheduled scoreboard: a 200-query pool swept every two hours, with
# history, tracked-competitor positions, and a worklist. The sweep is a
# background job - 200 sequential SERP calls take minutes - so POST starts it
# and the panel polls GET for progress, exactly like the deep audit.

def _rank_payload(brand: dict) -> dict:
    brand_id = brand["id"]
    pool = seo_rank.latest_pool(brand_id) or {"queries": [], "notes": [], "sources_used": []}
    return {
        "rows": seo_rank.latest_rows(brand_id),
        "meta": seo_rank.latest_meta(brand_id),
        "worklist": seo_rank.worklist(brand, limit=10),
        "pool": {
            "size": len([q for q in pool["queries"] if q.get("active")]),
            "cap": seo_rank.MAX_POOL,
            "built_at": pool.get("built_at"),
            "sources_used": pool.get("sources_used", []),
            "notes": pool.get("notes", []),
        },
        "budget": seo_rank.budget_status(brand_id),
        "competitors": [d.lower() for d in (brand.get("competitors") or [])][:8],
        "enabled": seo_rank.enabled(brand),
        "job": seo_jobs.status(seo_rank.JOB_KIND, brand_id),
    }


@router.get("/seo-geo/rank-tracker/{brand_id}")
def get_rank_tracker(brand_id: str, user=Depends(get_current_user)):
    return _rank_payload(_brand_or_404(brand_id))


@router.get("/seo-geo/rank-tracker/{brand_id}/history")
def get_rank_history(brand_id: str, query: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    row = seo_rank.history_for(brand_id, query)
    if not row:
        raise HTTPException(status_code=404, detail="That query has no recorded history yet")
    return {"history": row}


@router.post("/seo-geo/rank-tracker/{brand_id}/sweep")
def run_rank_sweep(brand_id: str, user=Depends(require_creator),
                   act: Activity = trail.records("rank_sweep", "Started a rank sweep", unit=JOB)):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    if not seo_state.use_network():
        raise HTTPException(status_code=503, detail="offline mode - set SEO_ALLOW_NETWORK=1")
    act.note(f"Rank sweep for {brand['domain']}")
    job = seo_jobs.start(seo_rank.JOB_KIND, brand_id,
                         lambda progress: seo_rank.sweep(brand, progress))
    return {"job": job}


@router.post("/seo-geo/rank-tracker/{brand_id}/pool/rebuild")
def rebuild_rank_pool(brand_id: str, user=Depends(require_creator),
                      act: Activity = trail.records("rank_pool", "Rebuilt the rank-tracking pool")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    pool = seo_rank.build_pool(brand, rows_fn=_rows_28d)
    active = len([q for q in pool["queries"] if q.get("active")])
    act.note(f"Pool rebuilt: {active} active queries from {', '.join(pool['sources_used']) or 'no source'}")
    return _rank_payload(brand)


@router.post("/seo-geo/rank-tracker/{brand_id}/gap")
def rank_gap_card(brand_id: str, payload: QueryIn, user=Depends(get_current_user),
                  act: Activity = trail.records("rank_gap", "Explained a ranking gap")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    doc = seo_rank_gap.explain(brand, payload.query.strip())
    if not doc:
        raise HTTPException(status_code=404,
                            detail="That query has not been swept, or nothing is ranking above us")
    act.note(f"Gap for “{payload.query.strip()}” vs {doc['their_domain']}")
    return {"gap": doc}


@router.post("/seo-geo/rank-tracker/cron")
def rank_cron(request: Request, response: Response,
              act: Activity = trail.records("rank_cron", "Scheduled rank sweep",
                                            unit=JOB, actor=CRON)):
    """Two-hourly rank sweep across every enabled brand.

    Separate from /seo-geo/cron/run on purpose: that one runs the full brand
    report, which has no business running twelve times a day. Status contract is
    the same, because Cloud Scheduler reads only the status code.
    """
    expected = os.environ.get("SEO_CRON_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="SEO_CRON_KEY not configured")
    if not hmac.compare_digest(request.headers.get("x-cron-key", ""), expected):
        raise HTTPException(status_code=403, detail="Bad cron key")

    results: dict[str, dict] = {}
    today = date.today().isoformat()
    for brand in insights.list_brands():
        if not brand.get("enabled", True):
            continue
        try:
            pool = seo_rank.latest_pool(brand["id"]) or {}
            # The pool is rebuilt once a day; sweeping twelve times against a
            # pool that changes every run would make every trend line a lie.
            if (pool.get("built_at") or "")[:10] != today:
                seo_rank.build_pool(brand, rows_fn=_rows_28d)
            results[brand["id"]] = {"ok": True, **seo_rank.sweep(brand)}
        except Exception as exc:  # noqa: BLE001 — one bad brand must not kill the sweep
            logger.exception("rank sweep failed for %s", brand["id"])
            results[brand["id"]] = {"ok": False, "error": str(exc)}

    ok = sum(1 for r in results.values() if r.get("ok"))
    failed = len(results) - ok
    out = {"brands": results, "ok": ok, "failed": failed, "status": "ok"}
    if results and ok == 0:
        out["status"] = "failed"
        response.status_code = 502
        logger.error("rank sweep FAILED: all %d brands errored", failed)
    elif failed:
        out["status"] = "partial"
        response.status_code = 207
        logger.warning("rank sweep degraded: %d/%d brands failed", failed, len(results))
    act.note(f"Rank sweep across {len(results)} brands — {ok} ok, {failed} failed",
             status=str(out["status"]))
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python -m pytest "agents/SEO GEO agent/seo_geo_agent/tests/test_rank_routes.py" -v
```

Expected: PASS (8 tests).

- [ ] **Step 5: Run the whole backend suite**

```bash
cd backend && python -m pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/seo_geo.py "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_rank_routes.py"
git commit -m "feat(seo): rank tracker routes and two-hourly cron entry point"
```

---

## Task 10: API client

**Files:**
- Modify: `frontend/lib/api.ts`
- Test: `frontend/lib/api.ranktracker.test.ts` (create)

**Interfaces:**
- Consumes: the existing `getJson` / `postJson` helpers and the `DeepJob` and `RequestOptions` types already in `api.ts`.
- Produces: types `RankRow`, `RankWorklistRow`, `RankPool`, `RankBudget`, `RankMeta`, `RankTrackerDoc`, `RankHistory`, `RankGap`; functions `seoRankTracker`, `seoRankHistory`, `seoRankSweep`, `seoRankPoolRebuild`, `seoRankGap`.

- [ ] **Step 1: Write the failing test**

Create `frontend/lib/api.ranktracker.test.ts`, following the fetch-stub style already used in `api.priorities.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { seoRankGap, seoRankHistory, seoRankTracker } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown) {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("rank tracker client", () => {
  it("reads the panel payload from the brand-scoped path", async () => {
    const fetchMock = stub({ rows: [], worklist: [], pool: { size: 0, cap: 200 },
                             budget: { searches: 0, cap: 3000, remaining: 3000, date: "2026-10-05" },
                             meta: null, job: null, competitors: [], enabled: true });
    const doc = await seoRankTracker("b1");
    expect(fetchMock.mock.calls[0][0]).toContain("/api/seo-geo/rank-tracker/b1");
    expect(doc.pool.cap).toBe(200);
  });

  it("url-encodes the query when reading history", async () => {
    const fetchMock = stub({ history: { query: "clat & cuet", raw: [], daily: [], rivals: {} } });
    await seoRankHistory("b1", "clat & cuet");
    expect(fetchMock.mock.calls[0][0]).toContain("query=clat%20%26%20cuet");
  });

  it("posts the query in the body for a gap card", async () => {
    const fetchMock = stub({ gap: { query: "q", narrative: "n" } });
    await seoRankGap("b1", "q");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ query: "q" });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run lib/api.ranktracker.test.ts
```

Expected: FAIL — `seoRankTracker is not exported`.

- [ ] **Step 3: Implement**

Append to `frontend/lib/api.ts`, after the page-speed section:

```ts
/* ------------------------------ rank tracker ------------------------------ */

export interface RankSerpEntry {
  position: number;
  domain: string;
  url: string;
  title: string;
}

export interface RankRow {
  query: string;
  position: number | null;
  url: string;
  checked_at: string;
  top: RankSerpEntry[];
  error: string | null;
}

export interface RankWorklistRow {
  query: string;
  position: number | null;
  impressions: number;
  leader: string;
  leader_position: number;
  leader_url: string;
  tracked_rival: boolean;
  delta_7d: number | null;
  score: number;
  reason: string;
}

export interface RankPool {
  size: number;
  cap: number;
  built_at: string | null;
  sources_used: string[];
  notes: string[];
}

export interface RankBudget {
  date: string;
  searches: number;
  cap: number;
  remaining: number;
}

export interface RankMeta {
  at: string;
  ranked: number;
  errors: number;
  rivals: string[];
  count: number;
}

export interface RankTrackerDoc {
  rows: RankRow[];
  meta: RankMeta | null;
  worklist: RankWorklistRow[];
  pool: RankPool;
  budget: RankBudget;
  competitors: string[];
  enabled: boolean;
  job: DeepJob | null;
}

export interface RankHistory {
  query: string;
  raw: [number, number | null][];
  daily: [string, { best: number; worst: number; last: number }][];
  rivals: Record<string, { raw: [number, number | null][];
                           daily: [string, { best: number; worst: number; last: number }][] }>;
}

export interface RankGap {
  query: string;
  our_url: string;
  our_position: number | null;
  their_url: string;
  their_domain: string;
  their_position: number;
  metrics: {
    words: { ours: number | null; theirs: number | null };
    headings: { ours: number | null; theirs: number | null };
    schema: { ours: string[] | null; theirs: string[] | null };
  };
  narrative: string;
  notes: string[];
  at: string;
  cached: boolean;
}

export const seoRankTracker = (id: string, opts?: RequestOptions) =>
  getJson<RankTrackerDoc>(`/api/seo-geo/rank-tracker/${id}`, opts);

export const seoRankHistory = (id: string, query: string, opts?: RequestOptions) =>
  getJson<{ history: RankHistory }>(
    `/api/seo-geo/rank-tracker/${id}/history?query=${encodeURIComponent(query)}`, opts);

export const seoRankSweep = (id: string) =>
  postJson<{ job: DeepJob }>(`/api/seo-geo/rank-tracker/${id}/sweep`, {});

export const seoRankPoolRebuild = (id: string) =>
  postJson<RankTrackerDoc>(`/api/seo-geo/rank-tracker/${id}/pool/rebuild`, {});

export const seoRankGap = (id: string, query: string) =>
  postJson<{ gap: RankGap }>(`/api/seo-geo/rank-tracker/${id}/gap`, { query });
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd frontend && npx vitest run lib/api.ranktracker.test.ts && npx tsc --noEmit
```

Expected: PASS and a clean type-check.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/api.ranktracker.test.ts
git commit -m "feat(seo): rank tracker API client and types"
```

---

## Task 11: The panel

**Files:**
- Create: `frontend/components/console/seo/ranktracker.tsx`
- Create: `frontend/components/console/seo/ranktracker.test.tsx`
- Modify: `frontend/app/seo.css`

**Interfaces:**
- Consumes: the Task 10 client functions and types; `ToastFn` and the `LoadError` / `Notes` helpers' visual language from `labs.tsx`.
- Produces: `export function RankTrackerView({ brandId, isCreator, onToast }: { brandId: string; isCreator: boolean; onToast: ToastFn })`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/components/console/seo/ranktracker.test.tsx`:

```tsx
// @vitest-environment jsdom
/** The panel's job is to make 200 rows actionable. These tests pin the four
 *  things that decide whether it does: the worklist leads, the filters select,
 *  a blocked budget is visible rather than silent, and a missing Search
 *  Console connection is explained rather than shown as an empty table. */
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RankTrackerView } from "./ranktracker";
import * as api from "@/lib/api";

afterEach(cleanup);

function doc(over: Partial<api.RankTrackerDoc> = {}): api.RankTrackerDoc {
  return {
    rows: [
      { query: "clat coaching", position: 8, url: "", checked_at: "", error: null,
        top: [{ position: 1, domain: "rival.com", url: "https://rival.com/a", title: "" }] },
      { query: "clat syllabus", position: 2, url: "", checked_at: "", error: null, top: [] },
      { query: "clat fees", position: null, url: "", checked_at: "", error: null, top: [] },
    ],
    meta: { at: "2026-10-05T09:00:00+00:00", ranked: 3, errors: 0, rivals: ["rival.com"], count: 3 },
    worklist: [{ query: "clat coaching", position: 8, impressions: 5000, leader: "rival.com",
                 leader_position: 1, leader_url: "https://rival.com/a", tracked_rival: true,
                 delta_7d: -3, score: 12.1, reason: "#8 — striking distance" }],
    pool: { size: 3, cap: 200, built_at: "2026-10-05T06:00:00+00:00",
            sources_used: ["custom", "gsc"], notes: [] },
    budget: { date: "2026-10-05", searches: 36, cap: 3000, remaining: 2964 },
    competitors: ["rival.com"], enabled: true, job: null,
    ...over,
  };
}

describe("RankTrackerView", () => {
  it("leads with the worklist and explains why the top row is there", async () => {
    vi.spyOn(api, "seoRankTracker").mockResolvedValue(doc());
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/striking distance/i)).toBeInTheDocument());
    expect(screen.getByText(/rival\.com/)).toBeInTheDocument();
  });

  it("filters the table down to striking-distance queries", async () => {
    vi.spyOn(api, "seoRankTracker").mockResolvedValue(doc());
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /striking distance/i }));

    const table = screen.getByRole("table");
    expect(table).toHaveTextContent("clat coaching");
    expect(table).not.toHaveTextContent("clat syllabus");   // #2, already won
  });

  it("says the daily budget is exhausted instead of failing silently", async () => {
    vi.spyOn(api, "seoRankTracker").mockResolvedValue(
      doc({ budget: { date: "2026-10-05", searches: 3000, cap: 3000, remaining: 0 } }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/daily search budget/i)).toBeInTheDocument());
  });

  it("explains a thin pool when Search Console is not connected", async () => {
    vi.spyOn(api, "seoRankTracker").mockResolvedValue(
      doc({ pool: { size: 4, cap: 200, built_at: "2026-10-05T06:00:00+00:00",
                    sources_used: ["custom", "seed"],
                    notes: ["Search Console: no access"] } }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/search console/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd frontend && npx vitest run components/console/seo/ranktracker.test.tsx
```

Expected: FAIL — cannot resolve `./ranktracker`.

- [ ] **Step 3: Implement the panel**

Create `frontend/components/console/seo/ranktracker.tsx`. Required structure (match the card/table visual language already in `labs.tsx` and `dashboard.tsx` — read `CompetitorsView` first and reuse its class names):

```tsx
"use client";

/** Rank tracker — the scheduled scoreboard.
 *
 *  200 rows is not an answer, so the worklist leads: the ten queries where a
 *  win is worth most and nearest. The full table is below it for the person
 *  who wants to look for themselves, and every row opens a drawer with its
 *  history and the gap card.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  seoRankGap, seoRankHistory, seoRankPoolRebuild, seoRankSweep, seoRankTracker,
  type RankGap, type RankHistory, type RankRow, type RankTrackerDoc,
} from "@/lib/api";

type Filter = "all" | "striking" | "losing" | "won" | "unranked";
type ToastFn = (message: string, tone?: "ok" | "err") => void;

const STRIKING_LOW = 4;
const STRIKING_HIGH = 20;

function matches(row: RankRow, filter: Filter): boolean {
  const p = row.position;
  switch (filter) {
    case "striking": return p !== null && p >= STRIKING_LOW && p <= STRIKING_HIGH;
    case "won":      return p !== null && p < STRIKING_LOW;
    case "unranked": return p === null;
    case "losing":   return p === null || p >= STRIKING_LOW;
    default:         return true;
  }
}

export function RankTrackerView({ brandId, isCreator, onToast }: {
  brandId: string; isCreator: boolean; onToast: ToastFn;
}) {
  const [doc, setDoc] = useState<RankTrackerDoc | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const [history, setHistory] = useState<RankHistory | null>(null);
  const [gap, setGap] = useState<RankGap | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setDoc(await seoRankTracker(brandId)); setError(""); }
    catch (e) { setError(String(e)); }
  }, [brandId]);

  useEffect(() => { void load(); }, [load]);

  // While a sweep runs, poll its progress — same pattern as the deep audit.
  useEffect(() => {
    if (doc?.job?.status !== "running") return;
    const timer = setInterval(() => { void load(); }, 3000);
    return () => clearInterval(timer);
  }, [doc?.job?.status, load]);

  const rows = useMemo(
    () => (doc?.rows ?? []).filter((r) => !r.error && matches(r, filter)),
    [doc, filter],
  );

  // ...render: header strip, worklist card, filter buttons, table, drawer
}
```

The render must include, in this order:

1. **Header strip** — `Tracking {pool.size} of {pool.cap} queries`, last sweep time from `meta.at`, `Credits today {budget.searches} / {budget.cap}`, a **Run now** button (rendered only when `isCreator`; disabled while `job?.status === "running"` or `budget.remaining === 0`), and a **Rebuild pool** button.
2. **Budget banner** — when `budget.remaining === 0`, the text `Daily search budget reached — the next sweep runs after midnight UTC.`
3. **Pool note banner** — when `pool.notes.length > 0`, render each note; the Search Console note must render its text so the Task 11 test matches on `/search console/i`.
4. **Worklist card** — heading `Fix these next`, one row per `doc.worklist` entry showing `query`, `#{position}`, `reason`, `leader` with `#{leader_position}`, and two buttons: `Why?` (calls `seoRankGap`, sets `gap`, opens the drawer) and `Brief` (calls the existing `seoBuildBrief` from `api.ts`).
5. **Filter buttons** — `All`, `Striking distance`, `Losing`, `Won`, `Not ranking`, setting `filter`.
6. **Table** (`<table>`, so `getByRole("table")` finds it) — columns: Query, Our rank, Δ7d, Leader, Impressions. Clicking a row sets `open` to the query and loads `seoRankHistory`.
7. **Drawer** — the selected query's history (render `daily` as a simple inline SVG polyline; no chart library — nothing new may be added to `package.json`), the row's `top` list, and the gap card when loaded.

Add the matching `.seo-rank__*` classes to `frontend/app/seo.css`, following the existing `.seo-pool__row` treatment.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd frontend && npx vitest run components/console/seo/ranktracker.test.tsx && npx tsc --noEmit
```

Expected: PASS (4 tests) and a clean type-check.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/console/seo/ranktracker.tsx frontend/components/console/seo/ranktracker.test.tsx frontend/app/seo.css
git commit -m "feat(seo): rank tracker panel — worklist, filterable table, history drawer"
```

---

## Task 12: Mount the panel

**Files:**
- Modify: `frontend/components/console/seo/SeoAgent.tsx:35-44` and its section render block (around line 804)
- Test: `frontend/components/console/seo/shell.test.tsx`

**Interfaces:**
- Consumes: `RankTrackerView` from Task 11.
- Produces: a navigable `rank-tracker` section.

- [ ] **Step 1: Write the failing test**

Append to `frontend/components/console/seo/shell.test.tsx`:

```tsx
it("offers a Rank tracker section", () => {
  render(
    <Shell
      sections={[{ id: "insights", label: "Insights" },
                 { id: "rank-tracker", label: "Rank tracker" }]}
      activeId="rank-tracker"
      onSelect={vi.fn()}
    >
      <div>panel</div>
    </Shell>,
  );
  expect(screen.getByRole("button", { name: /rank tracker/i }))
    .toHaveAttribute("aria-current", "page");
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run components/console/seo/shell.test.tsx
```

Expected: FAIL if the import or render shape differs from the existing tests in that file; adjust to match them, then confirm the assertion drives the change.

- [ ] **Step 3: Implement**

In `SeoAgent.tsx`, add to `SECTIONS` immediately after the `competitors` entry:

```tsx
  { id: "rank-tracker", label: "Rank tracker" },
```

Import the panel:

```tsx
import { RankTrackerView } from "./ranktracker";
```

And in the section render block, alongside the other `activeSection === "..."` branches:

```tsx
            {activeSection === "rank-tracker" && (
              <RankTrackerView brandId={brand.id} isCreator={isCreator} onToast={toast} />
            )}
```

Match the exact prop names the neighbouring branches use for brand id, creator flag and toast — read them before writing this.

- [ ] **Step 4: Run the frontend suite**

```bash
cd frontend && npx vitest run && npx tsc --noEmit && npm run build
```

Expected: PASS, clean types, successful build.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/console/seo/SeoAgent.tsx frontend/components/console/seo/shell.test.tsx
git commit -m "feat(seo): add the Rank tracker section to the SEO console"
```

---

## Task 13: Credit verification, environment, and the schedule

This task is deliberately last: the spec requires one manual sweep to be inspected before the schedule is enabled.

**Files:**
- Modify: `backend/.env.example`
- Modify: `README.md`
- Possibly modify: `backend/agents/SEO GEO agent/seo_geo_agent/sources.py` (`SERP_RESULTS`)

- [ ] **Step 1: Verify what Serper bills for `num=20`**

Check the account's credit balance, run one search at `num=20`, check it again:

```bash
curl -s -X POST https://google.serper.dev/search \
  -H "X-API-KEY: $SEO_SERPER_API_KEY" -H "Content-Type: application/json" \
  -d '{"q":"clat coaching","num":20,"gl":"in","hl":"en"}' | head -c 400
```

Compare the credit delta against the same call with `"num":10`. **If `num=20` costs more than one credit**, set `SERP_RESULTS = 10` in `sources.py`, update the constant's comment to record the measured cost, and re-run `cd backend && python -m pytest -m seo -q` (the Task 1 test asserts `num == 20` — change that assertion to 10 in the same commit, with a comment saying why).

- [ ] **Step 2: Document the environment**

Add to `backend/.env.example`, near the existing `SEO_CRON_KEY` entry:

```
# Rank tracker. Reuses SEO_CRON_KEY for POST /api/seo-geo/rank-tracker/cron.
# Set to 1 to stop every rank sweep service-wide without a deploy.
# SEO_RANK_SWEEP_DISABLED=0
```

- [ ] **Step 3: Document the schedule**

Add to `README.md`, in the section that describes the SEO cron:

```markdown
### Rank tracker schedule

`POST /api/seo-geo/rank-tracker/cron` (header `x-cron-key: $SEO_CRON_KEY`) sweeps
every enabled brand's rank pool. Cloud Scheduler runs it on `0 */2 * * *`.

At 200 queries × 12 runs/day that is ~2,400 Serper searches per brand per day
(~72,000/month). A per-brand ceiling of 3,000/day is enforced in
`rank_tracker.MAX_SEARCHES_PER_DAY`; `SEO_RANK_SWEEP_DISABLED=1` stops it
everywhere. To lower the cadence, change the Cloud Scheduler expression — no
code change is needed.
```

- [ ] **Step 4: Run one manual sweep and inspect it**

With the service deployed, click **Rebuild pool**, then **Run now**, and confirm in the panel: the pool reached a sensible size, `meta.errors` is 0 or near it, ranks are plausible for India (not US), and the worklist's top rows are queries worth working on.

- [ ] **Step 5: Create the Cloud Scheduler job**

```bash
gcloud scheduler jobs create http seo-rank-sweep \
  --schedule="0 */2 * * *" \
  --uri="https://<service-host>/api/seo-geo/rank-tracker/cron" \
  --http-method=POST \
  --headers="x-cron-key=<SEO_CRON_KEY>" \
  --attempt-deadline=900s \
  --location=<region>
```

- [ ] **Step 6: Commit**

```bash
git add backend/.env.example README.md "backend/agents/SEO GEO agent/seo_geo_agent/sources.py"
git commit -m "docs(seo): rank tracker environment, credit cost, and the two-hourly schedule"
```

---

## Done when

- `cd backend && python -m pytest -q` passes.
- `cd frontend && npx vitest run && npx tsc --noEmit && npm run build` all pass.
- The Rank tracker section loads, shows a pool of real queries, and a manual sweep records India-localised ranks.
- `/seo-geo/cron/run` no longer calls `rank_snapshot`, and the Competitors panel still shows shifts and suggested competitors.
- Cloud Scheduler's `seo-rank-sweep` job reports 200.
