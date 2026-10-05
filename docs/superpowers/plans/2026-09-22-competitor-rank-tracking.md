# Competitor Rank Tracking (Query Pool Expansion) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a brand's rank-tracking query pool include user-supplied custom queries and the keyword pool's top-opportunity terms (not just 15 hardcoded seeds+cluster-heads), capped at 50, checked via a real Serper.dev key kept strictly separate from the DataForSEO-backed provider Keyword Lab uses.

**Architecture:** `competitors.py` already has `rank_snapshot()` (live SERP check per tracked keyword) and `_domain_stats()` (competitor keyword-leverage math) — both already render in the frontend (`CompetitorsView`'s per-query shift list and per-competitor `keywords_won` list). This plan does NOT touch that rendering logic — it widens what feeds `rank_snapshot()`'s keyword loop (a new `rank_tracking_pool()` function combining custom queries + existing `tracked_keywords()` + `keyword_pool`'s top terms, capped at 50) and adds a new real-Serper search backend (`sources.brand_rank_search`) used only by this one call site, plus the UI to manage custom queries and see the pool size against its cap.

**Tech Stack:** FastAPI + Firestore/local-JSON (`state.py`) backend, Next.js + React frontend, real Serper.dev API (`SEO_SERPER_API_KEY`, already in Secret Manager), existing pytest/vitest test harnesses.

**Spec:** `docs/superpowers/specs/2026-09-22-competitor-rank-tracking-design.md`

## Global Constraints

- `sources.serper_search()`/`serper_available()` (DataForSEO-backed) must NOT be touched — Keyword Lab, `serp_deep_dive()`, and `build_profiles()` keep using them unchanged. Only `rank_snapshot()` switches providers.
- The new real-Serper backend reads `SEO_SERPER_API_KEY` from the environment only (no Firestore admin-config fallback — that fallback exists for DataForSEO specifically per its own docstring, not a general pattern to copy).
- The combined query pool is hard-capped at 50 in backend code, not just the UI — enforce with `[:50]`/a named constant, never trust a client-supplied count.
- Priority order when the combined pool exceeds the cap: custom queries first (explicit user intent), then existing auto-derived (`tracked_keywords()` — seeds + cluster heads), then keyword-pool top-opportunity terms.
- Every new backend function follows this module's own local convention: `competitors.py` does NOT use the `_DOC = "xxx-{}"` constant pattern other sibling modules use — it builds persistence keys as inline f-strings at each call site (e.g. `f"ranks-{brand['id']}"`). New code in this file matches that local style, not the majority-module style.
- No auto-triggering: the rank check only runs when the user clicks the existing "Check now" button (`track()` in `CompetitorsView`) — adding a custom query does not itself trigger a live SERP check.
- Never mock network calls with a mocking library in this test suite — the established convention (`test_seo_lab.py`) is plain-lambda injection matching the real function's exact input/output shape, with real `state.save`/`state.load` under `SEO_OFFLINE=1`.

---

## Task 1: Real-Serper search backend in `sources.py`

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/sources.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py` (check if this file exists first — `ls backend/agents/"SEO GEO agent"/seo_geo_agent/tests/test_sources*.py`; if a sources test file already exists, add to it, else create it)

**Interfaces:**
- Consumes: nothing new — `state.use_network()`, `CredentialMissing` (both already imported in this file).
- Produces: `brand_rank_available() -> bool` and `brand_rank_search(query: str, client: httpx.Client | None = None) -> dict` (same return shape as `serper_search()`: `{organic: [{link,title,position}], related: [str], paa: [str], aio_present: bool}`). Task 3 imports and calls both.

- [ ] **Step 1: Confirm the current file state**

Run: `grep -n "^DATAFORSEO_ENDPOINT\|^def serper_\|^def _dataforseo_auth" "backend/agents/SEO GEO agent/seo_geo_agent/sources.py"` — confirm the constants/functions are still at roughly the locations this plan assumes (module-level constants near the top, `_dataforseo_auth`/`serper_available`/`serper_search` as a trio later in the file) before editing.

- [ ] **Step 2: Write the failing tests**

```python
# add to backend/agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py
"""sources.py — real-Serper rank-tracking backend, kept separate from DataForSEO."""
from __future__ import annotations

from unittest.mock import Mock, patch

from seo_geo_agent import sources
from seo_geo_agent.sources import CredentialMissing


def test_brand_rank_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("SEO_SERPER_API_KEY", raising=False)
    assert sources.brand_rank_available() is False


def test_brand_rank_available_with_key(monkeypatch):
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")
    assert sources.brand_rank_available() is True


def test_brand_rank_search_raises_without_key(monkeypatch):
    monkeypatch.delenv("SEO_SERPER_API_KEY", raising=False)
    try:
        sources.brand_rank_search("clat coaching jaipur")
        assert False, "expected CredentialMissing"
    except CredentialMissing as exc:
        assert "SEO_SERPER_API_KEY" in str(exc)


def test_brand_rank_search_parses_real_serper_shape(monkeypatch):
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {
        "organic": [
            {"link": "https://lawpreptutorial.com/clat", "title": "CLAT Coaching", "position": 1},
            {"link": "https://competitor.com/clat", "title": "Competitor CLAT", "position": 2},
        ],
        "relatedSearches": [{"query": "best clat coaching"}],
        "peopleAlsoAsk": [{"question": "Which is the best CLAT coaching?"}],
        "aiOverview": {"text": "CLAT coaching overview..."},
    }
    fake_client = Mock()
    fake_client.post.return_value = fake_response

    result = sources.brand_rank_search("clat coaching", client=fake_client)

    assert result["organic"][0]["link"] == "https://lawpreptutorial.com/clat"
    assert result["organic"][1]["position"] == 2
    assert result["related"] == ["best clat coaching"]
    assert result["paa"] == ["Which is the best CLAT coaching?"]
    assert result["aio_present"] is True

    # confirm it hit the real Serper endpoint with the API-key header, not DataForSEO's Basic auth
    call_kwargs = fake_client.post.call_args.kwargs
    assert call_kwargs["headers"]["X-API-KEY"] == "test-key"
    assert "auth" not in call_kwargs
```

If `test_sources.py` already exists with its own `BRAND`/fixture conventions, adapt these tests' style to match rather than introducing a second style in the same file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest seo_geo_agent/tests/test_sources.py -v -k brand_rank`
Expected: FAIL — `AttributeError: module 'seo_geo_agent.sources' has no attribute 'brand_rank_available'`

- [ ] **Step 3: Implement `brand_rank_available`/`brand_rank_search`**

Add near the other module-level constants at the top of `sources.py` (alongside `DATAFORSEO_ENDPOINT`):

```python
REAL_SERPER_ENDPOINT = "https://google.serper.dev/search"
```

Add near `_dataforseo_auth`/`serper_available`/`serper_search` (keep them grouped — this is a sibling provider for a different caller, not a replacement):

```python
def _real_serper_key() -> str:
    """Env var only — deliberately no Firestore admin-config fallback (that
    exists for DataForSEO specifically). This key is kept on its own so
    competitor rank tracking can never silently fall back to the DataForSEO
    provider serper_search() actually calls."""
    return os.environ.get("SEO_SERPER_API_KEY", "").strip()


def brand_rank_available() -> bool:
    return bool(_real_serper_key()) and state.use_network()


def brand_rank_search(query: str, client: httpx.Client | None = None) -> dict:
    """One Google SERP via real Serper.dev — used ONLY by competitor rank
    tracking (competitors.rank_snapshot). Every other caller in this codebase
    (Keyword Lab, SERP X-ray, competitor profiles) stays on serper_search()'s
    DataForSEO backend; the two providers must never be conflated. Same
    return shape as serper_search() so rank_snapshot() doesn't care which
    provider answered.
    """
    key = _real_serper_key()
    if not key or not state.use_network():
        raise CredentialMissing("SEO_SERPER_API_KEY not set")
    own = client is None
    cli = client or httpx.Client(timeout=20)
    try:
        resp = cli.post(
            REAL_SERPER_ENDPOINT,
            json={"q": query, "num": 10},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own:
            cli.close()
    return {
        "organic": [
            {"link": r.get("link", ""), "title": r.get("title", ""), "position": r.get("position", i + 1)}
            for i, r in enumerate(data.get("organic", [])[:10])
        ],
        "related": [r.get("query", "") for r in data.get("relatedSearches", []) if r.get("query")],
        "paa": [q.get("question", "") for q in data.get("peopleAlsoAsk", []) if q.get("question")],
        "aio_present": bool((data.get("aiOverview") or {}).get("text")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest seo_geo_agent/tests/test_sources.py -v -k brand_rank`
Expected: PASS

- [ ] **Step 5: Run the full backend suite for regressions**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest -v`
Expected: PASS (same pre-existing unrelated failures as baseline, if any — check with `git stash` if unsure whether a failure is pre-existing, then `git stash pop` immediately after and re-run `git add`/verify staging is intact afterward, since stash affects the whole repo index)

- [ ] **Step 6: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/sources.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_sources.py"
git commit -m "feat: add real-Serper brand_rank_search/available, separate from DataForSEO-backed serper_search"
```

---

## Task 2: Custom-query persistence in `competitors.py`

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/competitors.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_competitors.py`

**Interfaces:**
- Consumes: `state.load`/`state.save` (already imported in this file).
- Produces: `list_custom_queries(brand_id: str) -> list[str]`, `add_custom_query(brand_id: str, query: str) -> list[str]` (raises `ValueError` on empty/too-long query), `remove_custom_query(brand_id: str, query: str) -> list[str]`. Task 3 calls `list_custom_queries`; Task 4's routes call `add_custom_query`/`remove_custom_query`/`list_custom_queries`.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_competitors.py
def test_add_custom_query_persists_and_dedupes_case_insensitively():
    competitors.add_custom_query("acme", "CLAT coaching Jodhpur")
    result = competitors.add_custom_query("acme", "clat coaching jodhpur")  # same, different case
    assert result == ["CLAT coaching Jodhpur"]  # first-occurrence casing wins, no duplicate


def test_add_custom_query_rejects_empty_or_too_long():
    try:
        competitors.add_custom_query("acme", "   ")
        assert False, "expected ValueError for empty query"
    except ValueError:
        pass
    try:
        competitors.add_custom_query("acme", "x" * 201)
        assert False, "expected ValueError for too-long query"
    except ValueError:
        pass


def test_remove_custom_query_removes_case_insensitively():
    competitors.add_custom_query("acme", "judiciary exam prep")
    result = competitors.remove_custom_query("acme", "JUDICIARY EXAM PREP")
    assert result == []
    assert competitors.list_custom_queries("acme") == []


def test_list_custom_queries_empty_when_never_added():
    assert competitors.list_custom_queries("brand-with-no-queries") == []
```

Check `tests/conftest.py`'s autouse fixture isolates `SEO_LOCAL_DIR` per test (it does, per this suite's established pattern), so these tests don't need their own brand-id isolation beyond using a distinct id per test where state could otherwise leak within the same test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest seo_geo_agent/tests/test_seo_competitors.py -v -k custom_query`
Expected: FAIL — `AttributeError: module 'seo_geo_agent.competitors' has no attribute 'add_custom_query'`

- [ ] **Step 3: Implement the three functions**

Add to `competitors.py`, near `tracked_keywords()` (same conceptual area — both feed the rank-tracking pool):

```python
MAX_CUSTOM_QUERY_LEN = 200


def list_custom_queries(brand_id: str) -> list[str]:
    doc = state.load(f"custom-queries-{brand_id}")
    return (doc or {}).get("queries", [])


def add_custom_query(brand_id: str, query: str) -> list[str]:
    query = query.strip()
    if not query:
        raise ValueError("Query must not be empty")
    if len(query) > MAX_CUSTOM_QUERY_LEN:
        raise ValueError(f"Query must be {MAX_CUSTOM_QUERY_LEN} characters or fewer")
    existing = list_custom_queries(brand_id)
    if query.lower() not in [q.lower() for q in existing]:
        existing = existing + [query]
    state.save(f"custom-queries-{brand_id}", {"queries": existing})
    return existing


def remove_custom_query(brand_id: str, query: str) -> list[str]:
    query = query.strip().lower()
    remaining = [q for q in list_custom_queries(brand_id) if q.lower() != query]
    state.save(f"custom-queries-{brand_id}", {"queries": remaining})
    return remaining
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest seo_geo_agent/tests/test_seo_competitors.py -v -k custom_query`
Expected: PASS

- [ ] **Step 5: Run full backend suite**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/competitors.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_competitors.py"
git commit -m "feat: add per-brand custom rank-tracking query persistence"
```

---

## Task 3: Widen the rank-tracking pool, switch to real Serper

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/competitors.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_competitors.py` and/or `test_seo_lab.py` (whichever the existing `rank_snapshot`/`tracked_keywords` tests live in — confirmed by prior research to be `test_seo_lab.py`; add pool-combination tests there to sit beside the existing `test_rank_snapshot_and_shifts`)

**Interfaces:**
- Consumes: `list_custom_queries` (Task 2), `tracked_keywords` (existing, unchanged), `keyword_pool.latest` (existing sibling module — verify its exact per-keyword item shape before using it, see Step 1), `sources.brand_rank_search`/`brand_rank_available` (Task 1).
- Produces: `rank_tracking_pool(brand: dict) -> list[str]`, `MAX_RANK_POOL = 50` (module constant) — Task 4's GET route reads both.

- [ ] **Step 1: Verify `keyword_pool.latest()`'s real per-keyword field names**

Run: `grep -n "def build\|\"keyword\"\|\"opportunity\"" "backend/agents/SEO GEO agent/seo_geo_agent/keyword_pool.py"` and read the surrounding lines. Confirm the exact key names used for a keyword's text and its opportunity score inside each item of the `"keywords"` list `keyword_pool.latest(brand_id)` returns. The plan assumes `item["keyword"]` and `item.get("opportunity", 0)` — if the real names differ (e.g. `"term"` instead of `"keyword"`, or the opportunity field is nested), use the real ones in Step 3 below.

- [ ] **Step 2: Write the failing tests**

```python
# add to test_seo_lab.py, beside the existing rank_snapshot tests
def test_rank_tracking_pool_prioritizes_custom_then_auto_then_pool_terms(monkeypatch):
    brand = {**BRAND, "id": "pool-brand", "seeds": ["seed one"]}
    competitors.add_custom_query("pool-brand", "custom query one")
    # tracked_keywords() will pull "seed one" as the sole auto-derived term
    # (no keyword-lab clusters seeded for this brand id, so heads == [])
    pool = competitors.rank_tracking_pool(brand)
    assert pool[0].lower() == "custom query one"  # custom first
    assert "seed one" in pool


def test_rank_tracking_pool_deduplicates_and_caps_at_fifty(monkeypatch):
    brand = {**BRAND, "id": "cap-brand", "seeds": []}
    for i in range(60):
        competitors.add_custom_query("cap-brand", f"query {i}")
    pool = competitors.rank_tracking_pool(brand)
    assert len(pool) == competitors.MAX_RANK_POOL
    assert pool[0] == "query 0"  # first-added custom queries survive the cap


def test_rank_snapshot_uses_brand_rank_search_not_serper_search(monkeypatch):
    calls = {"brand_rank": 0, "serper": 0}

    def fake_brand_rank(q, client=None):
        calls["brand_rank"] += 1
        return serp_with(1)(q)

    def fake_serper(q, client=None):
        calls["serper"] += 1
        return serp_with(1)(q)

    monkeypatch.setattr(sources, "brand_rank_available", lambda: True)
    monkeypatch.setattr(sources, "brand_rank_search", fake_brand_rank)
    monkeypatch.setattr(sources, "serper_search", fake_serper)

    competitors.rank_snapshot({**BRAND, "id": "provider-check"})

    assert calls["brand_rank"] > 0
    assert calls["serper"] == 0
```

Adjust the `serp_with(1)(q)` call style to match however `test_seo_lab.py`'s real `serp_with` helper is actually invoked (confirmed earlier as `search=lambda q: serp_with(5)` — a factory returning a lambda, not a direct call — match that exact shape rather than what's guessed above).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest seo_geo_agent/tests/test_seo_lab.py -v -k rank_tracking_pool`
Expected: FAIL — `AttributeError: module 'seo_geo_agent.competitors' has no attribute 'rank_tracking_pool'`

- [ ] **Step 4: Implement `rank_tracking_pool` and switch `rank_snapshot`**

Add near `tracked_keywords()`:

```python
MAX_RANK_POOL = 50


def rank_tracking_pool(brand: dict) -> list[str]:
    """Custom queries first (explicit user intent), then the existing
    auto-derived seeds+cluster-heads, then keyword_pool's top-opportunity
    terms — deduplicated case-insensitively, capped at MAX_RANK_POOL so a
    brand with a big keyword pool can't blow the Serper budget on one click."""
    from . import keyword_pool as kw_pool

    custom = list_custom_queries(brand["id"])
    auto = tracked_keywords(brand)
    pool_doc = kw_pool.latest(brand["id"]) or {}
    pool_items = sorted(
        pool_doc.get("keywords", []), key=lambda k: k.get("opportunity", 0), reverse=True
    )
    pool_terms = [k["keyword"] for k in pool_items if k.get("keyword")]

    out: list[str] = []
    for kw in custom + auto + pool_terms:
        if kw.lower() not in [o.lower() for o in out]:
            out.append(kw)
    return out[:MAX_RANK_POOL]
```

(Use the real field names from Step 1 if they differ from `"keyword"`/`"opportunity"`.)

Modify `rank_snapshot()` (currently lines ~40-46, confirm before editing):

```python
def rank_snapshot(brand: dict, search=None) -> dict:
    """Record where we rank today for every tracked keyword, and which domains
    keep showing up above us (competitor discovery)."""
    if search is None:
        if not sources.brand_rank_available():
            raise CredentialMissing("Serper key missing — rank tracking needs live SERPs")
        search = sources.brand_rank_search
    ranks: dict[str, dict] = {}
    seen_domains: dict[str, int] = {}
    for kw in rank_tracking_pool(brand):
        # ...rest of the function body is UNCHANGED from here down...
```

Only the `if search is None:` block's two lines and the loop's iterable (`tracked_keywords(brand)` → `rank_tracking_pool(brand)`) change — everything else in the function body stays exactly as it is today.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest seo_geo_agent/tests/test_seo_lab.py -v -k "rank_tracking_pool or rank_snapshot"`
Expected: PASS

- [ ] **Step 6: Run full backend suite**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest -v`
Expected: PASS — specifically confirm `test_rank_snapshot_and_shifts` (the existing test, unmodified) still passes, since `rank_snapshot`'s default-`search=None` path changed providers but the test injects its own `search=` lambda, bypassing that branch entirely.

- [ ] **Step 7: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/competitors.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_seo_lab.py"
git commit -m "feat: widen rank-tracking pool to 50 (custom + auto + keyword-pool terms), switch to real Serper"
```

---

## Task 4: Backend routes — custom queries + pool visibility

**Files:**
- Modify: `backend/app/routers/seo_geo.py`
- Test: whichever HTTP-level test file already covers this router (per a prior session's work, `test_seo_geo.py` under `backend/agents/SEO GEO agent/seo_geo_agent/tests/` has the first HTTP-level `TestClient` setup for this router — reuse its `client`/`auth_headers` fixtures)

**Interfaces:**
- Consumes: `competitors.add_custom_query`/`remove_custom_query`/`list_custom_queries`/`rank_tracking_pool`/`MAX_RANK_POOL` (Tasks 2-3).
- Produces: `POST /api/seo-geo/competitors/{brand_id}/custom-queries` → `{"custom_queries": [...]}`; `DELETE` same path → `{"custom_queries": [...]}`; the existing `GET /api/seo-geo/competitors/{brand_id}` response gains `"custom_queries": [...]`, `"pool_size": int`, `"pool_cap": int`.

- [ ] **Step 1: Confirm current route code**

Run: `grep -n "def get_competitors\|def track_competitors\|class CompetitorsIn" backend/app/routers/seo_geo.py` and read the surrounding ~20 lines of each to confirm they're still shaped as this plan assumes.

- [ ] **Step 2: Write the failing tests**

```python
# added to the existing HTTP-level test file (e.g. test_seo_geo.py)
def test_add_and_remove_custom_query(client, auth_headers, seeded_brand):
    resp = client.post(
        f"/api/seo-geo/competitors/{seeded_brand['id']}/custom-queries",
        json={"query": "clat coaching jaipur"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "clat coaching jaipur" in resp.json()["custom_queries"]

    resp2 = client.request(
        "DELETE",
        f"/api/seo-geo/competitors/{seeded_brand['id']}/custom-queries",
        json={"query": "clat coaching jaipur"},
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["custom_queries"] == []


def test_add_custom_query_rejects_empty(client, auth_headers, seeded_brand):
    resp = client.post(
        f"/api/seo-geo/competitors/{seeded_brand['id']}/custom-queries",
        json={"query": "   "},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_get_competitors_reports_pool_size_and_cap(client, auth_headers, seeded_brand):
    resp = client.get(f"/api/seo-geo/competitors/{seeded_brand['id']}", headers=auth_headers)
    body = resp.json()
    assert "pool_size" in body and "pool_cap" in body
    assert body["pool_cap"] == 50
    assert "custom_queries" in body
```

Match `client`/`auth_headers`/`seeded_brand` (or whatever the real fixture names turn out to be — confirm by reading an existing passing test in the same file first) exactly rather than inventing new ones.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest <test-file-path> -k custom_quer -v`
Expected: FAIL with 404 (routes don't exist yet)

- [ ] **Step 4: Add the routes and extend the GET response**

Add a payload model near `CompetitorsIn`:

```python
class CustomQueryIn(BaseModel):
    query: str
```

Add the two routes near the existing competitor routes (`get_competitors`/`set_competitors`/`track_competitors`):

```python
@router.post("/seo-geo/competitors/{brand_id}/custom-queries")
def add_custom_query(brand_id: str, payload: CustomQueryIn, user=Depends(get_current_user),
                     act: Activity = trail.records("custom_query_added", "Added a custom rank-tracking query")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    try:
        queries = seo_competitors.add_custom_query(brand_id, payload.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    act.note(f"Added query “{payload.query.strip()}” ({len(queries)} custom queries total)")
    return {"custom_queries": queries}


@router.delete("/seo-geo/competitors/{brand_id}/custom-queries")
def remove_custom_query(brand_id: str, payload: CustomQueryIn, user=Depends(get_current_user),
                        act: Activity = trail.records("custom_query_removed", "Removed a custom rank-tracking query")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    queries = seo_competitors.remove_custom_query(brand_id, payload.query)
    act.note(f"Removed query “{payload.query.strip()}” ({len(queries)} custom queries remain)")
    return {"custom_queries": queries}
```

Modify the existing `get_competitors` route to add the three new fields:

```python
@router.get("/seo-geo/competitors/{brand_id}")
def get_competitors(brand_id: str, user=Depends(get_current_user)):
    brand = _brand_or_404(brand_id)
    ranks_doc = seo_state.load(f"ranks-{brand_id}") or {}
    sitemap_doc = seo_state.load(f"sitemaps-{brand_id}") or {}
    return {
        "tracked": brand.get("competitors", []),
        "suggested": ranks_doc.get("suggested_competitors", []),
        "shifts": seo_competitors.rank_shifts(brand_id),
        "feed": sitemap_doc.get("last_feed", {}),
        "custom_queries": seo_competitors.list_custom_queries(brand_id),
        "pool_size": len(seo_competitors.rank_tracking_pool(brand)),
        "pool_cap": seo_competitors.MAX_RANK_POOL,
    }
```

Function name note: this route handler is named `add_custom_query`/`remove_custom_query`, which shadows the `seo_competitors.add_custom_query`/`remove_custom_query` module functions by name (not by reference, since one is called via `seo_competitors.` prefix) — this matches the existing file's own pattern (e.g. `refresh_keyword_pool` the route vs functions it calls) and is not a real collision, but keep the `seo_competitors.` prefix on every call to the module function so Python doesn't get confused between the route function's own name and the import.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest <test-file-path> -k custom_quer -v`
Expected: PASS

- [ ] **Step 6: Run full backend suite**

Run: `cd "backend/agents/SEO GEO agent" && python3 -m pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/seo_geo.py <test-file-path>
git commit -m "feat: add custom-query routes, surface pool_size/pool_cap on GET competitors"
```

---

## Task 5: Frontend API client

**Files:**
- Modify: `frontend/lib/api.ts`
- Test: a new small test file `frontend/lib/api.customqueries.test.ts` (following the naming convention of `frontend/lib/api.priorities.test.ts` from a prior task)

**Interfaces:**
- Consumes: `getJson`/`postJson`/`request` (existing module-private helpers).
- Produces: extends `SeoCompetitors` with `custom_queries: string[]`, `pool_size: number`, `pool_cap: number`; adds `seoAddCustomQuery(brandId: string, query: string): Promise<{custom_queries: string[]}>` and `seoRemoveCustomQuery(brandId: string, query: string): Promise<{custom_queries: string[]}>`. Task 6 imports both plus the extended type.

- [ ] **Step 1: Find the real DELETE-with-body pattern already in this file**

Run: `grep -n "method: \"DELETE\"\|seoDeleteBrand" frontend/lib/api.ts` and read the matched function in full — this codebase already has at least one DELETE call (`seoDeleteBrand`, per a prior session's research). Match its exact pattern (whether it uses `request()` directly, a body, headers) rather than inventing a new DELETE style.

- [ ] **Step 2: Write the failing tests**

```typescript
// frontend/lib/api.customqueries.test.ts
import { describe, expect, it, vi, afterEach } from "vitest";

describe("custom rank-tracking queries", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("adds a custom query", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ custom_queries: ["clat coaching jaipur"] }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
    const { seoAddCustomQuery } = await import("./api");
    const result = await seoAddCustomQuery("b1", "clat coaching jaipur");
    expect(result.custom_queries).toContain("clat coaching jaipur");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/seo-geo/competitors/b1/custom-queries"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("removes a custom query", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ custom_queries: [] }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
    const { seoRemoveCustomQuery } = await import("./api");
    const result = await seoRemoveCustomQuery("b1", "clat coaching jaipur");
    expect(result.custom_queries).toEqual([]);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/seo-geo/competitors/b1/custom-queries"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run api.customqueries.test.ts`
Expected: FAIL — functions not exported.

- [ ] **Step 4: Extend the type and add the two functions**

Find `export interface SeoCompetitors { ... }` and add the three new fields:

```typescript
export interface SeoCompetitors {
  tracked: string[];
  suggested: string[];
  shifts: SeoRankShift[];
  feed: Record<string, SeoSitemapEntry>;
  custom_queries: string[];
  pool_size: number;
  pool_cap: number;
}
```

Near `seoTrackCompetitors`/`seoSetCompetitors`, add:

```typescript
export const seoAddCustomQuery = (brandId: string, query: string) =>
  postJson<{ custom_queries: string[] }>(`/api/seo-geo/competitors/${brandId}/custom-queries`, { query });
```

For `seoRemoveCustomQuery`, match whatever DELETE pattern Step 1 found in `seoDeleteBrand` — write it in that same style rather than the guess below if it differs:

```typescript
export async function seoRemoveCustomQuery(brandId: string, query: string): Promise<{ custom_queries: string[] }> {
  const res = await request(`/api/seo-geo/competitors/${brandId}/custom-queries`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  return res.json();
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run api.customqueries.test.ts`
Expected: PASS

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no new errors (this will also catch any other place `SeoCompetitors` is constructed/destructured that now needs the three new fields — fix any such call site to match, e.g. a test fixture elsewhere that builds a `SeoCompetitors` object literal)

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/api.customqueries.test.ts
git commit -m "feat: add seoAddCustomQuery/seoRemoveCustomQuery API client functions"
```

---

## Task 6: `CompetitorsView` — custom-query UI + pool indicator

**Files:**
- Modify: `frontend/components/console/seo/labs.tsx`
- Test: `frontend/components/console/seo/labs.test.tsx` (create — no existing test file for this component per prior research; follow the testing-library conventions established in `dashboard.test.tsx`/`shell.test.tsx`/`insights.test.tsx`)

**Interfaces:**
- Consumes: `seoAddCustomQuery`, `seoRemoveCustomQuery`, extended `SeoCompetitors` type (Task 5).
- Produces: no new exports — this is the UI integration point.

- [ ] **Step 1: Re-read the current `CompetitorsView` in full**

Read `frontend/components/console/seo/labs.tsx` lines ~238-498 (may have shifted) to confirm the exact current state/effects/render structure before editing — specifically the `data` state shape usage and where the "Check now" button and the tracked/suggested competitor chips render (lines ~430-479 per prior research), since the new UI goes near there.

- [ ] **Step 2: Write the failing test**

```typescript
// frontend/components/console/seo/labs.test.tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CompetitorsView } from "./labs";
import * as api from "@/lib/api";

describe("CompetitorsView custom queries", () => {
  beforeEach(() => {
    vi.spyOn(api, "seoCompetitors").mockResolvedValue({
      tracked: [], suggested: [], shifts: [], feed: {},
      custom_queries: ["existing query"], pool_size: 16, pool_cap: 50,
    });
    vi.spyOn(api, "seoCompetitorProfiles").mockResolvedValue({ profiles: null });
  });

  it("shows the pool size against the cap", async () => {
    render(<CompetitorsView brandId="b1" isCreator={true} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/16.*50/)).toBeInTheDocument());
  });

  it("shows existing custom queries and adds a new one", async () => {
    const addSpy = vi.spyOn(api, "seoAddCustomQuery").mockResolvedValue({
      custom_queries: ["existing query", "new query"],
    });
    render(<CompetitorsView brandId="b1" isCreator={true} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("existing query")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/add a query/i), { target: { value: "new query" } });
    fireEvent.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() => expect(addSpy).toHaveBeenCalledWith("b1", "new query"));
  });
});
```

Check `frontend/vitest.config.mts` (added by a prior task) and an existing `.test.tsx` file for the exact React-testing-library setup already in place before assuming this renders correctly — match established conventions.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run labs.test.tsx`
Expected: FAIL — no pool-size text, no add-query input rendered yet.

- [ ] **Step 4: Implement the UI**

Add state near the component's existing state (line ~241-248):

```tsx
  const [newQuery, setNewQuery] = useState("");
  const [queryBusy, setQueryBusy] = useState(false);
```

Add handlers near `track`/`toggleTracked`:

```tsx
  async function addQuery() {
    const query = newQuery.trim();
    if (!query) return;
    setQueryBusy(true);
    try {
      const res = await seoAddCustomQuery(brandId, query);
      setData((d) => d && { ...d, custom_queries: res.custom_queries });
      setNewQuery("");
    } catch (e) {
      onToast(errMsg(e, "Could not add query"), "error");
    } finally {
      setQueryBusy(false);
    }
  }

  async function removeQuery(query: string) {
    try {
      const res = await seoRemoveCustomQuery(brandId, query);
      setData((d) => d && { ...d, custom_queries: res.custom_queries });
    } catch (e) {
      onToast(errMsg(e, "Could not remove query"), "error");
    }
  }
```

Add the render block near the "Check now" button (before or after it — implementer's call on the most natural placement given the surrounding JSX read in Step 1):

```tsx
        <div className="seo-comp__queries">
          <div className="seo-lab__meta">
            Tracking {data?.pool_size ?? 0} of {data?.pool_cap ?? 50} possible queries
          </div>
          <div className="seo-comp__query-add">
            <input
              className="seo-input"
              placeholder="Add a query to track…"
              value={newQuery}
              onChange={(e) => setNewQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void addQuery()}
            />
            <button className="seo-btn" disabled={queryBusy || !newQuery.trim()} onClick={() => void addQuery()}>
              Add
            </button>
          </div>
          {!!data?.custom_queries.length && (
            <div className="seo-comp__query-list">
              {data.custom_queries.map((q) => (
                <span key={q} className="seo-chip seo-chip--removable">
                  {q}
                  <button
                    className="seo-chip__remove"
                    aria-label={`Remove ${q}`}
                    onClick={() => void removeQuery(q)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
```

Add the two new imports to this file's existing `@/lib/api` import list: `seoAddCustomQuery`, `seoRemoveCustomQuery`.

Add minimal CSS for the three new classes (`seo-comp__queries`, `seo-comp__query-add`, `seo-comp__query-list`, `seo-chip--removable`, `seo-chip__remove`) to `frontend/app/seo.css`, matching the existing `.seo-chip`/`.seo-input`/`.seo-btn` visual language already in that file (read their current rules first, don't invent a new visual style).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run labs.test.tsx`
Expected: PASS

- [ ] **Step 6: Typecheck and full frontend suite**

Run: `cd frontend && npm run typecheck && npm run test`
Expected: clean, all pass

- [ ] **Step 7: Commit**

```bash
git add frontend/components/console/seo/labs.tsx frontend/components/console/seo/labs.test.tsx frontend/app/seo.css
git commit -m "feat: custom rank-tracking query management + pool-size indicator in Competitors"
```

---

## Task 7: Deploy + verify

**Files:** none (deployment + verification only)

- [ ] **Step 1: Deploy backend**

```bash
cd "/home/vishal/Documents/lpt seo agent"
gcloud run deploy seo-agent-backend --source backend --region asia-south1 --project lpt-seo-agent --service-account seo-agent-backend@lpt-seo-agent.iam.gserviceaccount.com --quiet
```

- [ ] **Step 2: Confirm `SEO_SERPER_API_KEY` is mounted on the backend service**

Run: `gcloud run services describe seo-agent-backend --region asia-south1 --project lpt-seo-agent --format="value(spec.template.spec.containers[0].env)" | grep -i serper`

If it's not already mounted (it may not be, since the key was stored in Secret Manager during an earlier session turn but never attached to the running service), mount it:

```bash
gcloud run services update seo-agent-backend --region asia-south1 --project lpt-seo-agent --update-secrets SEO_SERPER_API_KEY=SEO_SERPER_API_KEY:latest --quiet
```

- [ ] **Step 3: Deploy frontend**

```bash
cd "/home/vishal/Documents/lpt seo agent"
gcloud run deploy seo-agent-frontend --source frontend --region asia-south1 --project lpt-seo-agent --service-account seo-agent-frontend@lpt-seo-agent.iam.gserviceaccount.com --set-env-vars BACKEND_ORIGIN=https://seo-agent-backend-432448138006.asia-south1.run.app --memory 512Mi --cpu 1 --concurrency 80 --quiet
```

- [ ] **Step 4: Check logs for errors**

```bash
gcloud logging read 'resource.type=cloud_run_revision AND (resource.labels.service_name=seo-agent-backend OR resource.labels.service_name=seo-agent-frontend) AND severity>=ERROR' --project lpt-seo-agent --limit 20 --freshness=10m
```

- [ ] **Step 5: Manual browser smoke test**

Open the frontend, sign in, open a brand's Competitors section: add a custom query, confirm it appears in the list and the pool-size counter updates, click "Check now," confirm the shift list and `keywords_won` reflect the wider pool (more rows than the old 15-keyword cap, if the brand has enough seeds/pool terms to exceed 15).

---

## Self-review notes

- **Spec coverage:** §1 (real Serper backend → Task 1), §2 (custom-query persistence → Task 2), §3 (widened + capped pool, provider switch → Task 3), §4 (routes → Task 4), §5 (frontend UI → Tasks 5-6), §6 (hard cap in backend code → Task 3's `[:MAX_RANK_POOL]`), §7 (testing → embedded per task) all covered.
- **Known open verifications, called out explicitly:** `keyword_pool.latest()`'s real per-keyword field names (Task 3 Step 1), the real DELETE-with-body pattern already in `api.ts` via `seoDeleteBrand` (Task 5 Step 1), and whether `CompetitorsView`'s exact current line numbers still match (Task 6 Step 1) — each is a "check first, use the real thing" instruction, not an unresolved design question.
- **Type consistency:** `rank_tracking_pool(brand: dict) -> list[str]` (Task 3) is consumed by Task 4's route with the same signature; `MAX_RANK_POOL` (Task 3) is read directly by Task 4's route (`seo_competitors.MAX_RANK_POOL`); `SeoCompetitors`'s three new fields (Task 5) match exactly what Task 4's route adds to the GET response (`custom_queries`, `pool_size`, `pool_cap`); `seoAddCustomQuery`/`seoRemoveCustomQuery` (Task 5) match what Task 6 imports and calls.
