"""The deep audit — one crawl, every diagnostic, as a background job.

Order of work, and why it is this order:

1. **Discover** every sitemap and every URL in them.
2. **Crawl** all of them once. Every check below reads this one snapshot, so
   the landing audit, the sitemap diagnosis and the cannibalization report can
   never disagree about what a page said.
3. **Probe** the handful of extra URLs the sitemap diagnosis needs (host and
   trailing-slash variants, pages linked but unlisted).
4. **Sitemap** diagnosis.
5. **Landing pages**, with real-browser performance folded in when the page
   speed job has already measured them.
6. **Cannibalization**, confirmed against Search Console when connected.
7. **Blog keyword density**, the only step that calls the language model (to
   name each post's focus keyword), so it runs last and cannot hold the rest up.

Page speed is a separate job (``page_speed``), not a phase here: it takes over
an hour for a site this size, and nobody should wait that long for a sitemap
report. It reads the crawl this job saves.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from . import (
    cannibalization, crawl, jobs, keyword_density, landing_audit, page_speed,
    sitemap_health, state,
)
from .crawl import norm_url

_CRAWL = "crawl-{}"
_SUMMARY = "deepaudit-{}"


def _gsc_top(rows: list) -> dict[str, str]:
    """{norm_url(page): the query that page earns the most impressions for}."""
    best: dict[str, tuple[int, str]] = {}
    for r in rows or []:
        key = norm_url(r.page)
        if r.impressions > best.get(key, (-1, ""))[0]:
            best[key] = (r.impressions, r.query)
    return {k: q for k, (_, q) in best.items()}


def run(brand: dict, progress: jobs.Progress, *, rows_fn=None) -> dict:
    domain = brand["domain"]

    progress.phase("discovering sitemaps")
    cli = crawl._client()
    try:
        disc = crawl.discover(domain, cli)
        n_urls = len(disc["urls"])
        progress.note(f"{len(disc['sitemaps'])} sitemaps, {n_urls} URLs")
        if disc.get("truncated"):
            progress.note(f"{disc['truncated']} more sitemap URLs past the {crawl.MAX_URLS:,} cap were not crawled")

        progress.phase("crawling every URL", total=n_urls)
        records, texts = crawl.crawl(domain, disc["urls"], cli=cli, on_page=lambda _r: progress.step())
        live = sum(1 for r in records if r.get("status") == 200)
        progress.note(f"crawled {len(records)} pages, {live} returned 200")
        jobs.save_list(_CRAWL.format(brand["id"]), records, meta={"domain": domain})

        progress.phase("probing host, slash and unlisted variants")
        probes = sitemap_health.probe(domain, records, set(disc["urls"]), cli)
    finally:
        cli.close()

    progress.phase("diagnosing sitemaps")
    sm = sitemap_health.build(brand, disc, records, probes)

    rows, notes = [], []
    if rows_fn:
        try:
            rows, notes = rows_fn(brand)
        except Exception as exc:  # noqa: BLE001 — Search Console is optional here
            notes = [f"Search Console: {exc}"]
    gsc_top = _gsc_top(rows)

    progress.phase("auditing landing pages")
    speed = page_speed.by_url(brand["id"])
    la = landing_audit.build(brand, records, texts, speed=speed, gsc_top=gsc_top,
                             robots_parser=disc.get("_robots_parser"))

    progress.phase("checking keyword cannibalization")
    cb = cannibalization.build(brand, records, texts, gsc_rows=rows, gsc_top=gsc_top)

    posts = sum(1 for r in records if r.get("type") == "blog_post" and r.get("status") == 200)
    progress.phase("measuring blog keyword density", total=posts)
    kd = keyword_density.build(brand, records, texts, gsc_top=gsc_top, progress=progress)

    types = defaultdict(int)
    for r in records:
        types[r.get("type", "unknown")] += 1
    summary = {
        "at": date.today().isoformat(),
        "domain": domain,
        "urls": n_urls,
        "sitemaps": len(disc["sitemaps"]),
        "pages_by_type": dict(types),
        "live_pages": live,
        "gsc_connected": bool(rows),
        "notes": notes,
        "sitemap": {"score": sm["score"], "affected": sm["affected_urls"], "issues": len(sm["issues"])},
        "landing": {"pages": la["pages"], "avg_score": la["avg_score"], "high": la["high_total"],
                    "template_issues": la["template_issues"], "grades": la["grades"]},
        "cannibalization": {"pairs": cb["pairs"], "high": cb["by_severity"]["high"],
                            "medium": cb["by_severity"]["medium"], "confirmed": len(cb["confirmed"])},
        "density": {"posts": kd["posts"], "pass": kd["pass_every_window"],
                    "average_only": kd["pass_on_average_only"], "fail": kd["fail"],
                    "median_wpm": kd["median_words_per_mention"]},
    }
    state.save(_SUMMARY.format(brand["id"]), summary)
    progress.note("deep audit complete")
    return summary


def refresh_landing(brand: dict) -> dict | None:
    """Re-run the landing audit with the latest page-speed results folded in,
    without re-crawling.

    Page speed finishes long after the crawl, and the landing audit's
    performance checks need it. Everything else the audit reads is in the saved
    crawl — except main-content text, which is never persisted. Text is only
    used to find near-duplicates, so the pairs the last audit found are carried
    forward instead of recomputed.
    """
    records = latest_crawl(brand["id"])
    prev = landing_audit.latest(brand["id"])
    if not records or not prev:
        return None
    dupes: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for p in prev.get("near_duplicate_pairs") or []:
        dupes[p["a"]].append((p["b"], p["similarity"]))
        dupes[p["b"]].append((p["a"], p["similarity"]))
    for k in dupes:
        dupes[k].sort(key=lambda x: -x[1])
    doc = landing_audit.build(brand, records, {}, speed=page_speed.by_url(brand["id"]), dupes=dupes)
    summary = latest(brand["id"])
    if summary:
        summary["landing"] = {"pages": doc["pages"], "avg_score": doc["avg_score"],
                              "high": doc["high_total"], "template_issues": doc["template_issues"],
                              "grades": doc["grades"]}
        state.save(_SUMMARY.format(brand["id"]), summary)
    return doc


def latest(brand_id: str) -> dict | None:
    return state.load(_SUMMARY.format(brand_id))


def latest_crawl(brand_id: str) -> list[dict]:
    return jobs.load_list(_CRAWL.format(brand_id))[0]
