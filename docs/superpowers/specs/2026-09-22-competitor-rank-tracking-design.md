# Competitor Rank Tracking (Query Pool Expansion) — Design

## Problem

The user wants to: build a pool of search queries about a brand (auto-generated
plus their own custom queries), run each through live SERP search, see the
brand's current rank per query, see which competitors show up per query, and
see which queries a given competitor is winning that the brand isn't.

Most of this already exists but is buried and capped tightly:

- `competitors.rank_snapshot()` (`backend/agents/SEO GEO agent/seo_geo_agent/competitors.py:40-66`)
  already live-searches a keyword list, finds the brand's position, records the
  top-5 domains per query, and aggregates a `suggested_competitors` list.
- `competitors._domain_stats()` (`:197-214`) already computes, per competitor
  domain, `keywords_won` — queries where that domain outranks the brand. This
  is the existing "competitor keyword leverage" concept.
- Both are driven by a hardcoded, capped keyword list: brand seeds + cluster
  heads, **capped at 15** (`:27-37`), for Serper-cost control.
- **Nothing lets a user type in their own queries.** The only free-text
  keyword input anywhere in the app is a single-keyword field in the Content
  Brief tool — unrelated to this flow.

So this is an expansion of existing, working machinery, not a new subsystem:
widen the query pool (auto-derived + user-supplied), raise the cap to a
sane bound, and surface the existing per-query/per-competitor data more
directly — not reinvent rank tracking.

## Decisions made in brainstorming

1. **Two SERP backends, kept separate.** A prior session task repointed
   `sources.serper_search()` (used by Keyword Lab and cluster-owner detection)
   to call DataForSEO internally, keeping the old name. That is
   already deployed and working — leave it alone. This feature gets its own
   function, `sources.brand_rank_search()`, backed by the real Serper.dev API
   key the user just supplied (`SEO_SERPER_API_KEY`, already in Secret
   Manager). The two must never be conflated: Keyword Lab's expansion calls
   stay on DataForSEO; rank tracking's calls use real Serper.
2. **Manual run, capped pool (50), not an automatic background job.** Serper
   is billed per search. The combined pool (auto-derived + user-supplied,
   deduplicated) is capped at 50 queries; a live SERP check runs only when the
   user explicitly triggers it (the existing rank-snapshot trigger button),
   never automatically. The UI must show the pool size against the cap before
   running.
3. **Enhance the existing Competitors section — no new sidebar entry.** This
   is additive to what's already there (rank snapshot, competitor profiles),
   not a parallel destination.

## Design

### 1. Backend: real-Serper search function

New function in `sources.py`, parallel to (not replacing) `serper_search()`:

```python
def _real_serper_key() -> str:
    return os.environ.get("SEO_SERPER_API_KEY", "").strip()

def brand_rank_available() -> bool:
    return bool(_real_serper_key()) and state.use_network()

def brand_rank_search(query: str, client: httpx.Client | None = None) -> dict:
    """One Google SERP via real Serper.dev, for competitor rank tracking only.
    Kept separate from serper_search() (DataForSEO-backed) so Keyword Lab's
    provider and this feature's provider can never be conflated."""
```

Same return shape as `serper_search()` (`{organic, related, paa, aio_present}`)
so `competitors.py` can consume either interchangeably — the caller decides
which function to use, the shape doesn't force a choice.

### 2. Backend: custom queries, persisted per brand

New small persistence functions in `competitors.py` (or a new sibling module
if `competitors.py` is already large — check its line count before deciding;
follow the existing `_DOC`-constant + `latest()` pattern either way):

- `add_custom_query(brand_id: str, query: str) -> list[str]`
- `remove_custom_query(brand_id: str, query: str) -> list[str]`
- `list_custom_queries(brand_id: str) -> list[str]`

Persisted as `state.save(f"custom-queries-{brand_id}", {"queries": [...]})`,
deduplicated (case-insensitive), each capped to a sane length (e.g. 200 chars)
to prevent abuse.

### 3. Backend: widen the tracked-query pool, cap at 50

Modify `rank_snapshot()`'s keyword-gathering step (currently seeds +
cluster-heads, capped 15) to combine, in priority order, until the 50-query
cap is hit:

1. User's custom queries (highest priority — they asked for these explicitly)
2. Brand seeds + cluster heads (today's existing behavior)
3. `keyword_pool.latest(brand_id)`'s top keywords by `opportunity` (new source)

Deduplicate case-insensitively across all three sources before capping.
Switch the search call inside `rank_snapshot()` from `sources.serper_search`
to `sources.brand_rank_search` (per decision 1). If `brand_rank_available()`
is false (no key / offline), degrade with a note exactly like every other
optional source in this codebase — do not fall back to the DataForSEO-backed
function, since that would silently defeat the separation this feature
exists to establish.

`_domain_stats()`'s `keywords_won` computation needs no logic change — it
already reads whatever the latest rank snapshot contains, so a bigger snapshot
naturally produces a bigger, more useful `keywords_won` list with zero code
change there.

### 4. Backend: two small routes

```
POST   /api/seo-geo/competitors/{brand_id}/custom-queries   {"query": "..."}
DELETE /api/seo-geo/competitors/{brand_id}/custom-queries   {"query": "..."}
```

Following the router's existing `_brand_or_404` + `Depends(get_current_user)`
+ `Activity`/`trail.records(...)` conventions exactly (read the neighboring
competitor routes in `seo_geo.py` before writing these, same as every prior
task in this app has done). The existing rank-snapshot trigger route needs no
signature change — it already calls `rank_snapshot()`, which now internally
uses the wider pool.

### 5. Frontend: Competitors section additions

In `CompetitorsView` (`frontend/components/console/seo/labs.tsx`):

- A small add/remove list UI for custom queries (text input + add button +
  a removable chip/row per existing custom query), calling the two new
  routes.
- A pool-size indicator before the rank-snapshot trigger: "Will check N of 50
  queries" (auto-derived + custom, deduplicated, capped) — computed
  client-side is fine if the count is available from `list_custom_queries`
  plus what's already known about seeds/clusters/pool size, or read from a
  small field the backend adds to its response; implementer's call on the
  cheapest accurate source.
- The per-query rank-snapshot results and `keywords_won` leverage data: check
  what `CompetitorsView` already renders from the existing snapshot doc before
  adding new UI — if a per-query table already exists, it will simply show
  more rows once the pool widens; if it doesn't exist yet (the brainstorming
  research didn't confirm either way), add a table: query → brand position →
  top domains, matching the existing card/table visual language from the
  recent dashboard revamp (`.seo-pool__row`-style treatment).

### 6. Cost safety

The 50-query cap is a hard ceiling in backend code (`min(50, ...)`), not just
a UI suggestion — the API must enforce it even if a client sends more.

### 7. Testing

- Backend: pytest for `add_custom_query`/`remove_custom_query`/dedup
  behavior; a test that the combined pool respects the 50 cap and the stated
  priority order (custom queries survive capping before auto-derived ones
  when the combined total exceeds 50); a test that `rank_snapshot()` uses
  `brand_rank_search` not `serper_search` (mock both, assert only the real-
  Serper one is called); a degrade-not-raise test for `brand_rank_available()
  == False`.
- Frontend: component tests for the custom-query add/remove UI and the pool-
  size indicator, following this codebase's existing testing-library
  conventions (established in the prior dashboard-revamp work).

## Out of scope for this pass

- Historical rank trend/graphing over time (this pass is current-snapshot
  only, matching what `rank_snapshot()` already does).
- Any change to Keyword Lab's DataForSEO-backed expansion.
- A dedicated new sidebar section (decision 3).
- Automatic/scheduled re-runs of the rank check.
