# SEO Agent Dashboard Revamp — Design

## Problem

The SEO GEO agent console (`frontend/components/console/seo/SeoAgent.tsx` +
`labs.tsx`, ~1,779 lines) has grown into a single long scrolling page of 15
stacked accordion sections. Reported issues:

1. Core Web Vitals / CrUX panel shows no data on repeated tries despite a
   valid, freshly configured `SEO_CRUX_API_KEY`.
2. Keyword pool shows far fewer keywords than expected.
3. The dashboard is visually cluttered and not human-readable — too much is
   crammed into single folds (Deep Audit's 5 sub-tabs, Competitors' 3 stacked
   panels, Traffic & Rankings' 3 stacked sub-sections, Blog Plan's 5-6 chips
   per row).
4. No page synthesizes findings across sources (sitemap, vitals, keyword
   pool, competitors, deep audit) into a single prioritized action list. The
   existing "This week" hero and "Fix list" are generated only from
   rank-tracking data (`insights.py`), not from vitals/keyword-pool/
   competitor/audit data — those four are dead-end tabs.

## Root causes (confirmed by code read)

**CrUX/Vitals** — `vitals.py` builds the CrUX origin as
`f"https://{brand['domain']}"` with no normalization. CrUX requires an exact
origin match (scheme + host, no `www`/bare mismatch tolerance). If the site's
real traffic is under a different host form than what's stored in
`brand["domain"]`, every request 404s. A 404 is treated identically to "site
below CrUX's traffic threshold" (`vitals.py:150-155`), so a real
misconfiguration is indistinguishable from a legitimately low-traffic site —
both render as the same "Chrome has no field data yet" note. Generic request
failures also collapse into a similarly worded note, hiding the real error
class.

**Keyword pool** — not a bug. `keyword_pool.py` joins 4 sources: Search
Console (requires OAuth connection per brand — likely not connected), brand
seeds (small, user-typed list), keyword-lab clusters (requires the user to
have manually run "Map keywords" at least once — likely never run), and blog
plan topics (requires at least one main analysis run). For a brand missing
GSC connection and a keyword-lab run, only the two small, bounded sources
remain — hence "very less keywords."

## Scope

Everything at once, no phasing (explicit user decision) — bug fixes, new
backend synthesis endpoint, full frontend IA restructure (sidebar/tabs
instead of one scrolling accordion page), and a full visual redesign
(direction below), in one implementation pass.

## Design

### 1. Bug fixes

- **CrUX origin normalization**: try `https://{domain}`; on a 404/empty
  result, retry the opposite `www.`/bare-apex form; use whichever returns
  data. Record which origin form succeeded in the vitals doc. Distinguish, in
  the note text shown to the user, "no field data at any origin form we
  tried" from "request failed" (exception class + which origin) — these must
  no longer render as the same sentence.
- **Keyword pool UX**: no auto-triggering of Keyword Lab (it's an LLM-cost
  operation; silently running it would violate the app's existing
  "every adapter degrades and says so, nothing runs itself" philosophy).
  Instead, the Keywords page must show an explicit empty-state per missing
  source with a direct action: "Connect Search Console" (deep-links to the
  existing OAuth start flow) and "Run Keyword Lab" (deep-links to that
  existing tool), so the low count is self-explanatory and one click from
  fixed.

### 2. Backend: cross-source synthesis

New module `priorities.py` in `seo_geo_agent/`, following the existing
adapter-degrades-gracefully pattern (`insights.py` is the closest existing
analog — read it for conventions before writing this).

`build_priorities(brand_id: str) -> dict` reads, per brand: the latest deep
audit summary (sitemap/landing/cannibalization/density/page-speed), the
vitals doc, the keyword pool, competitors, and the existing rank-tracking
`insights.latest_run`. Each available source contributes zero or more
candidate items shaped `{title, why_it_matters, severity, source,
action_link}` (`action_link` points at the existing panel/tab that has the
detail — this page synthesizes, it does not replace the detail views).
Missing/degraded sources contribute nothing and are noted, exactly like every
other adapter in this codebase — never an error that blocks the rest.

Candidates are merged and ranked by severity (critical > warning >
suggestion), capped at a sane top-N (10, matching the existing Fix List
cap). New route `GET /api/seo-geo/priorities/{brand}` in `seo_geo.py`,
following the existing router's auth/brand-lookup conventions.

The existing "This week" hero and "Fix list" (rank-tracking-only) are
retired — the new Insights page replaces both, so there is exactly one
"what to do next" surface instead of two competing ones scoped to different
data.

### 3. Frontend IA — sidebar navigation, one section per screen

Replace the single scrolling accordion page with a persistent sidebar (8
destinations) and a content area rendering one section at a time:

1. **Insights** (new; default landing view when a brand opens)
2. **Traffic & Rankings** (existing GSC/GA stats + "what changed")
3. **Keywords** (existing Keyword pool + Keyword Lab + Blog Plan, unified —
   today these are three separately-reached places)
4. **Competitors** (existing)
5. **Core Web Vitals** (existing, post-bug-fix)
6. **Deep Audit** (existing 5 sub-tabs unchanged in this pass: sitemap,
   landing, cannibalization, density, speed)
7. **Pages** (existing site-pages table)
8. **Tools** (existing Ask / Content briefs / Site audit, as a sub-switcher
   like today)

This is a restructure of an existing flow's layout and navigation, not a
change to what data each section shows (aside from the Insights page, which
is genuinely new, and the Keywords-page empty-state CTAs from the bug fix
above).

### 4. Visual redesign direction

Internal analytics-console feel, not a marketing page: dark-first neutral
theme, brand red/yellow reserved for primary actions and accents only (not
applied everywhere the way today's chip clusters do), Geist kept (the font
was never the complaint — hierarchy/spacing was), card-based panels
replacing bare chip-cluster stacks, and numeric panels (vitals, page-speed)
extended to the mini-chart/bar treatment those two already use elsewhere in
the app rather than raw stat grids. Exact tokens (palette, spacing scale,
component states) are a visual-design decision to make during
implementation, not fixed in this spec — the implementing pass should engage
a design-focused skill for that step.

### 5. Testing

- Backend: new pytest module for `priorities.py`, following
  `test_deep_audit.py`'s pattern — synthetic per-source docs, asserting
  ranking order and graceful degradation when a source is missing or
  errors.
- Backend: regression test for the CrUX origin-normalization fallback
  (mock both origin forms, assert the fallback fires and the doc records
  which one succeeded).
- Frontend: vitest smoke tests for the new sidebar nav and Insights page
  components, matching existing coverage style (`lib/*.test.ts`).
- Manual: full browser walkthrough (the existing
  `scratchpad/shot.mjs`-style screenshot pass mentioned in the repo README)
  across every nav destination, light and dark, before calling this done.

## Out of scope for this pass

- Firebase/Cloud Run infra (already deployed and working as of this spec).
- Any change to how deep audit's 5 sub-tabs work internally — only their
  container moves under the new "Deep Audit" nav destination.
- Auto-running Keyword Lab or connecting Search Console automatically.
