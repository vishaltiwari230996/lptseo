# SEO agent — standalone extract

The SEO GEO agent (`a2`) pulled out of the AgentOS workspace at
`C:\Users\ACER\Desktop\ghi`, backend and frontend, into a folder that runs on
its own. **This is a copy — nothing was removed from `ghi`.**

Extracted 2026-09-11.

```
seo-agent/
  backend/    FastAPI — the SEO agent package, its router, and the shared
              services it imports
  frontend/   Next.js — the SEO panel, its shared lib, and a standalone shell
```

## Running it

Both halves are already set up in this working copy — `backend/.venv`,
`backend/.env` and `frontend/.env.local` exist and are gitignored. To start:

```powershell
# terminal 1
cd backend
.venv\Scripts\python -m uvicorn app.main:app --port 8080

# terminal 2
cd frontend
npm run dev                 # localhost:3000
```

Then open **http://localhost:3000** and press **Continue without Google**.

### First-time setup, if you are starting from a clean checkout

```powershell
cd backend
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-local.txt
copy .env.example .env      # then fill in the values you need
```

Use **`requirements-local.txt`**, not `requirements.txt`, to run this on a
laptop. `requirements.txt` is the parent monorepo's, copied byte-identical, and
most of it belongs to agents that are not in this extract; `cairosvg` in
particular needs native Cairo DLLs Windows does not ship, and the file itself
flags it "Linux/Cloud Run; optional locally". `requirements-local.txt` is the
set this extract actually imports, verified by walking every import under
`app/` and `agents/`. The container build still uses `requirements.txt`.

### How the two halves find each other

`lib/api.ts` talks to `localhost:8080` whenever `NEXT_PUBLIC_API_URL` is unset,
so the two halves find each other with no configuration in local dev.
`frontend/.env.local` deliberately leaves it unset — which also keeps every
request away from `app/backend/[...path]/route.ts`, whose fallback upstream is
the live production service.

The browser calls `:8080` from `:3000`, so this is a real cross-origin request:
`CORS_ORIGINS` in `backend/.env` has to list the origin the frontend is served
from, or every call fails as "Failed to fetch".

### Signing in locally

Google sign-in needs two things a local offline run does not have: a Google Web
Client ID to verify an ID token against, and a reachable Firestore to upsert the
user into. So this extract adds a second door — `POST /api/auth/dev`, offered by
the login screen as **Continue without Google**:

| Switch | Where | Purpose |
|---|---|---|
| `LOCAL_DEV_AUTH=1` | `backend/.env` | opens the endpoint |
| `APP_ENV=development` | `backend/.env` | must *also* hold, or it answers 404 |
| `NEXT_PUBLIC_LOCAL_DEV_AUTH=1` | `frontend/.env.local` | shows the form |
| `NEXT_PUBLIC_LOCAL_DEV_EMAIL` | `frontend/.env.local` | prefills the address |

It is not a bypass of the allowlist: it calls the same `is_allowed_email` the
Google path does, so an address that cannot sign in with Google cannot sign in
here either (`ALLOWED_EMAIL_DOMAINS` is `lawpreptutorial.com`). It writes no user
document — the id is derived from the address — and roles are re-derived from
config on every request by `get_current_user`, exactly as for a Google sign-in.
Because `app_env` defaults to `production` when unset, a deployment cannot serve
this endpoint even if `LOCAL_DEV_AUTH` is set by accident.

To use real Google sign-in instead, set `GOOGLE_CLIENT_ID` in `backend/.env` and
`NEXT_PUBLIC_GOOGLE_CLIENT_ID` in `frontend/.env.local` to the same Web Client
ID, and give the backend Firestore credentials. Both doors can be open at once.

### Storage: this runs fully offline

`SEO_OFFLINE=1` in `backend/.env` makes the agent persist to local JSON under
`backend/agents/SEO GEO agent/seo_geo_agent/local_state/` instead of Firestore,
so **no GCP project, service account or Cloud Storage bucket is needed**. The
activity trail in `run_tracking.py` still aims at Firestore, but every one of
its writes is already best-effort (`_flush` swallows the failure by design), so
with no credentials it simply logs a warning per request and the call succeeds.

Every external key in `.env` is optional and starts empty. The feature each one
powers reports itself unavailable rather than crashing: `OPENROUTER_API_KEY`
(Ask, brief generation, competitor profiles), `SEO_SERPER_API_KEY` (SERP x-ray,
keyword lab), `SEO_CRON_KEY` (the cron endpoint, which this extract has no
caller for anyway).

### The console shell had to be written

`seo.css` opens by saying the panel "rides the MR desk chrome
(`.mr-app`/`.mr-top`/`.mr-section`)". That chrome lived in the Marketing
Research agent's stylesheet, and MR is not part of this extract — so it never
came across. Nine of the thirteen `.mr-*` classes the panel marks up resolved
to nothing, which is why the signed-in console had no header, no page width and
no cards. `app/console-shell.css` is that chrome, written for this brand.

### Offline persistence and offline network are separate switches

`SEO_OFFLINE=1` used to mean two things at once: persist to local JSON *and*
make no outbound calls. So the moment you set it to run without a Firestore you
also lost every OpenRouter feature — `/api/seo-geo/ask` answered 503 "offline
mode" with a perfectly good key in `.env`.

`state.use_network()` now decides the second question on its own:

| | Firestore | Outbound calls |
|---|---|---|
| `SEO_OFFLINE=1` alone | local JSON | off |
| `SEO_OFFLINE=1` + `SEO_ALLOW_NETWORK=1` | local JSON | **on** |

The default is unchanged, so nothing that relied on the old meaning moves.
`backend/conftest.py` forces `SEO_ALLOW_NETWORK=0` for the suite, and that
guard is load-bearing: `state._load_local_env` exports every `SEO_*` key out of
`backend/.env`, so without it a developer enabling network for their own
machine silently enables it for the tests — the run goes from 3 seconds to 3
minutes of real Google and Serper traffic and still passes, because these paths
all degrade quietly.

### Rank tracker schedule

`POST /api/seo-geo/rank-tracker/cron` (header `x-cron-key: $SEO_CRON_KEY`) sweeps
every enabled brand's rank pool. Cloud Scheduler runs it on `0 */2 * * *`.

**Measured against the live API: `num` is ignored by this Serper plan.**
`num=10`, `num=20` and `num=100` all billed exactly 1 credit and all returned
at most 10 organic results on page 1 (fewer if that's all that existed) —
raising `SERP_RESULTS` bought nothing, so it was dropped back to `10` and the
striking-distance band (ranks 4–20) that the worklist scores on was half-blind:
a query at rank 11 and one at rank 95 both came back `position: None`.

Depth past 10 comes from Serper's `page` parameter instead — page 2 costs one
*additional* credit and returns the next 10 results. The sweep (`rank_tracker.
sweep`) now fetches it adaptively, per query: page 1 first, and page 2 only
when our domain was not on page 1. A query already ranking top-10 costs
exactly what it always did; a query that is not costs one credit more, and
pays for seeing whether it is sitting at 11-20 or nowhere at all.

That makes the monthly bill a **range, not a fixed number**, and it shrinks as
rankings improve:

- **Floor — every query already top-10:** 200 queries × 12 runs/day × 1 credit
  = 2,400/brand/day (~72,000/month). This is the number the schedule was
  originally sized against.
- **Ceiling — every query missing page 1:** 200 queries × 12 runs/day × 2
  credits = 4,800/brand/day (~144,000/month).
- **In practice:** somewhere between the two, trending toward the floor as the
  worklist does its job — the whole point of tracking rank 11-20 instead of
  just "not in the top 10" is to close those gaps, and each one closed removes
  that query's second credit from every future sweep.

A per-brand ceiling of 3,000/day is enforced in `rank_tracker.
MAX_SEARCHES_PER_DAY` — above the 2,400 floor, but *below* the 4,800 worst-case
ceiling. At 200 queries all missing page 1, one sweep alone needs 400 charges
(200 × 2), so the 8th sweep of a worst-case day (8 × 400 = 3,200 > 3,000) hits
the cap before completing its 200 queries. **What happens then is exactly what
`charge()` already does for any budget exhaustion: the sweep stops partway
through, keeps the rows it already has, and reports `blocked: "budget"` with a
note naming how many queries it managed before stopping** — it does not queue,
retry, or silently skip the rest. The remaining sweeps that day are refused
outright the same way, until the UTC date rolls over. `SEO_RANK_SWEEP_DISABLED=1`
stops the cron everywhere; to lower the cadence instead, change the Cloud
Scheduler expression — no code change is needed.

The cron sweeps brands **sequentially**, so its wall-clock runtime grows with
brand count — manageable at one brand, worth revisiting before a second is
onboarded.

#### Before enabling the schedule

Two explicit actions are required before the Cloud Scheduler job is created.
Each must complete before the next:

**(a) Run one manual sweep and inspect it**

With the service deployed: in the Rank tracker panel, click **Rebuild pool**,
then **Run now**. Confirm in the result panel that:
- the pool reached a sensible size (e.g. 50–200 queries)
- `meta.errors` is 0 or very near it
- the ranks look like Indian SERPs (domains common in India, not US brands)
- the worklist's top rows are queries worth working on

**(b) Create the Cloud Scheduler job**

The backend is **not** publicly invokable — its only `roles/run.invoker`
member is the frontend's service account. A Scheduler job that sends nothing
but the `x-cron-key` header is rejected by Cloud Run's IAM layer with an HTML
403 and never reaches the application, so the sweep silently never runs. The
job therefore needs an **OIDC token** as well as the header: the header proves
"this caller knows the cron secret", the token proves "this caller may invoke
this service". Both are required.

First, a dedicated caller identity with permission to invoke the service:

```bash
PROJECT=lpt-seo-agent
REGION=asia-south1
URL=$(gcloud run services describe seo-agent-backend \
      --project=$PROJECT --region=$REGION --format='value(status.url)')

gcloud iam service-accounts create seo-rank-cron \
  --project=$PROJECT --display-name="Rank tracker scheduler"

gcloud run services add-iam-policy-binding seo-agent-backend \
  --project=$PROJECT --region=$REGION \
  --member="serviceAccount:seo-rank-cron@$PROJECT.iam.gserviceaccount.com" \
  --role=roles/run.invoker
```

Then the job itself (Cloud Scheduler API must be enabled on the project first
— `gcloud services enable cloudscheduler.googleapis.com`):

```bash
gcloud scheduler jobs create http seo-rank-sweep \
  --project=$PROJECT \
  --location=$REGION \
  --schedule="0 */2 * * *" \
  --time-zone="Asia/Kolkata" \
  --uri="$URL/api/seo-geo/rank-tracker/cron" \
  --http-method=POST \
  --headers="x-cron-key=<SEO_CRON_KEY>" \
  --oidc-service-account-email="seo-rank-cron@$PROJECT.iam.gserviceaccount.com" \
  --oidc-token-audience="$URL" \
  --attempt-deadline=900s \
  --max-retry-attempts=0
```

`<SEO_CRON_KEY>` is the value stored in the `SEO_CRON_KEY` secret. The service
must also reference that secret as an environment variable
(`--update-secrets=SEO_CRON_KEY=SEO_CRON_KEY:latest`) and its runtime service
account needs `roles/secretmanager.secretAccessor` on it — until both are in
place the endpoint answers `503 SEO_CRON_KEY not configured` and fails closed,
which is the intended behaviour, not a fault.

Verify the wiring before trusting the schedule, by calling it exactly as the
scheduler will:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "x-cron-key: <SEO_CRON_KEY>" \
  "$URL/api/seo-geo/rank-tracker/cron"
```

`200` means a sweep ran. `403` means the header is wrong. `503` means the
secret is not wired. An HTML `403` with no JSON body means the IAM layer
rejected the caller before the app saw it — the OIDC half is missing.

**`--max-retry-attempts=0` is not optional.** Cloud Scheduler's default is to
retry a non-2xx response, and this endpoint answers 502 when every brand's
sweep failed — which is exactly the situation in which Serper is returning
errors for everything. Each retry re-enters the sweep and `charge()`s another
~200 searches before the first query comes back, so a provider outage during
one scheduled run would bill for several. There is another run in two hours;
that is the retry. (A retry is also usually pointless for a different reason:
the sweep refuses to start while another one holds the brand's lease, so a
retry that arrives before the first attempt has finished is answered without
doing anything at all.)

**The Cloud Run service needs `--timeout=900` to match the
`--attempt-deadline=900s` above.** They are two separate ceilings and the
lower one wins. The sweep `charge()`s each query up front but writes its
results once, at the end, so a kill part-way through has spent every credit
and stored nothing — a Cloud Run default timeout of 300s against a
900s-deadline schedule means a long sweep is cut off two thirds of the way
through and the whole run is wasted:

```bash
gcloud run services update <service> --timeout=900 --region=<region>
```

### `--reload` does not work here

Uvicorn's reloader detects the edit and prints `WatchFiles detected changes …
Reloading…`, but the worker never respawns — the server keeps serving the **old
code** while claiming to have reloaded, which is worse than no reloader at all.
Reproduced on this machine with Python 3.14.5 on Windows, both with and without
`--reload-dir` scoping. Run without `--reload` and restart the process by hand
after a backend edit. The Next.js dev server hot-reloads normally.

## Branding

The console is branded **Law Prep Tutorial** (lawpreptutorial.com — law and
judiciary entrance coaching, Jodhpur, est. 2001). It was extracted carrying the
previous owner's identity; that has been replaced throughout.

Their logo is a red serif wordmark, pure `#ff0000` on pure `#ffff00`. Neither
value survives contact with a UI unchanged — white text on `#ff0000` is 4.00:1
and fails AA, and `#ffff00` cannot be a light-theme fill — so the identity and
the interface are separated in `tokens/colors.css`:

| Token | Value | Used by |
|---|---|---|
| `--lpt-red` / `--lpt-yellow` | `#ff0000` / `#ffff00` | the logo mark, and nothing else |
| `--brand` / `--action` (light) | `#d42a26` | buttons, links — white text at 5.05:1 |
| `--brand` / `--action` (dark) | `#ffe000` | same, ink text at 15.26:1 |
| `--blue-500` / `--brand-light` | `#e0332f` | the site's own CTA red, one stop lighter |

That mirrors the structure the file already had — a light-theme primary swapped
for a bright one on ink — with red/yellow where grape/volt used to be.

### Type

One family: **Geist**, at every size, with Geist Mono for figures. Hierarchy is
carried on weight and negative tracking, which is most of what reads as
"premium" in this category — a grotesque set loose at 60px looks cheap.

This replaced a three-family setup (Poppins display / Plus Jakarta Sans body /
Bodoni Moda brand serif). The serif was a wrong call worth recording: a didone
is built for large sizes on a page with air, and most of this product is a 13px
data console, where its thin strokes go spindly and its numerals fight the UI.

### The hero background

A growth curve, drawn live in canvas: a rising area chart with the detail
scrolling through it, a dimmer second series trailing behind, and a marker on
the leading edge. `components/HeroMedia.tsx`.

It is the product's subject, not the client's. An earlier pass put a generated
montage of law-school footage here — night library, lecture hall, courthouse
steps — which was about the customer's business rather than about what this
console does. This one is about growth, which is the thing being sold.

**It is drawn rather than filmed because filmed could not be made smooth.** The
montage was assembled with ffmpeg's `zoompan`, which recomputes an *integer*
pixel offset per frame: a slow move sits on one pixel for several frames and
then jumps two. That reads as stutter and no bitrate fixes it, because the
stepping is in the geometry, not the encoding. Here the position is a continuous
function of elapsed time — a trend plus three sines at incommensurable
frequencies, sampled fresh each frame — so there is nothing to quantise, no
keyframes to tween between, and no loop point to see.

Measured, not assumed: `scratchpad/smooth.mjs` reads the canvas back on every
animation frame and reports the curve's per-frame movement. Current numbers are
**mean 0.33px, max 1px, zero frames jumping 3px or more**. The stutter signature
it is looking for is the opposite shape — several frames of no movement followed
by a multi-pixel jump.

Reduced motion gets a composed still, not a frozen first frame.

### The dashboard

Above the fold list, a five-tile summary: sitemap score, Core Web Vitals,
keywords tracked, opportunity, and site findings. A tile with no data reads "—"
and stays neutral — an unknown and a pass must not look alike at a glance,
which is the only job a tile row has.

Three sections behind it, each a stored read with its own rebuild button. They
are split from the main run deliberately: a sitemap audit spot-checks a dozen
live URLs and a CrUX pull makes several API calls, so neither belongs on
something that renders on every page load.

**Sitemap health** now comes from the deep audit (below), which reads every
sitemap and crawls every URL in them; the tile shows the score it leaves. The
first, spot-checking version is gone. Two of its bugs are worth keeping written
down: it parsed `<lastmod>` out of `/sitemap.xml` and divided by the URL count —
on a sitemap **index** that compared the index's handful of dates with the
children's hundreds of URLs and reported 2% coverage for a site whose real
figure is 38% — and it read one level of index, reporting 310 URLs for a site
with 1,125, because the blog sat in an index nested inside the main index.

**Core Web Vitals** (`vitals.py`) — the Chrome UX Report: the 75th percentile of
what real Chrome visitors got over the trailing 28 days, on mobile and desktop,
plus the busiest pages. Field data, not a lab score, and the same measurement
Search Console assesses against. Each metric is drawn against Google's own
good/needs-improvement/poor boundaries rather than as a bare bar.

Needs `SEO_CRUX_API_KEY` (free — enable "Chrome UX Report API" on a Google Cloud
project). Neither CrUX nor the PageSpeed Insights API works without a key; the
shared keyless PSI quota is permanently exhausted. Absent a key the panel says
so and nothing else is affected. Note that a valid key still returns nothing for
a site below CrUX's reporting threshold — that is not a fault, and the panel
says that too.

**Keyword pool** (`keyword_pool.py`) — every keyword the agent knows, joined
from four places that each previously answered only their own question: Search
Console queries, the brand's seeds, keyword-lab clusters, and the blog plan.
Every row names its sources and a keyword with no history shows blank metrics
rather than a guess.

The one derived number is `opportunity`: estimated *additional* monthly clicks
from a realistic ranking move — to position 3 from page one, to position 8 from
page two, nothing past 30. Deliberately not "clicks if we were #1", which is
fantasy for most terms and makes every list top-heavy with keywords nobody will
win. A keyword already ranking well scores low, because it is already doing its
job.

## The deep audit

The first fold on a brand page. One crawl of **every URL in every sitemap**,
then five diagnostics that all read that one snapshot — so the sitemap report,
the landing-page audit and the cannibalization report can never disagree about
what a page said. Page speed is a sixth, separate job because it takes over an
hour.

| Module | What it answers |
|---|---|
| `crawl.py` | every sitemap (robots-declared + `/sitemap.xml`, indexes to any depth) and every URL in them, fetched and taken apart: ~40 facts per page, main content isolated from template |
| `sitemap_health.py` | is each sitemap file, and each URL in it, something Google should be given? |
| `landing_audit.py` | ~45 checks on every landing page, and which failures come from the shared template |
| `cannibalization.py` | which of the site's own pages compete for the same search, and which one to keep |
| `keyword_density.py` | does every blog article mention its focus keyword at least once per 150 words? |
| `page_speed.py` | how long every page takes to appear on a phone, and exactly what makes it slow |
| `deep_audit.py` | runs the first five in order, as a background job with progress |
| `jobs.py` | background jobs (one per brand per kind, progress persisted, restart-safe) and chunked storage under Firestore's 1MB document limit |

API: `POST /api/seo-geo/deep-audit/{brand}/run` starts it; `GET …/deep-audit/{brand}`
is progress + summary; `…/sitemap`, `…/landing`, `…/landing/page?url=`,
`…/cannibalization`, `…/density` are the reports. Page speed:
`POST /api/seo-geo/page-speed/{brand}/run` with `{"limit": 40}` (most important
pages first) or `{"limit": null}` (every page), then `GET …/page-speed/{brand}`
and `…/page-speed/{brand}/page?url=`.

### How each part decides

**Crawl.** Redirects are followed by hand so every hop is recorded and every
hop passes the SSRF address check. The address is resolved once per host,
checked, and *connected to* (`crawl._Resolver` + `_PinnedBackend`), which closes
the DNS-rebinding gap of checking a name and letting the HTTP library resolve
it again. Only the site's own host is retried, and at most 20,000 sitemap URLs
are read, so a hostile sitemap cannot turn the crawler on a third party. Main
content is trafilatura (`favor_recall`, `deduplicate` **off**) after stripping
header/nav/footer, forms, dialogs and anything named modal/popup/login/cookie —
counting template words would dilute every keyword ratio and word count. Then,
site-wide, each landing page's **unique** words: a word counts as the page's
own only if some 5-word run containing it appears on fewer than 3 landing
pages. That is what "thin content" is judged on — 277 words of centre-page
template repeated on 88 pages are not 277 words of content for any of them —
and it sees through name-swapped paragraphs ("Visit our Bhopal centre…").

**Sitemap** — per file: dead or redirected declarations (and whether the dead
one has a live twin at another path), unparseable, empty, oversize, nested
indexes, lastmod missing/invalid/in the future. Per URL, each a contradiction
with what the URL actually does: non-200, redirected, redirect that downgrades
to http mid-chain, noindex, canonical elsewhere, robots-blocked, http, mixed
hosts, soft 404, orphan (no internal links), identical content, listed twice,
parameters, uppercase, underscores, over-long, and **http resources on an https
page** — checked on every sitemap page, blog posts included, with the URLs they
point at. (This one was found by the page-speed run, not by any report: 111 blog
posts embed 568 images from `http://13.127.188.157`, a raw server IP the browser
blocks, so those images never appear.) Site level: host variants that
don't redirect to one canonical host, no HSTS, trailing-slash duplicates, and
live pages the sitemap omits.

**Landing pages** — indexability, title, meta description, headings, content
(thin, mostly-template, keyword missing from the intro, near-duplicate of
another landing page by exact 5-word-shingle Jaccard ≥ 0.70), images, links,
structured data *by page kind* (a location page wants LocalBusiness, a course
page Course), social cards, technical, URL, and performance once page speed has
run. A check failing on ≥ 80% of pages (and ≥ 8) is flagged as a **template
fault**: one fix clears it everywhere, so those are listed first.

**Cannibalization.** Two pages compete when their target keywords are the same
search — not when they merely share words. `/jaipur/clat-coaching` and
`/delhi/clat-coaching` share two of three words and are *different* searches;
`/jaipur` and `/bapu-nagar-jaipur` with 95% identical text are the same page
twice. Kinds: same-target, copied-spoke (neighbourhood page copying its city
page), numbered-series (only if content also overlaps), blog-vs-landing (only
when the post is a close variant: ≤1 extra word, no numbers — a topper's
profile does not compete with the toppers page), hub-spoke. Numbers are kept in
the signature because "CLAT 2025" and "CLAT 2026" are different searches; the
first version dropped them and reported 946 false pairs. Similarity too short to
measure is reported as unknown, never as 0%.

**Blog keyword density — the 150-word rule, measured two ways.** On average
(`mentions ≥ ceil(words / 150)`), and **per 150-word window** (a tail under 75
words joins the window before it) — because 10 mentions in the first paragraph
and none in the next 1,400 words passes on average and fails the reader. Each
article gets a verdict (every window / average only / fails), its gaps located
by their first words, and placement checks (title, H1, first 100 words, last
100 words). Density is occurrences ÷ words, the convention SEO tools use;
stuffing is > 3% overall or ≥ 6 mentions inside one window. The focus keyword
is Search Console's top query for the page when connected; otherwise the model
names a 2–4 word noun phrase from the title/H1/URL (verbs and question words are
stripped), and it must be *grounded* — made only of words on the page. Choices
are remembered per post, keyed by the text they came from, so re-runs are
reproducible and the model is asked again only when the post changes.

**Page speed.** Real Chromium, Lighthouse's mobile preset: 412×823 at 1.75×,
Slow 4G (150ms RTT, 1.6 Mbps down), CPU ×4, empty cache, fresh context per page,
four pages at a time (measured serially too — the numbers hold). Per page: LCP,
FCP, TTFB, CLS, TBT, load, bytes, requests, render-blocking count, the LCP
element, the heaviest resources, bytes by type, third-party hosts. Three things
make it precise rather than a number:

- **Every LCP candidate is kept, with the popup it sits in.** On
  lawpreptutorial.com the page's own content paints at ~2s and a promo modal
  (`div#popup_banner.modal`) opens at ~15–22s; being the largest thing painted,
  *it* is the LCP. The report shows both — "LCP 16.1s · popup · content 2.1s" —
  and the landing audit files the popup and a slow page as two separate
  findings with two separate fixes. An overlay is a `position: fixed` ancestor
  that is modal-like by name/role or covers half the screen; a fixed header is
  not one (there is a browser test for exactly that).
- **Layout shifts name the elements that moved**, per page and summed across
  the site, so a template section shifting on 20 pages shows up as one line.
- **Bytes come from the DevTools network log**, not Resource Timing, which
  reports 0 bytes for any cross-origin file without `Timing-Allow-Origin`. The
  old count put the home page at 2.2MB; the real figure is 4.7MB. Hosts are
  own / CDN (generic buckets like S3 and CloudFront — almost always the site's
  own files) / third-party.

Every request the browser makes — the page's images, iframes, scripts, their
`fetch()` calls, redirects — passes the same public-address check as the
crawler (`page_speed._guard`), and refused ones are aborted and listed on the
result. Without it a page containing `<img src="http://169.254.169.254/…">`
would have the browser fetch cloud metadata and the network log would save the
answer's size and timing. Measured on the same page, guarded and unguarded
loads differ by less than run-to-run noise (FCP 1,244 vs 1,280ms median).
The site's own hosts are **pinned** for the browser (`--host-resolver-rules`
to the addresses the resolver checked, `page_speed._pins`): the first full run
here "finished" with 802 of 1,139 pages unmeasured — every blog post failed with
`ERR_NAME_NOT_RESOLVED` in milliseconds, because Windows had cached one failed
lookup of the site. Pinned, a resolver outage cannot touch navigation, and the
browser connects to the address that was checked. Third-party hosts are not
pinned; a load in which *our* network failed under a third-party request (DNS,
disconnected) is marked `degraded`, re-measured after a 45s pause, and left out
of every statistic. A run with more than 3% of pages unmeasured ends as
**failed** with the reason, keeping what it did measure; running it again
measures only the rest. Residual: a third-party host that changes its DNS answer
between the guard's check and the browser's own lookup is not covered.

These are lab numbers — stricter than a typical real visit and comparable
across pages and runs. Field numbers are the Core Web Vitals panel (CrUX).

### Our network failing is not the site failing

A resolver hiccup mid-crawl once turned 1,059 of 1,140 live pages into
"could not resolve", and the audit **published** that — sitemap score 14,
landing average 20, zero blog posts — over a good report. Now: a request that
got no HTTP answer is retried twice with a pause, then once more in a slow
second pass; a crawl in which more than 3% of the site's own URLs still got no
answer (or half of the first 40) raises `CrawlUnreliable` and publishes
nothing, keeping the last good report; an unreachable robots.txt or own sitemap
stops discovery the same way. The job ends as *failed* with the reason, never
as *done* with the damage.

### Other defects caught by running it on a real site

Each is pinned by a test in `tests/test_deep_audit.py` (70+ tests):

- trafilatura's `deduplicate=True` is a cache shared across every call in the
  process, so the extraction depended on crawl order: of five identical centre
  pages, four extracted 277 words and the fifth **0**. It inflated "thin
  content", hid pages from the duplicate check and moved blog word counts; the
  landing median was 77 words when the real figure is 277;
- a login modal was extracted as "main content", so thin pages scored as 100%
  duplicates of each other;
- Python's salted `hash()` made shingle similarity differ between runs (now
  blake2b);
- a hyphenated keyword ("LLB-coaching") could not be matched;
- the focus keyword drifted between runs (model non-determinism) — and a
  partial crawl then erased every remembered keyword; they now expire only
  after 60 days unseen;
- the model returned verb phrases ("prepare up judiciary exam");
- density multiplied by keyword length, flagging 431 of 789 posts as stuffed;
- starting a running job again deadlocked every later status poll;
- a status poll read a half-written JSON file (writes are now atomic);
- "start" returned the *previous* run's finished document.

### Seeing the UI

`playwright` and `ffmpeg-static` are devDependencies for a reason: two rounds of
this rebrand were designed without ever looking at the result, and it showed.
`node scratchpad/shot.mjs <outdir> <light|dark>` drives a real Chromium through
sign-in, the brands list and a brand page, in both themes, and reports console
errors. Look at the screenshots before calling any visual change done.

### The mark

`BrandMark` in `lib/kit-ui.tsx` is scales of justice, red on the brand yellow,
rather than the wordmark: their own square favicon stacks the wordmark onto
three lines, which is legible at 100px and a smudge at the 16-30px this mark
renders at. Scales are already the brand's vocabulary — the site uses a
scales-of-justice icon across its course categories. `app/icon.svg` is the same
geometry with the colours written out literally, since a favicon cannot read
CSS variables. Keep the two in step.

Two things were deliberately **not** rebranded:

- **`test_seo_ga_attribution.py`** and the `_ga_section` docstring in
  `insights.py`. Both record a real production incident in which
  `berry-virtual` inherited `legal-soft`'s GA4 property and reported its 25,595
  sessions as its own. Renaming the companies in an incident report falsifies
  it; the names are history, not branding.
- **The personnel lists in `app/config.py`** — `CREATOR_EMAILS_DEFAULT` and
  `geo_editor_emails`. These are access grants for named people, not brand
  strings, and `geo_editor_emails` is inert here anyway (no route in this
  extract calls `require_geo_editor`). Deleting someone's access is not a
  side effect a rebrand should have.

`ALLOWED_EMAIL_DOMAINS` **is** now `lawpreptutorial.com`, so the old domain no
longer signs in. `backend/.env` sets `CREATOR_EMAILS` to an address on the new
domain, because Creator is what gates editing the brand registry
(`POST`/`DELETE /api/seo-geo/brands`) — without it you could sign in but not add
or remove a brand.

## Verification bar

Same as the parent repo, run from inside this folder:

```powershell
cd backend  ; .venv\Scripts\python -m pytest
cd frontend ; npm run typecheck ; npm run test
```

At the time of extraction: **142 backend tests passed, tsc clean, 50 frontend
tests passed, `next build` succeeded.** After the deep audit: **226 backend
tests, tsc clean, 50 frontend tests;** every deep-audit tab screenshotted at
1440px light and dark and at 400px, zero horizontal overflow, zero console
errors. `npm run lint` is knowingly broken in
the parent repo and is broken here too — tsc + vitest are the gate.

Run the tests with the backend's own interpreter, `backend\.venv\Scripts\python`.
The system Python has no trafilatura, and the extraction test fails under it in
a way that looks like a real regression.

---

## What is in `backend/`

Copied byte-identical from `ghi/backend`:

| Path | Why it is here |
|---|---|
| `agents/SEO GEO agent/seo_geo_agent/` | the agent itself, all 12 modules + its 8 test files and their conftest |
| `app/routers/seo_geo.py` | the agent's HTTP surface (618 lines) |
| `app/routers/auth.py` | the frontend cannot obtain a JWT without it |
| `app/routers/health.py` | `/api/health` |
| `app/security.py` | `get_current_user`, `require_creator` — the router's auth dependencies |
| `app/config.py` | `settings`, read by everything below |
| `app/services/firestore_repo.py` | the agent's persistence |
| `app/services/openrouter.py`, `runtime_config.py` | the LLM calls in `sources.py` |
| `app/services/run_tracking.py` | the activity trail the router writes to |
| `requirements.txt`, `Dockerfile`, `.dockerignore`, `.env.example` | unchanged |

Rewritten for this extract (three files, each says so in its own docstring):

- **`app/__init__.py`** — puts one agent root on `sys.path` instead of five.
- **`app/main.py`** — same middleware and error handlers as the original;
  router list cut to `health`, `auth`, `seo_geo`. The Marketing Research
  datastore exception handler and the Graphics Designer dynamic-brand
  registration went with the agents they belong to.
- **`conftest.py`** / **`pytest.ini`** — the offline guards kept (`SEO_OFFLINE`
  on, Firestore `_db` blocked, OpenRouter key blank); the DataForSEO and Cloud
  Storage guards dropped, since the agents that need them are not here.

**Added here, not in `ghi`:** `crawl.py`, `keyphrase.py`, `landing_audit.py`,
`cannibalization.py`, `keyword_density.py`, `page_speed.py`, `deep_audit.py`,
`jobs.py`, `keyword_pool.py`, `vitals.py` and `tests/test_deep_audit.py`;
`sitemap_health.py` was rewritten, and `sources.py` gained `_non_public`
(shared with the crawler's pinned resolver). Page speed needs Playwright's
Chromium: `.venv\Scripts\python -m playwright install chromium`.

**Not copied on purpose:** `ghi/backend/.env` and `gcp-service-account.json`,
which hold live credentials. Use `.env.example` and supply your own.

## What is in `frontend/`

Copied byte-identical from `ghi/newfrontend`:

| Path | Why it is here |
|---|---|
| `components/console/seo/SeoAgent.tsx`, `labs.tsx` | the panel, 1,779 lines between them |
| `components/LoginScreen.tsx` | sign-in; its only imports are already in this closure |
| `lib/api.ts` | the whole client. Kept whole rather than trimmed to the ~30 `seo*` calls — cutting a 3,510-line module by hand is how you introduce a bug the tests do not catch |
| `lib/auth.tsx`, `kit-ui.tsx`, `icons.tsx`, `glyph.tsx`, `load.ts`, `requestPolicy.ts` | what the two SEO files import, transitively |
| `lib/*.test.ts` | the three test files covering that lib |
| `app/layout.tsx`, `app/backend/[...path]/route.ts` | shell and the same-origin Cloud Run relay |
| `app/seo.css` + `console.css`, `kit-ui.css`, `agenthub.css`, `hub-theme.css`, `hub-live.css`, `../styles.css`, `tokens/` | the panel's styling. Both design systems load, as in the console — `hub-live.css` pins `--surface` inside `.legacy`, which is the wrapper this panel renders in |
| `public/glyph/`, `public/logo/` | the only assets this closure references |
| `tsconfig.json`, `next.config.mjs`, `postcss.config.mjs`, `next-env.d.ts` | unchanged |

Rewritten for this extract:

- **`app/page.tsx`** — the console reaches this panel through AgentHub, which
  supplies two things `SeoAgent` depends on: the `.legacy` wrapper and an
  `onToast`. Neither AgentHub nor `ConsoleApp` belongs in a SEO-only extract, so
  this page reproduces exactly those two, with `ConsoleApp`'s toast behaviour
  lifted unchanged. `onBack` is a no-op — there is no agent grid to go back to.
- **`components/console/ConsoleApp.tsx`** — a stub holding only `ToastTone` and
  `ToastFn`. `SeoAgent.tsx` and `labs.tsx` import the type from there, and this
  keeps both files byte-identical to their originals.
- **`app/globals.css`** — the console's, minus the five other agents' panel
  sheets.
- **`package.json`** — `konva`, `react-konva` and `use-image` dropped; those are
  the Graphics Designer's.

## Things worth knowing

- **The relay's fallback URL is production.**
  `app/backend/[...path]/route.ts` defaults `UPSTREAM` to the live Cloud Run
  service, exactly as in `ghi`. It is only reached when `NEXT_PUBLIC_API_URL`
  is set (production), and `BACKEND_ORIGIN` overrides it — but if you deploy
  this extract, set `BACKEND_ORIGIN` deliberately.
- **This extract has no cron.** The SEO sweep runs from `app/routers/cron.py`
  in the parent repo, which is not here. The agent's endpoints all work; the
  scheduled sweep does not exist in this copy.
- **It shares the parent's Firestore.** `firestore_repo` reads the same
  `settings` and will point at whatever database `.env` names. Point it at
  staging (`agentos-staging`), not `lsbrandkit`, unless you mean it.
- **Divergence starts now.** Nothing syncs this copy back to `ghi`. A fix made
  here has to be carried across by hand.
- **The older fetchers still check-then-resolve.** `sources._safe_get` /
  `fetch_page` (audits, briefs) check an address and then let httpx resolve the
  name again — the DNS-rebinding gap the crawler's pinned resolver closes. They
  only take URLs from the brand's own config and crawl today; give them
  `crawl._client()`'s pinned transport before they take anything less trusted.
