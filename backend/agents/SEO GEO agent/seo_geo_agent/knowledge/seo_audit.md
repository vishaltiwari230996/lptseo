# SEO audit judgement (distilled from the seo-audit playbook)

## Priority order — always diagnose in this sequence
1. CRAWLABILITY & INDEXATION — can Google find and index it? A blocked or
   noindexed page earns zero whatever else is right. Fixing one is unblocking,
   not optimisation: expect the page to recover its natural rank, fast.
2. TECHNICAL FOUNDATIONS — speed, mobile, HTTPS, canonical consistency.
3. ON-PAGE — titles, headings, content, internal links.
4. CONTENT QUALITY — does it deserve to rank? (E-E-A-T, depth, intent match)
5. AUTHORITY — links and brand. The slowest lever; never recommend it as the
   first fix when tier 1-3 findings exist.

## Reading the signals
- Sitemap faults are CONTRADICTIONS: the sitemap says "index this" while the
  page 404s, redirects, says noindex, canonicals elsewhere, or robots.txt
  blocks it. Google resolves contradictions however it likes — the site loses
  control of its own indexing. Fix = make the two signals agree.
- A check failing on ~80%+ of pages is a TEMPLATE fault: one fix in the shared
  template clears every page at once. Always surface template faults before
  per-page work — best effort-to-impact ratio on a templated site.
- Titles: unique per page, primary keyword near the start, 50-60 chars,
  brand at the end. Duplicate titles = pages competing with themselves.
- One H1 per page, carrying the target keyword; logical H1→H2→H3 tree.
- Thin content: a commercial landing page needs 400-600+ words SPECIFIC to
  that page (for a coaching centre: faculty, batches, results, address, fees,
  reviews). Template copy shared with sibling pages does not count.
- Orphan pages get no internal link equity: link from the hub (city/service
  page) with a descriptive keyword anchor, never "click here".
- Cannibalization: two pages targeting one query split links, relevance and
  clicks; Google often ranks the WRONG one (a blog post over the money page).
  Hub-and-spoke ("clat coaching jaipur" ⊃ "clat coaching vaishali nagar") is
  healthy architecture UNLESS the spoke is a near-copy of the hub. Fix: merge
  same-target pages; differentiate near-copy spokes; make the blog post link
  to, not compete with, the commercial page.
- Core Web Vitals: LCP ≤2.5s, INP ≤200ms, CLS ≤0.1 at the 75th percentile.
  Field data (CrUX — what real visitors got) is the ranking signal; lab data
  (Lighthouse) is the diagnostic that says WHY. Quote field for "are we ok",
  lab for "what to fix".

## Traffic-drop triage (in order)
1. Indexation first: did pages fall out of the index? (coverage, noindex,
   canonical flips, sitemap errors)
2. Site-wide vs page-level: a site-wide drop suggests an algorithm update or
   technical fault; scattered pages suggest decay or new competitors.
3. Rank lost vs CTR lost: impressions steady + clicks down = snippet problem
   (title/meta); impressions down = rank problem.
4. Content decay: pages that slipped gradually usually need a refresh —
   updated facts, dates, internal re-promotion — not a rewrite.

## CTR expectations by position (for estimating opportunity)
#1 ≈28%, #2 ≈15%, #3 ≈11%, #5 ≈7%, #10 ≈2.3%, below #10 ≈1.5%. A page ranking
top-5 with CTR far below these is a title/meta problem — the cheapest fix in
SEO because no ranking change is needed.

## How to advise
- Name the exact page and the exact change; never say "improve content".
- Rank advice by (pages affected × severity ÷ effort). One template fix beats
  fifty page edits.
- Striking distance (positions 4-20) is where content work pays fastest;
  position >30 is a content/authority gap, not a tweak.
- Never promise a number the data does not support; estimates are labelled
  estimates.
