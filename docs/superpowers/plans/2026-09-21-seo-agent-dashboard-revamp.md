# SEO Agent Dashboard Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the CrUX/vitals and keyword-pool bugs, add a cross-source "Insights" priority list, and restructure the SEO console from one long scrolling accordion page into a sidebar-navigated dashboard with a full visual redesign.

**Architecture:** Two independent backend fixes/additions (vitals origin retry, new `priorities.py` synthesis module + endpoint) land first and are fully testable against the existing pytest offline harness without touching the frontend. The frontend then gets a new `Shell`/`Sidebar` container that replaces the current single `<div className="mr-panel">` + nine stacked `<Fold>` sections; each existing section's *content* component (`DeepAuditPanel`, `CompetitorsView`, `VitalsView`, `KeywordPoolView`, etc.) is reused unchanged inside the new container — only the wrapping/navigation and CSS are new. The new Insights page is the only genuinely new frontend view; it consumes the new priorities endpoint. Visual redesign is applied via the existing token system (`tokens/*.css`) plus updated component classes, not a parallel styling system.

**Tech Stack:** FastAPI + Firestore/local-JSON (`state.py`) backend, Next.js 15 + React 19 frontend, existing `tokens/*.css` design-token system, vitest for frontend tests, pytest for backend tests.

**Spec:** `docs/superpowers/specs/2026-09-21-seo-agent-dashboard-revamp-design.md`

## Global Constraints

- Every backend data-source module in this package degrades instead of raising past its own boundary: wrap optional sources in `try/except CredentialMissing as exc`, append a note, never let one missing source fail the whole document. (spec §1, §2; codebase convention confirmed in `insights.py`)
- New backend modules follow the existing pattern exactly: `"""docstring"""` then `from __future__ import annotations`, a private `_DOC = "<name>-{}"` format-string constant, `available() -> bool`, `build(...) -> dict` that saves and returns the doc, `latest(brand_id: str) -> dict | None` that loads it. (research: `vitals.py` conventions)
- Sibling modules are read through their own `latest()`/`latest_profiles()` accessor, never via `state.load` with a hand-built doc id from outside that module. (research: `insights._competitor_topic_pool` convention)
- New frontend API functions match the existing shape exactly: `export const seoXxx = (id: string, opts?: RequestOptions) => getJson<{ xxx: XxxDoc | null }>(\`/api/seo-geo/xxx/${id}\`, opts);` for GET, `export const seoXxxRefresh = (id: string) => postJson<{ xxx: XxxDoc }>(\`/api/seo-geo/xxx/${id}/refresh\`, {});` for POST. (research item 6)
- No auto-triggering of paid/LLM-cost operations (Keyword Lab) — missing data always surfaces as an explicit, actionable empty state, never a silent background job. (spec §1)
- The old "This week" hero and "Fix list" fold are removed once Insights ships — do not leave two competing "what to do" surfaces. (spec §2)
- Backend tests run under the existing offline harness (`conftest.py` sets `SEO_OFFLINE=1`, isolates `SEO_LOCAL_DIR` to a tmp dir) — never touch real Firestore or paid APIs in a test.
- Run backend tests with `backend/.venv/Scripts/python -m pytest` (or the project's configured interpreter) per the repo's verification bar; run frontend tests with `npm run typecheck && npm run test` inside `frontend/`.

---

## Task 1: Fix CrUX origin normalization bug

**Files:**
- Modify: `backend/agents/SEO GEO agent/seo_geo_agent/vitals.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_vitals.py` (create if it does not already exist — check first with `ls backend/agents/"SEO GEO agent"/seo_geo_agent/tests/test_vitals*.py`; if a vitals test file already exists, add these tests to it instead of creating a new one)

**Interfaces:**
- Consumes: nothing new — this task only changes `vitals.py` internals.
- Produces: `vitals.build(brand, ...)` behavior changes (same signature, same return shape, but the persisted doc gains one new field: `"origin_tried": list[str]` recording every origin form attempted, and `"origin_used": str | None` recording which one (if any) returned data). Later tasks (Task 3) may read `vitals.latest(brand_id)` and can rely on the doc still having its existing fields (`origin`, `notes`, per-form-factor slices) plus these two new ones.

- [ ] **Step 1: Read the current implementation to get exact line numbers**

Run: `grep -n "def build\|def _query_origin\|origin = f\|CrUX request failed\|no field data" "backend/agents/SEO GEO agent/seo_geo_agent/vitals.py"`

Confirm the line `origin = f"https://{brand['domain']}"` (reported at vitals.py:137) and the note-building block around vitals.py:147-155 are still at those locations before editing — line numbers may have shifted since this plan was written.

- [ ] **Step 2: Write the failing test**

```python
# backend/agents/SEO GEO agent/seo_geo_agent/tests/test_vitals.py
"""Vitals module tests — CrUX origin retry and degradation notes."""
from __future__ import annotations

from unittest.mock import patch

from seo_geo_agent import vitals


def _brand(domain: str = "lawpreptutorial.com") -> dict:
    return {"id": "b1", "domain": domain}


def test_origin_retry_tries_www_variant_when_bare_domain_has_no_data(monkeypatch):
    """A bare-domain 404 must fall back to the www. form before giving up."""
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    calls: list[str] = []

    def fake_query(origin: str, form_factor: str, api_key: str):
        calls.append(origin)
        if origin == "https://www.lawpreptutorial.com":
            return {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": 2000}}}}
        return None  # bare domain: no CrUX record

    with patch.object(vitals, "_query_crux", side_effect=fake_query):
        doc = vitals.build(_brand(), today=None)

    assert calls == [
        "https://lawpreptutorial.com",
        "https://www.lawpreptutorial.com",
    ] or calls == [
        "https://lawpreptutorial.com",
        "https://www.lawpreptutorial.com",
        "https://lawpreptutorial.com",
        "https://www.lawpreptutorial.com",
    ]  # once per form factor (mobile, desktop) if both are queried
    assert doc["origin_used"] == "https://www.lawpreptutorial.com"
    assert "https://www.lawpreptutorial.com" in doc["origin_tried"]


def test_origin_retry_records_no_data_when_neither_form_has_records(monkeypatch):
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    with patch.object(vitals, "_query_crux", return_value=None):
        doc = vitals.build(_brand(), today=None)

    assert doc["origin_used"] is None
    assert any("no field data" in n.lower() for n in doc["notes"])


def test_request_failure_note_is_distinct_from_no_data_note(monkeypatch):
    """A real exception must not be worded identically to 'not enough traffic yet'."""
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    def boom(origin, form_factor, api_key):
        raise TimeoutError("connect timed out")

    with patch.object(vitals, "_query_crux", side_effect=boom):
        doc = vitals.build(_brand(), today=None)

    failure_notes = [n for n in doc["notes"] if "request failed" in n.lower()]
    no_data_notes = [n for n in doc["notes"] if "no field data" in n.lower()]
    assert failure_notes and not no_data_notes
    assert "TimeoutError" in failure_notes[0]
```

Adjust the mock target name (`vitals._query_crux`) and the `build()` call signature/kwargs to match whatever the real private query function and `build()` parameters are actually named in the file you read in Step 1 — if the existing function is named differently (e.g. `_crux_query`, `_fetch`), use that exact name in the `patch.object` calls, and if `build()` takes a different keyword than `today=None`, match its real signature. The test *behavior* being pinned (origin fallback order, `origin_used`/`origin_tried` fields, distinct wording for failure vs. no-data) is what must not change, not these exact names.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest seo_geo_agent/tests/test_vitals.py -v`
Expected: FAIL (either `AttributeError` on `origin_used`/`origin_tried` not existing, or the mock target not found, or wrong call count) — confirms the current code does not retry.

- [ ] **Step 4: Implement the origin-normalization fallback**

Edit `vitals.py` around the `origin = f"https://{brand['domain']}"` line. Replace the single-origin build with a small helper that tries the bare domain, then the opposite `www.`/bare form, and records both what was tried and what worked:

```python
def _origin_candidates(domain: str) -> list[str]:
    """Bare domain first, then the other www/bare form — CrUX needs an exact
    origin match and this app does not know which form the site's real
    traffic is recorded under."""
    bare = domain[4:] if domain.startswith("www.") else domain
    www = f"www.{bare}"
    ordered = [bare, www] if domain == bare else [domain, bare]
    seen: list[str] = []
    for d in ordered:
        candidate = f"https://{d}"
        if candidate not in seen:
            seen.append(candidate)
    return seen
```

Then, wherever the per-form-factor CrUX query currently runs against the single hardcoded `origin`, loop over `_origin_candidates(brand["domain"])` and stop at the first one that returns data:

```python
origins = _origin_candidates(brand["domain"])
origin_tried: list[str] = []
origin_used: str | None = None
result = None
last_exc: Exception | None = None
for candidate in origins:
    origin_tried.append(candidate)
    try:
        result = _query_crux(candidate, form_factor, api_key)  # match the real function name from Step 1
    except Exception as exc:  # noqa: BLE001 — recorded below, not swallowed silently
        last_exc = exc
        continue
    if result:
        origin_used = candidate
        break
```

Use whichever loop variable/form-factor structure the existing code already has (it likely loops over `("PHONE", "DESKTOP")` or similar) — nest this origin-retry loop *inside* the existing per-form-factor loop, not the other way around, so each form factor independently finds its best origin.

At the point where the existing code currently builds the "no field data yet" note (around the old line 150-155), change it to only fire when `origin_used is None and last_exc is None`:

```python
if origin_used is None and last_exc is None:
    notes.append(
        f"{tag}: Chrome has no field data for this origin yet — it needs enough "
        "traffic to report anonymously."
    )
elif origin_used is None and last_exc is not None:
    notes.append(f"{tag}: CrUX request failed ({type(last_exc).__name__}: {last_exc})")
```

Finally, add the two new fields to the returned/saved doc:

```python
doc = {
    # ...all existing fields...
    "origin_tried": origin_tried,
    "origin_used": origin_used,
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest seo_geo_agent/tests/test_vitals.py -v`
Expected: PASS

- [ ] **Step 6: Run the full existing test suite to check for regressions**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest -v`
Expected: PASS (no existing test should reference the old single-origin behavior, but confirm — if `test_deep_audit.py` or another file has a vitals test asserting the old note wording, update it to match the new distinct wording from Step 4)

- [ ] **Step 7: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/vitals.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_vitals.py"
git commit -m "fix: retry CrUX origin as www/bare fallback, distinguish failure from no-data"
```

---

## Task 2: Keyword pool empty-state CTAs

**Files:**
- Modify: `frontend/components/console/seo/dashboard.tsx` (`KeywordPoolView`, dashboard.tsx:276+)
- Test: `frontend/components/console/seo/dashboard.test.tsx` (create — no existing test file for this component per the research; follow the style of `frontend/lib/*.test.ts`)

**Interfaces:**
- Consumes: `SeoKeywordPoolDoc` type from `@/lib/api` (existing); `KeywordPoolView`'s existing props (`brandId: string; doc: SeoKeywordPoolDoc | null; onLoaded: (d) => void; onToast: ToastFn`) — unchanged.
- Produces: `KeywordPoolView` now also needs to know whether GSC is connected and whether keyword-lab has ever run, to decide which empty-state CTA(s) to show. Add two new optional props: `gscConnected?: boolean` and `keywordLabRun?: boolean`. `SeoAgent.tsx` (Task 8) will pass these from data it already fetches (`gsc` from `seoBrandDetail`, and the keyword-lab doc it already loads for the "More tools" → Keyword lab view).

- [ ] **Step 1: Read the current `KeywordPoolView` implementation**

Run: `sed -n '271,403p' "frontend/components/console/seo/dashboard.tsx"` to see the exact current rendering (the research report has this, but line numbers may have shifted — re-check before editing).

- [ ] **Step 2: Write the failing test**

```typescript
// frontend/components/console/seo/dashboard.test.tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { KeywordPoolView } from "./dashboard";

describe("KeywordPoolView empty states", () => {
  it("shows a Connect Search Console CTA when GSC is not connected and the pool is thin", () => {
    render(
      <KeywordPoolView
        brandId="b1"
        doc={{ at: "", keywords: [], totals: { keywords: 2 }, bands: {}, clusters: [], notes: [] } as any}
        gscConnected={false}
        keywordLabRun={false}
        onLoaded={vi.fn()}
        onToast={vi.fn()}
      />,
    );
    expect(screen.getByText(/connect search console/i)).toBeInTheDocument();
    expect(screen.getByText(/run keyword lab/i)).toBeInTheDocument();
  });

  it("does not show the Search Console CTA once GSC is connected", () => {
    render(
      <KeywordPoolView
        brandId="b1"
        doc={{ at: "", keywords: [], totals: { keywords: 40 }, bands: {}, clusters: [], notes: [] } as any}
        gscConnected={true}
        keywordLabRun={false}
        onLoaded={vi.fn()}
        onToast={vi.fn()}
      />,
    );
    expect(screen.queryByText(/connect search console/i)).not.toBeInTheDocument();
    expect(screen.getByText(/run keyword lab/i)).toBeInTheDocument();
  });
});
```

Check `frontend/package.json` and any existing `*.test.tsx` in the repo for which testing-library setup is already wired (the research only found `lib/*.test.ts`, no `.test.tsx` — if `@testing-library/react` is not yet a devDependency, add it: `npm install -D @testing-library/react @testing-library/jest-dom` inside `frontend/`, and check `vitest.config` / `vite.config` for whether a `jsdom` environment is already configured; if not, add `environment: "jsdom"` to the vitest config and add `/// <reference types="vitest/globals" />` or an explicit `import "@testing-library/jest-dom"` per whatever the project's convention turns out to be once you check).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run dashboard.test.tsx`
Expected: FAIL — `gscConnected`/`keywordLabRun` props don't exist yet / no CTA text rendered.

- [ ] **Step 4: Implement the empty-state CTAs**

In `KeywordPoolView`, add the two new optional props to the signature, and near the top of the component's returned JSX (before the existing table), add:

```tsx
export function KeywordPoolView({
  brandId, doc, gscConnected, keywordLabRun, onLoaded, onToast,
}: {
  brandId: string;
  doc: SeoKeywordPoolDoc | null;
  gscConnected?: boolean;
  keywordLabRun?: boolean;
  onLoaded: (d: SeoKeywordPoolDoc) => void;
  onToast: ToastFn;
}) {
  // ...existing state/hooks...

  const missingSources: { label: string; hint: string; cta: string; onClick: () => void }[] = [];
  if (!gscConnected) {
    missingSources.push({
      label: "Search Console not connected",
      hint: "This is usually the largest source of real keyword volume.",
      cta: "Connect Search Console",
      onClick: () => {
        window.location.href = `/api/seo-geo/oauth/start?brand_id=${brandId}`;
      },
    });
  }
  if (!keywordLabRun) {
    missingSources.push({
      label: "Keyword Lab has not been run yet",
      hint: "Clustering adds every mapped keyword to this pool.",
      cta: "Run Keyword Lab",
      onClick: () => onToast("info", "Open “More tools → Keyword lab” and click “Map keywords”."),
    });
  }

  return (
    <div className="seo-pool-wrap">
      {missingSources.length > 0 && (
        <div className="seo-empty seo-empty--sources">
          {missingSources.map((s) => (
            <div key={s.label} className="seo-empty__row">
              <div>
                <strong>{s.label}</strong>
                <p className="seo-note">{s.hint}</p>
              </div>
              <button className="seo-btn seo-btn--primary" onClick={s.onClick}>
                {s.cta}
              </button>
            </div>
          ))}
        </div>
      )}
      {/* ...existing table/filters JSX unchanged below... */}
    </div>
  );
}
```

Confirm the real OAuth-start URL and the real "how to trigger keyword lab" navigation once you read `SeoAgent.tsx`'s existing `seoOauthStart`/`connectGsc` call (research item: `SeoAgent.tsx:552-560`) and the keyword-lab tool switcher in `labs.tsx` — use the exact existing route/handler rather than the placeholder URL/toast shown here if they differ.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run dashboard.test.tsx`
Expected: PASS

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no new errors

- [ ] **Step 7: Commit**

```bash
git add frontend/components/console/seo/dashboard.tsx frontend/components/console/seo/dashboard.test.tsx
git commit -m "feat: keyword pool empty-state CTAs for missing GSC connection / keyword lab run"
```

---

## Task 3: New backend `priorities.py` synthesis module

**Files:**
- Create: `backend/agents/SEO GEO agent/seo_geo_agent/priorities.py`
- Test: `backend/agents/SEO GEO agent/seo_geo_agent/tests/test_priorities.py`

**Interfaces:**
- Consumes: `vitals.latest(brand_id) -> dict | None`, `keyword_pool.latest(brand_id) -> dict | None`, `competitors.latest_profiles(brand_id) -> dict | None`, `deep_audit.latest(brand_id) -> dict | None`, `insights.latest_run(brand_id) -> dict | None` — all existing, unchanged.
- Produces: `priorities.build(brand_id: str) -> dict` and `priorities.latest(brand_id: str) -> dict | None`, doc shape:
  ```python
  {
      "brand_id": str,
      "at": str,  # ISO date
      "items": [
          {
              "id": str,           # stable id, e.g. f"{source}-{slug}"
              "title": str,
              "why_it_matters": str,
              "severity": str,     # "critical" | "warning" | "suggestion"
              "source": str,       # "vitals" | "keyword_pool" | "competitors" | "deep_audit" | "insights"
              "action_link": str,  # e.g. "#vitals", "#deep-audit", matching the new sidebar section ids from Task 6
          },
          ...
      ],
      "notes": [str],  # one per source that was unavailable/degraded, same convention as every other module
  }
  ```

- [ ] **Step 1: Read `insights.py` and `vitals.py` once more for the exact degradation idiom**

Run: `sed -n '1,30p;480,590p' "backend/agents/SEO GEO agent/seo_geo_agent/insights.py"` — confirm the `degraded: list[str]` + per-source `try/except CredentialMissing` pattern is still shaped as documented in the Global Constraints section above before writing new code against it.

- [ ] **Step 2: Write the failing test**

```python
# backend/agents/SEO GEO agent/seo_geo_agent/tests/test_priorities.py
"""priorities.py — cross-source synthesis into one ranked action list."""
from __future__ import annotations

from unittest.mock import patch

from seo_geo_agent import priorities


def _brand() -> dict:
    return {"id": "b1", "domain": "lawpreptutorial.com"}


def test_critical_vitals_finding_ranks_above_a_suggestion():
    vitals_doc = {
        "origin_used": "https://www.lawpreptutorial.com",
        "mobile": {"assessment": "poor", "metrics": {"largest_contentful_paint": {"assessment": "poor"}}},
        "notes": [],
    }
    pool_doc = {"totals": {"keywords": 3}, "notes": []}
    with (
        patch("seo_geo_agent.priorities.vitals.latest", return_value=vitals_doc),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=pool_doc),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=None),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=None),
    ):
        doc = priorities.build("b1")

    severities = [item["severity"] for item in doc["items"]]
    assert severities.index("critical") < severities.index("suggestion") if "suggestion" in severities else True
    assert any(item["source"] == "vitals" for item in doc["items"])


def test_every_source_missing_produces_notes_not_an_error():
    with (
        patch("seo_geo_agent.priorities.vitals.latest", return_value=None),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=None),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=None),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=None),
    ):
        doc = priorities.build("b1")

    assert doc["items"] == []
    assert len(doc["notes"]) >= 1


def test_list_is_capped_at_ten_items():
    deep_doc = {
        "landing": {
            "template_faults": [
                {"check": f"issue-{i}", "count": 20, "impact": "high"} for i in range(20)
            ]
        },
        "notes": [],
    }
    with (
        patch("seo_geo_agent.priorities.vitals.latest", return_value=None),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=None),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=deep_doc),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=None),
    ):
        doc = priorities.build("b1")

    assert len(doc["items"]) <= 10


def test_latest_returns_persisted_doc(monkeypatch, tmp_path):
    monkeypatch.setenv("SEO_LOCAL_DIR", str(tmp_path))
    with (
        patch("seo_geo_agent.priorities.vitals.latest", return_value=None),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=None),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=None),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=None),
    ):
        built = priorities.build("b1")
    assert priorities.latest("b1") == built
```

Adjust field names inside the synthetic `vitals_doc`/`deep_doc` fixtures once Step 1 confirms the exact real shapes of `vitals.latest()` and `deep_audit.latest()` (the research report gives the deep-audit shape as `{at, domain, urls, sitemaps, pages_by_type, live_pages, gsc_connected, notes, sitemap{...}, landing{...}, cannibalization{...}, density{...}}` — use `landing`'s real template-fault field names, not the guessed `template_faults`/`impact` above, once you've read `landing_audit.py`'s actual output shape).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest seo_geo_agent/tests/test_priorities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'seo_geo_agent.priorities'`

- [ ] **Step 4: Implement `priorities.py`**

```python
"""Cross-source priority list: the one "what to do next" surface for a brand.

Reads every other data source's already-persisted doc through its own
``latest()``/``latest_profiles()`` accessor — this module never re-derives
another module's data, it only ranks and merges what each one already
decided to report. A source with nothing to say (not configured, no run yet,
genuinely clean) contributes zero items and a note, never an error: this
list must never fail to render because one input source is unavailable.

Replaces the old rank-tracking-only "This week" hero and "Fix list" — those
only ever saw ``insights.latest_run``, so vitals/keyword-pool/competitor/
deep-audit findings never reached a to-do list at all.
"""
from __future__ import annotations

from datetime import date

from . import competitors, deep_audit, insights, keyword_pool, state, vitals

_DOC = "priorities-{}"

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "suggestion": 2}
_MAX_ITEMS = 10


def _from_vitals(brand_id: str) -> tuple[list[dict], str | None]:
    doc = vitals.latest(brand_id)
    if not doc:
        return [], "Core Web Vitals: no report yet"
    items: list[dict] = []
    for form_factor in ("mobile", "desktop"):
        slice_ = doc.get(form_factor) or {}
        if slice_.get("assessment") == "poor":
            items.append({
                "id": f"vitals-{form_factor}",
                "title": f"Core Web Vitals are failing on {form_factor}",
                "why_it_matters": "Real visitors are experiencing slow/unstable pages, which Google uses as a ranking signal.",
                "severity": "critical",
                "source": "vitals",
                "action_link": "#vitals",
            })
    return items, None


def _from_keyword_pool(brand_id: str) -> tuple[list[dict], str | None]:
    doc = keyword_pool.latest(brand_id)
    if not doc:
        return [], "Keyword pool: no report yet"
    count = (doc.get("totals") or {}).get("keywords", 0)
    if count < 10:
        return [{
            "id": "keyword-pool-thin",
            "title": "Keyword pool has very few tracked keywords",
            "why_it_matters": "Connect Search Console and run Keyword Lab to see real opportunity.",
            "severity": "warning",
            "source": "keyword_pool",
            "action_link": "#keywords",
        }], None
    return [], None


def _from_competitors(brand_id: str) -> tuple[list[dict], str | None]:
    doc = competitors.latest_profiles(brand_id)
    if not doc:
        return [], "Competitors: no report yet"
    return [], None


def _from_deep_audit(brand_id: str) -> tuple[list[dict], str | None]:
    doc = deep_audit.latest(brand_id)
    if not doc:
        return [], "Deep audit: no report yet"
    items: list[dict] = []
    landing = doc.get("landing") or {}
    for fault in (landing.get("template_faults") or [])[:5]:
        items.append({
            "id": f"deep-audit-{fault.get('check', 'issue')}",
            "title": f"Template issue: {fault.get('check', 'unnamed check')} on {fault.get('count', 0)} pages",
            "why_it_matters": "A template-level fault is fixed once and clears on every page it appears on.",
            "severity": "warning",
            "source": "deep_audit",
            "action_link": "#deep-audit",
        })
    sitemap = doc.get("sitemap") or {}
    if (sitemap.get("score") or 100) < 50:
        items.append({
            "id": "deep-audit-sitemap-score",
            "title": "Sitemap health score is low",
            "why_it_matters": "A broken sitemap can keep pages out of Google's index entirely.",
            "severity": "critical",
            "source": "deep_audit",
            "action_link": "#deep-audit",
        })
    return items, None


def _from_insights(brand_id: str) -> tuple[list[dict], str | None]:
    run = insights.latest_run(brand_id)
    if not run:
        return [], "Rank tracking: no report yet"
    items = [
        {
            "id": f"insights-{todo['id']}",
            "title": todo.get("title", "Untitled"),
            "why_it_matters": todo.get("why", "Estimated traffic gain available in the Traffic & rankings section."),
            "severity": "warning",
            "source": "insights",
            "action_link": "#traffic",
        }
        for todo in (run.get("todos") or [])
        if todo.get("status", "open") == "open"
    ]
    return items, None


def build(brand_id: str) -> dict:
    items: list[dict] = []
    notes: list[str] = []
    for source_fn in (_from_vitals, _from_keyword_pool, _from_competitors, _from_deep_audit, _from_insights):
        source_items, note = source_fn(brand_id)
        items.extend(source_items)
        if note:
            notes.append(note)

    items.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 99))
    doc = {
        "brand_id": brand_id,
        "at": date.today().isoformat(),
        "items": items[:_MAX_ITEMS],
        "notes": notes,
    }
    state.save(_DOC.format(brand_id), doc)
    return doc


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))
```

Before finalizing, re-check the real field names used in `landing_audit.py`'s output (`template_faults`, `check`, `count`) and `deep_audit.py`'s `sitemap.score` — the code above uses the names implied by the spec/research; if the real keys differ, use the real ones (grep `landing_audit.py` and `sitemap_health.py` for their actual output dict construction before treating this as final).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest seo_geo_agent/tests/test_priorities.py -v`
Expected: PASS

- [ ] **Step 6: Run full backend suite for regressions**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add "backend/agents/SEO GEO agent/seo_geo_agent/priorities.py" "backend/agents/SEO GEO agent/seo_geo_agent/tests/test_priorities.py"
git commit -m "feat: add priorities.py — cross-source synthesis into one ranked action list"
```

---

## Task 4: New `/api/seo-geo/priorities/{brand_id}` endpoint

**Files:**
- Modify: `backend/app/routers/seo_geo.py`
- Test: existing router test file — find it with `grep -rl "def get_vitals\|def get_keyword_pool" backend/app` or `backend/agents/"SEO GEO agent"/seo_geo_agent/tests/` and add to whichever file already tests these sibling routes (likely `test_seo_geo.py` per the research's file listing).

**Interfaces:**
- Consumes: `priorities.latest(brand_id)`, `priorities.build(brand_id)` from Task 3; `_brand_or_404(brand_id)` (existing helper, seo_geo.py:107-111); `get_current_user` dependency (existing).
- Produces: `GET /api/seo-geo/priorities/{brand_id}` → `{"priorities": <doc or null>}`; `POST /api/seo-geo/priorities/{brand_id}/refresh` → `{"priorities": <doc>}`.

- [ ] **Step 1: Find the exact import block and insertion point**

Run: `grep -n "^from \.\.agents\|^import\|seo_kwpool\|seo_vitals" backend/app/routers/seo_geo.py | head -20` to find how sibling modules are imported and aliased (e.g. `from ...agents.\`SEO GEO agent\`.seo_geo_agent import vitals as seo_vitals` or similar — match whatever the real import style is).

- [ ] **Step 2: Write the failing test**

```python
# added to the existing test file found in Step 1's sibling search, e.g. test_seo_geo.py
def test_get_priorities_returns_none_when_never_built(client, auth_headers, seeded_brand):
    resp = client.get(f"/api/seo-geo/priorities/{seeded_brand['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["priorities"] is None


def test_refresh_priorities_builds_and_persists(client, auth_headers, seeded_brand):
    resp = client.post(f"/api/seo-geo/priorities/{seeded_brand['id']}/refresh", headers=auth_headers)
    assert resp.status_code == 200
    assert "items" in resp.json()["priorities"]

    resp2 = client.get(f"/api/seo-geo/priorities/{seeded_brand['id']}", headers=auth_headers)
    assert resp2.json()["priorities"]["items"] == resp.json()["priorities"]["items"]


def test_get_priorities_404s_for_unknown_brand(client, auth_headers):
    resp = client.get("/api/seo-geo/priorities/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404
```

Match `client`, `auth_headers`, and `seeded_brand` to whatever fixtures the existing tests in this file actually use — read the top of the file and an existing `test_get_vitals`-style test first, and copy its exact fixture names/setup rather than inventing new ones.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest <path-to-test-file> -k priorities -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 4: Add the routes**

Add the import (matching Step 1's exact style) and, near the existing `vitals`/`keyword-pool` routes, add:

```python
@router.get("/seo-geo/priorities/{brand_id}")
def get_priorities(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"priorities": seo_priorities.latest(brand_id)}


@router.post("/seo-geo/priorities/{brand_id}/refresh")
def refresh_priorities(
    brand_id: str,
    user=Depends(get_current_user),
    act: Activity = trail.records("priorities", "Rebuilt the priority list"),
):
    brand = _brand_or_404(brand_id)
    doc = seo_priorities.build(brand_id)
    act.note(f"Built {len(doc['items'])} priority items for {brand['domain']}")
    return {"priorities": doc}
```

Match the exact `Activity`/`trail.records(...)` call signature to the one used by the neighboring `refresh_keyword_pool` route (seo_geo.py:629-638) — copy it verbatim rather than retyping from memory, since decorator/dependency argument order matters for FastAPI's dependency injection.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest <path-to-test-file> -k priorities -v`
Expected: PASS

- [ ] **Step 6: Run full backend suite**

Run: `cd "backend/agents/SEO GEO agent" && python -m pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/seo_geo.py <test-file-path>
git commit -m "feat: add GET/POST /api/seo-geo/priorities/{brand_id} routes"
```

---

## Task 5: Frontend API client — `seoPriorities`

**Files:**
- Modify: `frontend/lib/api.ts`
- Test: `frontend/lib/api.test.ts` if one exists (check with `ls frontend/lib/*.test.ts`); if the existing test files are narrowly scoped (e.g. `api.timeout.test.ts` only tests timeout behavior per the earlier file listing), add a small new test file `frontend/lib/api.priorities.test.ts` instead of overloading an unrelated one.

**Interfaces:**
- Consumes: `getJson`, `postJson` (existing module-private helpers in `api.ts`, per Global Constraints).
- Produces: `SeoPriorityItem` type, `SeoPrioritiesDoc` type, `seoPriorities(id, opts?)`, `seoPrioritiesRefresh(id)` — used by Task 7's Insights component.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/lib/api.priorities.test.ts
import { describe, expect, it, vi, beforeEach } from "vitest";

describe("seoPriorities", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ priorities: { brand_id: "b1", at: "2026-09-21", items: [], notes: [] } }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
  });

  it("calls the priorities endpoint and returns the typed doc", async () => {
    const { seoPriorities } = await import("./api");
    const result = await seoPriorities("b1");
    expect(result.priorities?.brand_id).toBe("b1");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/seo-geo/priorities/b1"),
      expect.anything(),
    );
  });
});
```

Check an existing test like `frontend/lib/requestPolicy.test.ts` first for the actual `fetch`-mocking convention this codebase uses (`vi.stubGlobal` vs. a custom test helper vs. MSW) and match it exactly rather than introducing a new mocking style.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run api.priorities.test.ts`
Expected: FAIL — `seoPriorities` is not exported.

- [ ] **Step 3: Add the types and functions**

Near the other `Seo*Doc` type definitions in `api.ts` (search `grep -n "interface SeoVitalsDoc\|interface SeoKeywordPoolDoc" frontend/lib/api.ts` for the right neighborhood), add:

```typescript
export interface SeoPriorityItem {
  id: string;
  title: string;
  why_it_matters: string;
  severity: "critical" | "warning" | "suggestion";
  source: "vitals" | "keyword_pool" | "competitors" | "deep_audit" | "insights";
  action_link: string;
}

export interface SeoPrioritiesDoc {
  brand_id: string;
  at: string;
  items: SeoPriorityItem[];
  notes: string[];
}
```

Near `seoVitals`/`seoVitalsRefresh` (api.ts:2556-2560 per the research), add:

```typescript
export const seoPriorities = (id: string, opts?: RequestOptions) =>
  getJson<{ priorities: SeoPrioritiesDoc | null }>(`/api/seo-geo/priorities/${id}`, opts);

export const seoPrioritiesRefresh = (id: string) =>
  postJson<{ priorities: SeoPrioritiesDoc }>(`/api/seo-geo/priorities/${id}/refresh`, {});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run api.priorities.test.ts`
Expected: PASS

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no new errors

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/api.priorities.test.ts
git commit -m "feat: add seoPriorities/seoPrioritiesRefresh API client functions"
```

---

## Task 6: New `Shell` + `Sidebar` container (foundational — no behavior change yet)

**Files:**
- Create: `frontend/components/console/seo/shell.tsx`
- Modify: `frontend/app/seo.css` (new classes only, additive — do not delete any existing class yet)
- Test: `frontend/components/console/seo/shell.test.tsx`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  ```typescript
  export interface SidebarSection {
    id: string;        // matches action_link's "#id" from Task 3's priorities items, without the "#"
    label: string;
    hint?: string;
  }
  export function Shell({
    sections, activeId, onSelect, children,
  }: {
    sections: SidebarSection[];
    activeId: string;
    onSelect: (id: string) => void;
    children: ReactNode;  // the active section's content, rendered by the caller based on activeId
  }): JSX.Element
  ```
  Task 8 will render `<Shell sections={...} activeId={active} onSelect={setActive}>{content for active section}</Shell>` in place of the current nine `<Fold>` blocks.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/components/console/seo/shell.test.tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Shell } from "./shell";

const SECTIONS = [
  { id: "insights", label: "Insights" },
  { id: "traffic", label: "Traffic & rankings" },
];

describe("Shell", () => {
  it("renders every section label in the sidebar", () => {
    render(<Shell sections={SECTIONS} activeId="insights" onSelect={vi.fn()}>content</Shell>);
    expect(screen.getByText("Insights")).toBeInTheDocument();
    expect(screen.getByText("Traffic & rankings")).toBeInTheDocument();
  });

  it("marks the active section and calls onSelect when another is clicked", () => {
    const onSelect = vi.fn();
    render(<Shell sections={SECTIONS} activeId="insights" onSelect={onSelect}>content</Shell>);
    const activeButton = screen.getByRole("button", { name: "Insights" });
    expect(activeButton.className).toMatch(/seo-sidebar__item--active/);
    fireEvent.click(screen.getByRole("button", { name: "Traffic & rankings" }));
    expect(onSelect).toHaveBeenCalledWith("traffic");
  });

  it("renders the children as the main content area", () => {
    render(<Shell sections={SECTIONS} activeId="insights" onSelect={vi.fn()}>hello-content</Shell>);
    expect(screen.getByText("hello-content")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run shell.test.tsx`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `Shell`**

```tsx
// frontend/components/console/seo/shell.tsx
"use client";

import type { ReactNode } from "react";

export interface SidebarSection {
  id: string;
  label: string;
  hint?: string;
}

export function Shell({
  sections, activeId, onSelect, children,
}: {
  sections: SidebarSection[];
  activeId: string;
  onSelect: (id: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="seo-shell">
      <nav className="seo-sidebar" aria-label="SEO console sections">
        {sections.map((s) => (
          <button
            key={s.id}
            className={`seo-sidebar__item${s.id === activeId ? " seo-sidebar__item--active" : ""}`}
            onClick={() => onSelect(s.id)}
            aria-current={s.id === activeId ? "page" : undefined}
          >
            <span className="seo-sidebar__label">{s.label}</span>
            {s.hint && <span className="seo-sidebar__hint">{s.hint}</span>}
          </button>
        ))}
      </nav>
      <div className="seo-shell__content">{children}</div>
    </div>
  );
}
```

- [ ] **Step 4: Add the CSS**

Append to `frontend/app/seo.css` (additive — the existing `.seo-fold*` classes stay in the file until Task 8 removes their usages):

```css
.seo-shell {
  display: grid;
  grid-template-columns: var(--sidebar-w) 1fr;
  gap: var(--space-6);
  align-items: start;
  min-height: 0;
}

.seo-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  position: sticky;
  top: var(--topbar-h);
  padding: var(--pad-card);
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.seo-sidebar__item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: var(--pad-control);
  border-radius: 8px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  font: var(--text-body);
}

.seo-sidebar__item:hover {
  background: var(--surface-2);
  color: var(--text-primary);
}

.seo-sidebar__item--active {
  background: var(--brand);
  color: var(--surface-1);
}

.seo-sidebar__hint {
  font: var(--text-caption);
  color: inherit;
  opacity: 0.75;
}

.seo-shell__content {
  min-width: 0;
}

@media (max-width: 900px) {
  .seo-shell {
    grid-template-columns: 1fr;
  }
  .seo-sidebar {
    position: static;
    flex-direction: row;
    overflow-x: auto;
  }
}
```

Verify every custom property used here (`--sidebar-w`, `--space-6`, `--space-1`, `--pad-card`, `--pad-control`, `--surface-1`, `--surface-2`, `--border`, `--text-secondary`, `--text-primary`, `--brand`, `--text-body`, `--text-caption`) actually exists in `tokens/colors.css`/`tokens/spacing.css`/`tokens/typography.css` by running `grep -n "\-\-sidebar-w\|\-\-space-6\|\-\-surface-1" frontend/tokens/*.css` — if any name differs slightly from what's in the token files, use the real name.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run shell.test.tsx`
Expected: PASS

- [ ] **Step 6: Typecheck and commit**

```bash
cd frontend && npm run typecheck
git add frontend/components/console/seo/shell.tsx frontend/components/console/seo/shell.test.tsx frontend/app/seo.css
git commit -m "feat: add Shell/Sidebar navigation container (not yet wired into SeoAgent)"
```

---

## Task 7: New Insights page

**Files:**
- Create: `frontend/components/console/seo/insights.tsx`
- Test: `frontend/components/console/seo/insights.test.tsx`

**Interfaces:**
- Consumes: `seoPriorities`, `seoPrioritiesRefresh`, `SeoPrioritiesDoc`, `SeoPriorityItem` from Task 5; `ToastFn` from `@/components/console/ConsoleApp` (existing).
- Produces: `export function InsightsView({ brandId, onToast }: { brandId: string; onToast: ToastFn }): JSX.Element` — Task 8 renders this as the sidebar's default/"insights" section content.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/components/console/seo/insights.test.tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { InsightsView } from "./insights";
import * as api from "@/lib/api";

describe("InsightsView", () => {
  beforeEach(() => {
    vi.spyOn(api, "seoPriorities").mockResolvedValue({
      priorities: {
        brand_id: "b1",
        at: "2026-09-21",
        items: [
          { id: "vitals-mobile", title: "Core Web Vitals are failing on mobile", why_it_matters: "x", severity: "critical", source: "vitals", action_link: "#vitals" },
          { id: "keyword-pool-thin", title: "Keyword pool has very few tracked keywords", why_it_matters: "y", severity: "warning", source: "keyword_pool", action_link: "#keywords" },
        ],
        notes: ["Competitors: no report yet"],
      },
    });
  });

  it("renders items sorted with critical first and shows source notes", async () => {
    render(<InsightsView brandId="b1" onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/Core Web Vitals are failing on mobile/)).toBeInTheDocument());
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Core Web Vitals are failing on mobile");
    expect(screen.getByText(/Competitors: no report yet/)).toBeInTheDocument();
  });

  it("shows an empty state when there are zero items", async () => {
    (api.seoPriorities as any).mockResolvedValue({ priorities: { brand_id: "b1", at: "2026-09-21", items: [], notes: [] } });
    render(<InsightsView brandId="b1" onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/nothing urgent/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run insights.test.tsx`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `InsightsView`**

```tsx
// frontend/components/console/seo/insights.tsx
"use client";

import { useEffect, useState, useCallback } from "react";
import { seoPriorities, seoPrioritiesRefresh, type SeoPrioritiesDoc } from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";

const SEVERITY_LABEL: Record<string, string> = {
  critical: "Critical",
  warning: "Warning",
  suggestion: "Suggestion",
};

export function InsightsView({ brandId, onToast }: { brandId: string; onToast: ToastFn }) {
  const [doc, setDoc] = useState<SeoPrioritiesDoc | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let live = true;
    seoPriorities(brandId)
      .then((r) => { if (live) { setDoc(r.priorities); setLoaded(true); } })
      .catch((exc) => { if (live) { onToast("error", describeFailure(exc)); setLoaded(true); } });
    return () => { live = false; };
  }, [brandId, onToast]);

  const refresh = useCallback(() => {
    setBusy(true);
    seoPrioritiesRefresh(brandId)
      .then((r) => setDoc(r.priorities))
      .catch((exc) => onToast("error", describeFailure(exc)))
      .finally(() => setBusy(false));
  }, [brandId, onToast]);

  if (!loaded) {
    return <p className="seo-note">Loading insights…</p>;
  }

  return (
    <div className="seo-insights">
      <div className="seo-insights__head">
        <h2 className="mr-section__title">What to do next</h2>
        <button className="seo-btn seo-btn--primary" onClick={refresh} disabled={busy}>
          {busy ? "Rebuilding…" : "Rebuild"}
        </button>
      </div>

      {doc && doc.notes.length > 0 && (
        <ul className="seo-insights__notes">
          {doc.notes.map((n) => <li key={n} className="seo-note">{n}</li>)}
        </ul>
      )}

      {!doc || doc.items.length === 0 ? (
        <p className="seo-empty">Nothing urgent right now — every connected source is clean.</p>
      ) : (
        <ul className="seo-insights__list">
          {doc.items.map((item) => (
            <li key={item.id} className={`seo-insights__item seo-insights__item--${item.severity}`}>
              <span className={`seo-chip seo-chip--${item.severity}`}>{SEVERITY_LABEL[item.severity]}</span>
              <div className="seo-insights__body">
                <strong>{item.title}</strong>
                <p className="seo-note">{item.why_it_matters}</p>
              </div>
              <a href={item.action_link} className="seo-insights__link">View →</a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add the CSS**

Append to `frontend/app/seo.css`:

```css
.seo-insights__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-4);
}

.seo-insights__notes {
  margin-bottom: var(--space-4);
  padding-left: var(--space-5);
}

.seo-insights__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.seo-insights__item {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: start;
  gap: var(--space-3);
  padding: var(--pad-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-1);
}

.seo-insights__item--critical {
  border-left: 3px solid var(--danger);
}

.seo-insights__item--warning {
  border-left: 3px solid var(--warning);
}

.seo-insights__item--suggestion {
  border-left: 3px solid var(--border);
}

.seo-insights__link {
  white-space: nowrap;
  color: var(--brand);
}
```

Confirm `--danger`/`--warning` exist in `tokens/colors.css` (Global Constraints notes semantic `--success/--warning/--danger` already exist) before relying on them.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run insights.test.tsx`
Expected: PASS

- [ ] **Step 6: Typecheck and commit**

```bash
cd frontend && npm run typecheck
git add frontend/components/console/seo/insights.tsx frontend/components/console/seo/insights.test.tsx frontend/app/seo.css
git commit -m "feat: add InsightsView — cross-source prioritized action list"
```

---

## Task 8: Wire `Shell`/`Sidebar`/`InsightsView` into `SeoAgent.tsx`, retire old Fix list/This-week hero

This is the task that actually changes what a user sees. Do it as one focused diff, not a rewrite of the whole file — every existing section component (`DeepAuditPanel`, `CompetitorsView`, `VitalsView`, `KeywordPoolView`, `PagesView`, the "More tools" switcher, "Website health", "Traffic & rankings", "Blog plan") is reused exactly as-is; only the wrapper around them changes.

**Files:**
- Modify: `frontend/components/console/seo/SeoAgent.tsx`

**Interfaces:**
- Consumes: `Shell`, `SidebarSection` (Task 6); `InsightsView` (Task 7); `KeywordPoolView`'s new `gscConnected`/`keywordLabRun` props (Task 2); everything else already imported in this file.
- Produces: no new exports — this is the integration point, nothing downstream depends on `SeoAgent.tsx`'s internals changing shape.

- [ ] **Step 1: Re-read the current return statement to confirm line numbers**

Run: `sed -n '596,1100p' frontend/components/console/seo/SeoAgent.tsx` and diff mentally against the structure quoted in this plan's research (reproduced in the Architecture section above) — confirm the nine `<Fold>` blocks are still in the same order at roughly the same lines before editing.

- [ ] **Step 2: Add local state for the active sidebar section**

Near the component's other `useState` calls (top of the component body, before the return statement), add:

```typescript
const [activeSection, setActiveSection] = useState("insights");
```

- [ ] **Step 3: Define the sidebar sections list**

Just above the `return (` statement:

```typescript
const SECTIONS: SidebarSection[] = [
  { id: "insights", label: "Insights" },
  { id: "traffic", label: "Traffic & rankings" },
  { id: "keywords", label: "Keywords" },
  { id: "competitors", label: "Competitors" },
  { id: "vitals", label: "Core Web Vitals" },
  { id: "deep-audit", label: "Deep audit" },
  { id: "pages", label: "Pages" },
  { id: "tools", label: "Tools" },
];
```

- [ ] **Step 4: Replace the Fold stack with the Shell**

Replace this whole span — from the `<Story run={run} />` line (research line 782) through the closing of the last `<Fold>`/fragment before `</div>` (research line ~1094, the fragment closing after "Blog plan") — with:

```tsx
<Story run={run} />
<DashboardTiles
  sitemap={sitemapDoc}
  vitals={vitalsDoc}
  pool={poolDoc}
  healthFindings={siteReview ? siteReview.issues.length : null}
/>
{run && <DegradedNotes notes={run.degraded} domain={brand.domain} />}

<Shell sections={SECTIONS} activeId={activeSection} onSelect={setActiveSection}>
  {activeSection === "insights" && <InsightsView brandId={brand.id} onToast={onToast} />}

  {activeSection === "traffic" && run && (
    <div className="seo-section">
      {/* move the EXACT existing JSX body that was previously inside
          <Fold title="Traffic & rankings" ...>...</Fold> here, unchanged */}
    </div>
  )}

  {activeSection === "keywords" && (
    <div className="seo-section">
      <KeywordPoolView
        brandId={brand.id}
        doc={poolDoc}
        gscConnected={!!gsc?.connected}
        keywordLabRun={!!keywordLabDoc}
        onLoaded={setPoolDoc}
        onToast={onToast}
      />
      {/* move the existing Keyword Lab and Blog Plan JSX bodies here too,
          each in their own labeled sub-block, unchanged from their
          current <Fold> contents */}
    </div>
  )}

  {activeSection === "competitors" && (
    <div className="seo-section">
      <CompetitorsView brandId={brand.id} isCreator={!!user?.is_creator} onToast={onToast} />
    </div>
  )}

  {activeSection === "vitals" && (
    <div className="seo-section">
      <VitalsView
        brandId={brand.id}
        doc={vitalsDoc}
        available={vitalsAvailable}
        onLoaded={setVitalsDoc}
        onToast={onToast}
      />
    </div>
  )}

  {activeSection === "deep-audit" && (
    <div className="seo-section">
      <DeepAuditPanel brandId={brand.id} onToast={onToast} />
    </div>
  )}

  {activeSection === "pages" && (
    <div className="seo-section">
      <PagesView doc={pagesDoc} busy={pagesBusy} error={pagesError} onRefresh={/* existing handler, copy verbatim */} />
    </div>
  )}

  {activeSection === "tools" && (
    <div className="seo-section">
      {/* move the existing "More tools" switcher JSX body here, unchanged */}
    </div>
  )}
</Shell>
```

Every `{/* move the existing ... JSX body here, unchanged */}` comment above marks a mechanical cut-and-paste: take the exact children that were previously between a `<Fold title="X" ...>` and its matching `</Fold>` (per the research's line map — "Website health" contents fold into a review of whether that section is still needed given Insights now exists, but the plan's spec explicitly keeps every existing section's detail view; "Website health" and "Fix list"/"Blog plan" specifically: fold "Website health"'s content into the `traffic` section alongside "Traffic & rankings" since both are score/stat-summary content, OR give it its own `SECTIONS` entry if it turns out to be substantial enough — check the real content size at Step 1 and decide; document the choice in the commit message) and paste them into the matching `activeSection === "..."` block above, with zero changes to the component calls, hooks, or handlers inside — only the outer `<Fold>...</Fold>` wrapper is removed and the new `<div className="seo-section">...</div>` wrapper added.

- [ ] **Step 5: Remove the retired "This week" hero and "Fix list" blocks**

Delete the `{plan.length > 0 && (<div className="seo-hero">...</div>)}` block (research line 794) and the `<Fold title="Fix list" ...>...</Fold>` block (research line 986) entirely — their content is superseded by `InsightsView`. If `plan` (the variable feeding the old hero) is now unused elsewhere in the file, remove its `useState`/fetch too; run `grep -n "\bplan\b" frontend/components/console/seo/SeoAgent.tsx` after deleting to confirm no dangling reference remains.

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors — this step will surface any variable (`gsc`, `keywordLabDoc`, `pagesDoc`, `pagesBusy`, `pagesError`) that doesn't exist under the exact name assumed above; if the real variable is named differently, use its real name (the point of this step is to catch exactly that class of mismatch before runtime).

- [ ] **Step 7: Run the frontend test suite**

Run: `cd frontend && npm run test`
Expected: PASS — existing tests for `SeoAgent.tsx` (if any) may need their queries updated from "find X inside an open Fold" to "find X after clicking the sidebar item labeled Y"; update those queries to match, don't delete the tests.

- [ ] **Step 8: Manual browser check before committing**

Run the app locally (`cd backend && python -m uvicorn app.main:app --port 8080` in one terminal, `cd frontend && npm run dev` in another, per the repo's own README instructions) and click through all 8 sidebar sections for a brand with data, confirming each renders its existing content with no console errors. This is the one step in this task that cannot be a script — the whole point of this task is what a human sees.

- [ ] **Step 9: Commit**

```bash
git add frontend/components/console/seo/SeoAgent.tsx
git commit -m "feat: replace scrolling Fold stack with sidebar navigation, retire This-week/Fix-list for Insights"
```

---

## Task 9: Visual redesign pass — shared component classes

**Files:**
- Modify: `frontend/app/seo.css` (and whichever file actually holds `.seo-tile`, `.seo-vital*`, `.seo-band*`, `.seo-pool*` — **not yet identified**; see Step 1)
- Modify: `frontend/tokens/colors.css` only if the redesign direction needs a new semantic token (e.g. a muted "card" background distinct from `--surface-1`) — do not change any existing token's *value*, only add new ones, since existing components outside the SEO agent share this token file.

**Interfaces:**
- Consumes: nothing new.
- Produces: no new exports — pure CSS.

- [ ] **Step 1: Locate the real file holding `.seo-tile`/`.seo-vital`/`.seo-band`/`.seo-pool` classes**

Run: `grep -rn "\.seo-tile \|\.seo-vital \|\.seo-band \|\.seo-poolbar" frontend/ --include="*.css"`

The prior research confirmed these are NOT in `frontend/app/seo.css` despite `dashboard.tsx` using them — find the actual file (likely `frontend/app/dashboard.css`, per the file listing gathered earlier in this project: `frontend/app/dashboard.css` exists as a sibling to `seo.css`). Do all edits in this task against whatever file this grep actually returns.

- [ ] **Step 2: Apply the visual direction to card/tile/vital components**

Using the spec's direction (dark-first analytics-console feel, restrained brand accent, card-based panels, chart treatment for numeric panels) and the exact token names confirmed to exist in `tokens/colors.css`/`spacing.css`/`typography.css`, update the rules for `.seo-tile`, `.seo-tile--${tone}`, `.seo-vital`, `.seo-vital__track`, `.seo-band`, `.seo-band--on`, `.seo-pool__row` etc. so that:
- Every card-like block (`.seo-tile`, `.seo-vital`, a single `.seo-pool__row`) uses `border: 1px solid var(--border)`, `border-radius: 10px` or `12px` consistently (pick one radius and apply everywhere in this pass — do not leave a mix of 6px/8px/12px radii across sibling components), and `background: var(--surface-1)`.
- Brand color (`var(--brand)`/`var(--action)`) is applied only to: the active sidebar item (already done in Task 6), primary buttons (`.seo-btn--primary`, unchanged — already brand-colored), and one accent per stat (e.g. `.seo-tile__value` text color), not to every chip/border as the current chip-heavy styling does.
- `.seo-band` (the keyword-pool band filter buttons) get the same `--surface-2`/hover-state treatment as `.seo-sidebar__item` from Task 6, for visual consistency between the new sidebar and this older component.

Write the actual replacement CSS rules for each selector listed above (read the current rule for each first with `grep -n -A5 "^\.seo-tile {" <the-real-file>` etc., then replace the property values — do not leave TODOs for "pick colors later"; use `var(--brand)`, `var(--surface-1)`, `var(--surface-2)`, `var(--border)`, `var(--danger)`, `var(--warning)`, `var(--success)` as appropriate to each rule's existing semantic role, which you can infer from the current rule and from `VitalsView`'s className logic in `dashboard.tsx` — e.g. `.seo-vital__zone--poor` should use `var(--danger)`, `--good` should use `var(--success)`).

- [ ] **Step 3: Visual check**

Run the app locally (as in Task 8 Step 8), open a brand's dashboard in both light and dark theme (toggle per whatever mechanism the app already uses — check `lib/kit-ui.tsx` or a theme toggle in the header), and confirm: no illegible text (check contrast doesn't regress on any changed background/text pairing), no broken layout (a changed border-radius or padding value causing overflow), both themes render the intended restrained-accent look.

- [ ] **Step 4: Run frontend tests**

Run: `cd frontend && npm run typecheck && npm run test`
Expected: PASS (pure CSS changes should not break any test, but component tests that assert on className strings could — fix any that do)

- [ ] **Step 5: Commit**

```bash
git add frontend/app/seo.css frontend/app/dashboard.css frontend/tokens/colors.css
git commit -m "style: consistent card treatment and restrained brand accent across dashboard components"
```

(Adjust the file list in the commit to whatever files Step 1/2 actually touched.)

---

## Task 10: Full deploy + browser QA pass

**Files:** none (deployment + verification only)

- [ ] **Step 1: Deploy backend**

```bash
cd "/home/vishal/Documents/lpt seo agent"
gcloud run deploy seo-agent-backend --source backend --region asia-south1 --project lpt-seo-agent --service-account seo-agent-backend@lpt-seo-agent.iam.gserviceaccount.com --quiet
```

- [ ] **Step 2: Deploy frontend**

```bash
cd "/home/vishal/Documents/lpt seo agent"
gcloud run deploy seo-agent-frontend --source frontend --region asia-south1 --project lpt-seo-agent --service-account seo-agent-frontend@lpt-seo-agent.iam.gserviceaccount.com --set-env-vars BACKEND_ORIGIN=https://seo-agent-backend-432448138006.asia-south1.run.app --memory 512Mi --cpu 1 --concurrency 80 --quiet
```

- [ ] **Step 3: Smoke-test in a real browser**

Open `https://seo-agent-frontend-432448138006.asia-south1.run.app`, sign in, open a brand, and click through every sidebar section. Confirm: Insights shows a real ranked list (or the "nothing urgent" empty state), Core Web Vitals shows data or a specific, honest reason it can't (not a silent blank), Keyword pool shows its CTA(s) if thin. Check the Cloud Run logs for errors during this pass:

```bash
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=seo-agent-backend AND severity>=ERROR' --project lpt-seo-agent --limit 30 --freshness=10m
```

- [ ] **Step 4: Trigger a priorities refresh and a vitals refresh for the real brand to confirm the bug fixes against production data**

From the browser (Insights page "Rebuild" button, and Core Web Vitals section's own refresh action) rather than curl, since both routes are behind IAP + the app's own auth.

---

## Self-review notes

- **Spec coverage:** §1 (CrUX bug → Task 1, keyword-pool CTAs → Task 2), §2 (priorities module + endpoint → Tasks 3-4, retiring old surfaces → Task 8), §3 (sidebar IA → Tasks 6, 8), §4 (visual direction → Task 9), §5 (testing → embedded in every task's TDD steps + Task 10's manual pass) are all covered.
- **Known open decisions left to the executor, called out explicitly rather than hidden:** the real field names in `landing_audit.py`/`sitemap_health.py` output (Task 3, Step 4 note), the real CSS file holding `.seo-tile`/`.seo-vital` etc. (Task 9, Step 1), where "Website health" content lands in the new sidebar (Task 8, Step 4 note), and the real testing-library mocking convention already in use (Tasks 2 and 5, Step 1/2 notes). Each of these is a "grep first, then use the real name" instruction, not an unresolved design question.
- **Type consistency check:** `SeoPriorityItem`/`SeoPrioritiesDoc` (Task 5) match the shape `priorities.py` (Task 3) actually returns; `SidebarSection` (Task 6) matches the `action_link`/`#id` convention used by `priorities.py`'s `action_link` field and `SeoAgent.tsx`'s `SECTIONS` ids (Task 8) — `"#vitals"` → section id `"vitals"`, `"#keywords"` → `"keywords"`, `"#deep-audit"` → `"deep-audit"`, `"#traffic"` → `"traffic"`, confirmed consistent across Tasks 3, 6, 7, 8.
