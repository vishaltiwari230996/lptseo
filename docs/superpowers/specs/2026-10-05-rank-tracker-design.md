# Rank Tracker — Scheduled 200-Query Rank & Competitor Scoreboard — Design

## Problem

Law Prep Tutorial's SEO agent produces audits, briefs, and recommendations, but
the owner cannot see whether any of it moves rankings. There is no instrument
that answers, day over day: *for the queries students actually type, where do we
sit, who sits above us, and which of those gaps is worth closing next?*

What exists today is close but too small and too manual to answer that:

- `competitors.rank_snapshot()`
  (`backend/agents/SEO GEO agent/seo_geo_agent/competitors.py:92`) live-searches a
  query pool, records our position and the top-5 domains per query, and
  aggregates `suggested_competitors`.
- `competitors.rank_tracking_pool()` (`:41`) merges custom queries + brand
  seeds + cluster heads + keyword-pool terms, **capped at 50**
  (`MAX_RANK_POOL`).
- History is `MAX_SNAPSHOTS = 12` entries keyed by **calendar date**
  (`date.today().isoformat()`, `:110`) — so a sub-daily cadence overwrites the
  same day's entry repeatedly, and total retention is ~12 days.
- `POST /api/seo-geo/cron/run` (`backend/app/routers/seo_geo.py:823`) already
  exists, is HMAC-guarded by `SEO_CRON_KEY`, and already calls `rank_snapshot()`
  per brand as a best-effort extra inside the full brand sweep.
- `jobs.py` already provides background jobs (`jobs.start()` + polled
  `Progress`) and size-chunked list persistence (`jobs.save_list()` /
  `load_list()`, 800KB chunks under Firestore's 1MB document limit).

So the machinery is real. This design lifts three limits the
[2026-09-22 competitor rank tracking design](2026-09-22-competitor-rank-tracking-design.md)
deliberately set — pool cap 50, manual-trigger-only, current-snapshot-only — and
adds the layer that converts measurement into a work queue.

## Two defects found during brainstorming

These are not enhancements; they mean the numbers shown today are wrong.

1. **No locale on the rank SERP.** `sources.brand_rank_search()`
   (`sources.py:405`) posts `{"q": query, "num": 10}` with no `gl`, `hl`, or
   `location`, so it reads plain google.com — US-default results. The
   DataForSEO path immediately above it pins India correctly
   (`DATAFORSEO_LOCATION_CODE = 2356`, `sources.py:27`) and its comment
   explicitly notes that Serper had no geo params. For a Jodhpur-based CLAT
   brand, US SERPs are close to meaningless.
2. **`num: 10` erases the striking-distance band.** Anything at position 11–20
   reads as "not ranking". Those are precisely the queries most worth working
   on.

Both are fixed as prerequisites in this design (section 3).

## Decisions made in brainstorming

1. **Purpose: scoreboard *and* action.** A rank table alone restates a problem
   the owner already knows they have. The panel must also rank the gaps by
   winnability and explain each one.
2. **Query source: Search Console preferred, self-sustaining fallback.** GSC
   connection status in production is unconfirmed, so the pool builder treats
   GSC as one prioritised source among several and degrades with a visible
   banner rather than emptying.
3. **Cadence: full 200-query sweep every 2 hours.** Raised as a cost concern
   (≈2,400 searches/day ≈ 72,000 Serper credits/month, with ~11 of every 12
   data points identical to the prior one, since organic rank moves on a scale
   of days). The owner reaffirmed the 2-hourly full sweep. It is implemented as
   specified, with a hard daily budget ceiling and a kill switch so it cannot
   run away.
4. **One rank engine, not two.** `competitors.rank_snapshot()` is converted to a
   shim reading rank_tracker's latest run, and the existing `/seo-geo/cron/run`
   stops invoking it. Two independent sweeps over overlapping query sets would
   double-bill Serper on every cycle.
5. **Its own module and its own panel.** `competitors.py` (~450 lines) already
   owns competitor discovery, sitemap watch, and SERP x-ray; `labs.tsx` is 871
   lines and `SeoAgent.tsx` is 1,076. This gets `rank_tracker.py` and
   `ranktracker.tsx`.
6. **Pool rebuilds daily, sweeps run 2-hourly.** A pool that changes every run
   produces trend lines that mean nothing.

## Design

### 1. Module layout

| New | Purpose |
|---|---|
| `backend/agents/SEO GEO agent/seo_geo_agent/rank_tracker.py` | pool building, sweep, history, rollup, worklist |
| `backend/agents/SEO GEO agent/seo_geo_agent/rank_gap.py` | gap-card diff + the single LLM narrative call |
| `frontend/components/console/seo/ranktracker.tsx` | the panel |
| `frontend/components/console/seo/ranktracker.test.tsx` | its tests |

Modified: `sources.py` (locale params), `competitors.py` (`rank_snapshot` →
shim), `app/routers/seo_geo.py` (routes; remove the `rank_snapshot` call from
`cron_run`), `SeoAgent.tsx` (one sidebar entry), `frontend/lib/api.ts` (client
functions + types), `frontend/app/seo.css` (panel styles).

### 2. Query pool — `rank_tracker.build_pool(brand) -> dict`

Merged in priority order, deduplicated case-insensitively, capped at
`MAX_POOL = 200`:

1. **Custom queries** — `competitors.list_custom_queries(brand_id)`. Always
   included, never evicted by the cap; explicit user intent outranks everything.
2. **Search Console queries** — `sources.gsc_fetch()` over the last 28 days,
   aggregated per query (summing impressions across pages), sorted by
   impressions descending. Dropped: impressions < 3, length over `MAX_QUERY_LEN`
   (200 characters — unrelated to the 200-query pool cap).
3. **Harvested related + PAA** — every `brand_rank_search()` response already
   carries `related` and `paa` at no extra credit cost. Each sweep writes what
   it saw into `rank-harvest-{brand_id}` as `{query: seen_count}`; the daily
   rebuild promotes the highest-count unseen entries. This is what makes the
   pool self-sustaining when GSC is unavailable, and it supplies real Google
   queries rather than guesses.
4. **Brand seeds + cluster heads** — today's `competitors.tracked_keywords()`.

Persisted to `rank-pool-{brand_id}`:

```json
{
  "queries": [
    {"query": "...", "source": "gsc|custom|harvest|seed",
     "impressions": 0, "added_at": "ISO8601", "active": true}
  ],
  "built_at": "ISO8601",
  "sources_used": ["custom", "harvest", "seed"],
  "notes": ["Search Console unavailable — pool built from harvested + seed queries"]
}
```

Queries that fall out of a rebuild are marked `active: false` rather than
deleted, so their history remains readable. Inactive queries are not swept.

GSC failure is a `notes` entry and a UI banner, never an exception — matching
how every other optional source in this codebase degrades.

### 3. Locale and depth — `sources.brand_rank_search()`

```python
SERP_COUNTRY = "in"   # India — same market the DataForSEO path already pins
SERP_LANGUAGE = "en"
SERP_RESULTS = 20     # see the credit note below

def brand_rank_search(query, client=None, *, gl=SERP_COUNTRY,
                      hl=SERP_LANGUAGE, num=SERP_RESULTS) -> dict:
```

Posts `{"q": query, "num": num, "gl": gl, "hl": hl}`. Country-level, not
city-level: CLAT coaching competes nationally, so pinning Jodhpur would produce
a local SERP that misrepresents the national picture. Overridable per brand via
an optional `serp_country` field on the brand document, defaulting to `"in"`.

**Credit verification gate.** Serper may bill `num=20` above one credit per
search. Before the 2-hourly schedule is enabled, the implementation must confirm
the actual multiplier against the live account; if `num=20` costs more than one
credit, `SERP_RESULTS` stays at 10 and the striking-distance band is sourced
from GSC average position instead. This is a step in the plan, not an
assumption.

The return shape is unchanged, so every existing caller is unaffected.

### 4. The sweep — `rank_tracker.sweep(brand, progress)`

Runs as a background job under the existing `jobs.start("rank-sweep", brand_id,
body)`. 200 sequential Serper calls take roughly four minutes — far beyond any
HTTP request. `jobs.start()` already refuses a concurrent second run for the
same `(kind, brand)`, which is the whole concurrency story given a 2-hour
interval and a 4-minute job.

Per active query: one `brand_rank_search()` call; record our position (first
organic result whose link contains `brand["domain"]`), our ranking URL, and the
full top-`num` list as `{position, domain, url, title}`; append `related`/`paa`
to the harvest doc. A query that raises is recorded as
`{"error": "..."}` and the sweep continues — one bad SERP must not lose the
other 199.

**Budget guard.** `rank-budget-{brand_id}` holds `{date, searches}` and is
updated through `state.mutate()` (transactional; `load`+`save` would lose counts
across concurrent writers). `MAX_SEARCHES_PER_DAY = 3000` — above the 2,400 the
chosen cadence needs, below runaway. A sweep that would cross the ceiling stops
and records why; the panel shows `used / budget`.

**Kill switches.** Per-brand `rank_tracking_enabled` (default true) and the
environment variable `SEO_RANK_SWEEP_DISABLED=1`, which disables sweeping
service-wide without a deploy.

After the sweep: write `rank-latest`, append to `rank-history`, run the daily
rollup if this is the day's first sweep, recompute the worklist.

### 5. Storage

Three documents per brand, all written through the existing
`jobs.save_list()` / `load_list()` chunker, so none of them can hit Firestore's
1MB ceiling.

**`rank-latest-{brand_id}`** — most recent run only, full detail:

```json
{"query": "...", "position": 9, "url": "https://...", "checked_at": "ISO8601",
 "top": [{"position": 1, "domain": "...", "url": "...", "title": "..."}],
 "error": null}
```

200 queries × `SERP_RESULTS` entries ≈ 400–700KB at `num=20` → 1–2 chunks.

**`rank-history-{brand_id}`** — compact series per query:

```json
{"query": "...",
 "raw":  [[epoch_hours, position_or_null], ...],
 "daily":[["2026-10-05", {"best": 7, "worst": 11, "last": 9}], ...],
 "rivals": {"competitor.com": {"raw": [...], "daily": [...]}}}
```

Retention: **every run for 7 days** (84 raw points/query), then rolled into
**one daily point for 180 days**. The rollup stores `best`, `worst`, and `last`
— three integers — so the chart draws an honest daily band instead of pretending
a single number described the day. Rollup runs as the final step of the day's
first sweep and trims anything past both windows.

`rivals` carries the series for **our domain's tracked competitors only**
(`brand["competitors"]`, max 8). Keeping a series for every result slot would
multiply storage by an order of magnitude for drive-by domains that appeared
once. Full top-`SERP_RESULTS` detail for the current moment lives in `rank-latest`.

Estimated steady state: 200 queries × (84 raw + 180 daily + up to 8 rival
series) — chunked, this lands in the low single-digit MB across a handful of
documents.

**`rank-harvest-{brand_id}`** — `{query: seen_count}`, trimmed to the top 2,000
entries at each rebuild.

**`rank-pool-{brand_id}`**, **`rank-budget-{brand_id}`** — as described above.

### 6. Worklist — `rank_tracker.worklist(brand) -> list[dict]`

Pure Python, no LLM, recomputed at the end of every sweep. Deterministic and
unit-testable, which an LLM ranking would not be.

```
score = impressions_weight × position_band × competitor_gap × trend_penalty
```

- `impressions_weight` — `log1p(gsc_impressions)`, defaulting to a small
  constant for queries with no GSC data (harvested/custom), so a query with no
  impressions history is still rankable but does not outrank proven demand.
- `position_band` — peaks across ranks 4–20, falls toward 0 for ranks 1–3
  (already won; effort is better spent elsewhere) and above 40 (not winnable
  this quarter). Unranked queries get the floor value, not zero — they stay
  visible but sink below winnable work.
- `competitor_gap` — larger when a *tracked* competitor sits above us than when
  an unknown domain does; a rival we already study is a gap we can actually
  analyse.
- `trend_penalty` — amplifies queries that lost ground over the last 7 days
  (a live regression outranks a long-standing weakness).

Output rows carry the inputs alongside the score, so the panel can explain the
ranking rather than presenting a magic number. Top 10 pin to the top of the
panel.

### 7. Gap card — `rank_gap.explain(brand, query) -> dict`

On click, not on sweep. Fetches our ranking URL and the top competitor's URL via
`sources.fetch_page`, then diffs them on signals this codebase already computes:
word count, H2/H3 coverage, schema presence, and PAA-question coverage (see
`landing_audit.py`, `keyword_density.py` — reuse those functions, do not
reimplement them). One LLM call via `sources.llm_text` turns the diff into a
narrative. Cached in `rank-gap-{brand_id}-{hash(query, competitor_url)}` for 24
hours, so clicking around the table is free after the first look.

If we rank nowhere for the query, the card compares the winner against our
closest topically-related page instead, and says so.

### 8. Brief

A button wiring the query straight into the existing
`POST /api/seo-geo/briefs/{brand_id}` endpoint. No new machinery.

### 9. Routes

```
GET  /api/seo-geo/rank-tracker/{brand_id}                 latest + worklist + pool meta + sweep status + budget
GET  /api/seo-geo/rank-tracker/{brand_id}/history?query=  one query's series
POST /api/seo-geo/rank-tracker/{brand_id}/sweep           run now (jobs.start)
POST /api/seo-geo/rank-tracker/{brand_id}/pool/rebuild    rebuild the pool now
POST /api/seo-geo/rank-tracker/{brand_id}/gap             {"query": "..."} -> gap card
POST /api/seo-geo/rank-tracker/cron                       x-cron-key; sweeps every enabled brand
```

All brand routes follow the router's existing conventions exactly:
`_brand_or_404`, `Depends(get_current_user)` (`require_creator` for pool rebuild
and manual sweep, which spend money), and `Activity` / `trail.records(...)`.
Read the neighbouring competitor routes before writing these.

The cron route mirrors `cron_run`'s contract precisely: 503 when `SEO_CRON_KEY`
is unset, 403 on `hmac.compare_digest` mismatch, 200 all-ok / 207 partial / 502
all-failed, since Cloud Scheduler reads only the status code.

It is **separate** from `/seo-geo/cron/run` so the 2-hourly cadence does not
drag `insights.run_brand()` along with it twelve times a day. Cloud Scheduler:
`0 */2 * * *`.

`cron_run`'s existing `seo_competitors.rank_snapshot(brand)` call is removed in
the same change that converts `rank_snapshot` to a shim (decision 4).

### 10. Panel — `ranktracker.tsx`

New sidebar section `{ id: "rank-tracker", label: "Rank tracker" }` in
`SECTIONS` (`SeoAgent.tsx:35`).

- **Header strip** — pool size · last sweep · next sweep · credits used today /
  budget · [Run now]. Shows the degraded banner when GSC is absent.
- **Worklist card** — top 10 winnable queries: query, our rank, who is above us,
  why it scored, and **[Why?]** (gap card) and **[Brief]** buttons.
- **Full table** — every active query: query, our rank, Δ24h, Δ7d, sparkline,
  one column per tracked competitor, GSC impressions. Sortable; filters for
  *losing / striking distance / won / new*.
- **Detail drawer** — full history chart (us vs. tracked competitors, daily
  band), the current SERP as last swept, and the gap card.

Visual language matches the recent dashboard revamp (`seo.css`,
`dashboard.css`); follow the existing card/table treatment rather than
introducing a new one.

While a sweep is running, the panel polls the job's progress document through
the existing status mechanism, exactly as the deep audit and page-speed panels
already do.

### 11. Testing

**Backend (pytest).** Pool priority, case-insensitive dedup, and the 200 cap,
with custom queries surviving the cap · pool degrades with a note when
`gsc_fetch` raises `CredentialMissing` · harvested related/PAA promote into the
next rebuild · `brand_rank_search` actually sends `gl`/`hl`/`num` · rollup turns
7-day-old raw points into correct `{best, worst, last}` dailies · retention
trims past 7 raw days and 180 daily days · budget guard refuses a sweep at the
ceiling and records why · the sweep continues past a single query that raises ·
`competitors.rank_snapshot` makes **zero** Serper calls after becoming a shim ·
cron route returns 503/403/207/502 on the right conditions.

**Frontend (vitest + testing-library),** following the conventions already in
`dashboard.test.tsx` / `labs.test.tsx`: worklist renders and sorts · table
filters select the right rows · drawer opens with the history chart · degraded
banner appears when the API reports GSC missing · budget exhaustion renders as a
blocking message, not a silent no-op.

### 12. Rollout order

The locale fix (section 3) changes what every rank number means, so the sweep
must not run at scale before it lands. Order: locale fix and its credit
verification → pool builder → storage and sweep → shim conversion and cron-call
removal → routes → panel → worklist → gap card → Cloud Scheduler enablement
last, after one manual sweep has been inspected.

## Out of scope

- Mobile vs. desktop SERP split (desktop only for this pass).
- Local-pack / Maps rank tracking.
- Any automated content change — the panel aims the work; a human does it.
- Changes to Keyword Lab's DataForSEO-backed expansion.
- Multi-country tracking.

## Known limits, stated plainly

- **Rank tracking measures; it does not improve.** The worklist and gap cards
  point at what to fix and why, but closing a gap is human work.
- **At 2-hourly, roughly 11 of every 12 data points repeat the previous one.**
  The daily rollup makes them nearly free to store; they are not free to fetch.
  The budget guard and kill switch exist so the cadence can be lowered later
  without a code change — adjust the Cloud Scheduler expression.
- **Serper rank ≠ your rank.** It is a depersonalised, country-level SERP, which
  is the right instrument for tracking movement, but it will not match what any
  individual logged-in student sees.
