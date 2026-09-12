"""Sitemap diagnosis — every sitemap file, and every URL in every sitemap.

A sitemap is the site telling Google, in its own words, which URLs it wants
indexed. Nearly every sitemap fault is a *contradiction* between that statement
and another signal the site sends:

  * the sitemap lists a URL, and the URL returns 404, or redirects elsewhere;
  * the sitemap lists a URL, and the page itself says ``noindex``;
  * the sitemap lists a URL, and the page's canonical points to a different URL;
  * the sitemap lists a URL, and robots.txt forbids Googlebot from fetching it;
  * robots.txt names a sitemap, and that sitemap does not exist.

Each is a site sending Google two instructions that cannot both be followed.
Google resolves them however it likes, and the site stops controlling which
pages get indexed. This module finds every one, for every URL, and names each
affected URL — never a sample, because the fix is per URL.

Split in two on purpose: :func:`probe` does the few extra network requests the
diagnosis needs (canonical-host and trailing-slash variants, and the pages that
are linked but missing from every sitemap), and :func:`build` is a pure
function of already-fetched data, so every check in it can be tested offline.
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from datetime import date
from posixpath import basename
from urllib.parse import urlparse

from . import jobs, state
from .crawl import dissect, downgrades, fetch, norm_url

_DOC = "sitemap-{}"
_URLS = "sitemapurls-{}"

URL_LIMIT = 50_000
BYTE_LIMIT = 50 * 1024 * 1024

#: How many trailing-slash variants to try per site section. A section is a
#: first path segment (/blog, /jaipur...). The goal is to prove or disprove a
#: *site-wide* behaviour, not to test every page twice.
VARIANT_SAMPLE = 6

#: Linked-but-unlisted pages to actually fetch, so the finding reports pages
#: that really are live and indexable, not just hrefs that exist.
UNLISTED_FETCH = 150

_SOFT404 = re.compile(r"\b(404|not found|page not found|doesn[’']?t exist|no longer available)\b", re.I)
_SEV = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _path(url: str) -> str:
    u = urlparse(url)
    return (u.path or "/") + (f"?{u.query}" if u.query else "")


def _short(url: str, domain: str) -> str:
    return re.sub(rf"^https?://(www\.)?{re.escape(domain.removeprefix('www.'))}", "", url) or "/"


# --------------------------------------------------------------------------- #
# Probe — the network part
# --------------------------------------------------------------------------- #

def probe(domain: str, records: list[dict], sitemap_keys: set[str], cli) -> dict:
    """Extra requests the diagnosis needs, beyond the crawl itself.

    Three questions, each answerable only by asking the server:

    1. **Canonical host.** Do ``http://`` and the non-``www`` host redirect to
       the one host the sitemap uses? If they serve 200, the whole site exists
       twice.
    2. **Trailing slash.** Does the *other* slash variant of a URL redirect to
       the listed one? If both answer 200, every page has a duplicate.
    3. **Linked but unlisted.** Pages other pages link to that no sitemap
       lists — fetched, so the finding names pages that are actually live.
    """
    host = domain.lower()
    bare = host.removeprefix("www.")
    canonical_host = urlparse(records[0]["url"]).netloc.lower() if records else host
    host_checks = []
    for variant in sorted({f"http://{canonical_host}/", f"https://{bare}/", f"https://www.{bare}/",
                           f"http://{bare}/"} - {f"https://{canonical_host}/"}):
        got = fetch(cli, variant)
        canonical = ""
        if got.status == 200 and got.content:
            canonical = dissect(got, domain, [], None)[0].get("canonical", "")
        host_checks.append({
            "url": variant,
            "status": (got.chain[0]["status"] if got.chain else got.status),
            "final_url": got.final_url,
            "hops": len(got.chain),
            "ok": norm_url(got.final_url) == norm_url(f"https://{canonical_host}/")
                  and got.final_url.startswith("https://"),
            # What the un-redirected copy claims. A canonical back to https
            # limits duplicate INDEXING; it does not stop the insecure copy
            # being crawled, linked to, or served to visitors.
            "canonical": canonical,
            "error": got.error,
        })

    # HSTS: the header that makes browsers refuse http for this host at all.
    home = fetch(cli, f"https://{canonical_host}/")
    hsts = home.headers.get("strict-transport-security", "")

    # Trailing-slash behaviour, sampled per first path segment.
    by_section: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("status") == 200 and r.get("type") not in ("offsite",):
            path = urlparse(r["url"]).path or "/"
            if path == "/":
                continue
            seg = path.strip("/").split("/")[0]
            by_section[seg if path.count("/") > 1 else "(root)"].append(r)
    slash_checks = []
    for section, recs in by_section.items():
        for r in recs[:VARIANT_SAMPLE]:
            url = r["url"]
            alt = url[:-1] if url.endswith("/") else url + "/"
            got = fetch(cli, alt)
            canonical_back = ""
            if got.status == 200 and got.content:
                rec, _ = dissect(got, domain, [], None)
                canonical_back = rec.get("canonical", "")
            slash_checks.append({
                "section": section,
                "listed": url,
                "variant": alt,
                "status": got.chain[0]["status"] if got.chain else got.status,
                "final_url": got.final_url,
                "redirects_to_listed": bool(got.chain) and norm_url(got.final_url) == norm_url(url)
                                       and got.final_url.rstrip("/") == url.rstrip("/")
                                       and (got.final_url.endswith("/") == url.endswith("/")),
                "canonical_to_listed": bool(canonical_back)
                                       and canonical_back.rstrip("/") == url.rstrip("/")
                                       and canonical_back.endswith("/") == url.endswith("/"),
            })

    # Linked from the crawl, absent from every sitemap.
    inbound: Counter = Counter()
    example_source: dict[str, str] = {}
    for r in records:
        for t in r.get("internal_links") or []:
            if t in sitemap_keys:
                continue
            path = t.split("/", 1)[1] if "/" in t else ""
            # Files and obvious non-pages are not sitemap material.
            if re.search(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|mp4|css|js)(\?|$)", path, re.I):
                continue
            if "?" in t or "/wp-" in t or "/cdn-cgi/" in t:
                continue
            inbound[t] += 1
            example_source.setdefault(t, r["url"])
    unlisted = []
    for key, n in inbound.most_common(UNLISTED_FETCH):
        # `key` is a norm_url — host without www, then the path. Rebuild it on
        # the canonical host so the fetch tests the URL a searcher would reach.
        path = key.split("/", 1)[1] if "/" in key else ""
        url = f"https://{canonical_host}/{path}"
        got = fetch(cli, url)
        rec = {"status": got.status, "noindex": False, "canonical_kind": "missing", "title": ""}
        if got.status == 200 and got.content:
            rec, _ = dissect(got, domain, [], None)
        unlisted.append({
            "url": url,
            "inlinks": n,
            "linked_from": example_source.get(key, ""),
            "status": rec.get("status", got.status),
            "final_url": got.final_url,
            "redirected": bool(got.chain),
            "noindex": rec.get("noindex", False),
            "canonical_kind": rec.get("canonical_kind", "missing"),
            "title": rec.get("title", ""),
        })
        time.sleep(0.02)

    return {"host_checks": host_checks, "slash_checks": slash_checks, "unlisted": unlisted,
            "hsts": hsts}


# --------------------------------------------------------------------------- #
# Diagnosis — pure
# --------------------------------------------------------------------------- #

def _issue(code: str, severity: str, title: str, why: str, fix: str,
           urls: list[str] | None = None, detail: list[dict] | None = None) -> dict:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "why": why,
        "fix": fix,
        "count": len(urls or detail or []),
        "urls": urls or [],
        "detail": detail or [],
    }


def build(brand: dict, discovery: dict, records: list[dict], probes: dict | None = None) -> dict:
    """Every sitemap file and every sitemap URL, checked against every other signal."""
    domain = brand["domain"]
    probes = probes or {}
    sitemaps = discovery.get("sitemaps") or []
    robots = discovery.get("robots") or {}
    parser = discovery.get("_robots_parser")
    issues: list[dict] = []
    short = lambda u: _short(u, domain)  # noqa: E731

    by_key = {norm_url(r["url"]): r for r in records}

    # ================================================================ robots.txt
    if robots.get("status") != 200:
        issues.append(_issue(
            "robots-missing", "high",
            f"robots.txt returned {robots.get('status') or 'nothing'}",
            "Without a readable robots.txt, crawlers get no sitemap declaration and no crawl rules.",
            "Serve a robots.txt at the site root with 200, naming every live sitemap.",
        ))
    if robots.get("status") == 200 and not robots.get("declared"):
        issues.append(_issue(
            "robots-no-sitemap", "medium",
            "robots.txt names no sitemap",
            "Crawlers that read robots.txt first are never told where the sitemap is.",
            f"Add 'Sitemap: https://{domain}/sitemap.xml' to robots.txt.",
        ))

    # ============================================================ sitemap files
    live_names = {basename(urlparse(s["url"]).path): s for s in sitemaps if s["status"] == 200}
    dead_declared = [s for s in sitemaps if s["declared_in_robots"] and s["status"] != 200]
    if dead_declared:
        detail = []
        for s in dead_declared:
            twin = live_names.get(basename(urlparse(s["url"]).path))
            detail.append({
                "sitemap": short(s["url"]),
                "status": s["status"] or s.get("error") or "unreachable",
                "real_file": short(twin["url"]) if twin else None,
                "note": (f"a live file with the same name exists at {short(twin['url'])} — "
                         "robots.txt has the wrong path") if twin
                        else "no sitemap of this name exists anywhere — the declaration is stale",
            })
        issues.append(_issue(
            "sitemap-declared-dead", "high",
            f"robots.txt declares {len(dead_declared)} sitemap(s) that do not load",
            "Google fetches every sitemap robots.txt names. A dead one is reported as a "
            "sitemap error in Search Console and tells Google nothing about the URLs it was "
            "meant to carry.",
            "Correct each path to the real file, or remove the line if the sitemap no longer exists.",
            detail=detail,
        ))

    dead_indexed = [s for s in sitemaps if not s["declared_in_robots"] and s["status"] != 200]
    if dead_indexed:
        issues.append(_issue(
            "sitemap-child-dead", "high",
            f"{len(dead_indexed)} sitemap(s) listed in an index do not load",
            "A sitemap index that points at a missing file hides every URL that file was meant to list.",
            "Fix the path in the index, or remove the entry.",
            detail=[{"sitemap": short(s["url"]), "status": s["status"] or s.get("error"),
                     "listed_in": [short(p) for p in s["parents"]]} for s in dead_indexed],
        ))

    redirected_sm = [s for s in sitemaps if s.get("redirected")]
    if redirected_sm:
        issues.append(_issue(
            "sitemap-redirects", "medium",
            f"{len(redirected_sm)} sitemap URL(s) redirect",
            "Reference sitemaps at their final address; a redirecting sitemap URL is fragile and "
            "some crawlers do not follow it.",
            "Update robots.txt and the index to the final URL.",
            detail=[{"sitemap": short(s["url"]), "final": short(s["final_url"])} for s in redirected_sm],
        ))

    unparseable = [s for s in sitemaps if s["kind"] == "unparseable"]
    if unparseable:
        issues.append(_issue(
            "sitemap-unparseable", "high",
            f"{len(unparseable)} sitemap(s) return 200 but are not valid sitemap XML",
            "A sitemap Google cannot parse contributes nothing.",
            "Serve well-formed XML with a <urlset> or <sitemapindex> root.",
            detail=[{"sitemap": short(s["url"]), "content_type": s.get("content_type")} for s in unparseable],
        ))

    empty = [s for s in sitemaps if s["kind"] == "urlset" and s["url_count"] == 0]
    if empty:
        issues.append(_issue(
            "sitemap-empty", "low", f"{len(empty)} sitemap(s) are empty",
            "An empty sitemap is harmless but is dead weight that implies a broken generator.",
            "Remove it or fix the generator that produces it.",
            detail=[{"sitemap": short(s["url"])} for s in empty],
        ))

    oversize = [s for s in sitemaps if s["url_count"] > URL_LIMIT or s["bytes"] > BYTE_LIMIT]
    if oversize:
        issues.append(_issue(
            "sitemap-oversize", "high",
            f"{len(oversize)} sitemap(s) exceed 50,000 URLs or 50MB",
            "Google ignores the part of a sitemap beyond its size limits.",
            "Split it into several sitemaps under an index.",
            detail=[{"sitemap": short(s["url"]), "urls": s["url_count"], "bytes": s["bytes"]} for s in oversize],
        ))

    lm_invalid = [s for s in sitemaps if s.get("lastmod_invalid")]
    if lm_invalid:
        issues.append(_issue(
            "lastmod-invalid", "medium",
            "lastmod values not in W3C Datetime format",
            "Google only reads lastmod in W3C Datetime format; anything else is discarded.",
            "Emit YYYY-MM-DD or a full ISO-8601 timestamp with timezone.",
            detail=[{"sitemap": short(s["url"]), "invalid": s["lastmod_invalid"]} for s in lm_invalid],
        ))
    lm_future = [s for s in sitemaps if s.get("lastmod_future")]
    if lm_future:
        issues.append(_issue(
            "lastmod-future", "medium", "lastmod dates in the future",
            "A future lastmod is an obvious lie about freshness; Google learns to distrust the field.",
            "Set lastmod from the real content-modified timestamp.",
            detail=[{"sitemap": short(s["url"]), "future": s["lastmod_future"]} for s in lm_future],
        ))
    no_lm = [s for s in sitemaps if s["kind"] == "urlset" and s["url_count"] and s["lastmod_count"] == 0]
    if no_lm:
        missing = sum(s["url_count"] for s in no_lm)
        issues.append(_issue(
            "lastmod-missing", "medium",
            f"{len(no_lm)} sitemap(s) carry no lastmod at all ({missing} URLs)",
            "Without lastmod, Google has no signal about what changed and re-crawls these URLs on "
            "its own schedule — slower re-indexing after every content update.",
            "Emit lastmod per URL from the real content-modified timestamp (not the build time).",
            detail=[{"sitemap": short(s["url"]), "urls": s["url_count"]} for s in no_lm],
        ))

    nested = [s for s in sitemaps if s["kind"] == "index" and s["parents"]]
    if nested:
        issues.append(_issue(
            "sitemap-nested-index", "info",
            f"{len(nested)} sitemap index(es) are nested inside another index",
            "The URLs they carry are reached two levels deep. They are also declared directly in "
            "robots.txt here, which is what makes them reliably discoverable — do not remove those "
            "declarations while the nesting remains.",
            "Prefer one flat index that lists every URL sitemap directly.",
            detail=[{"index": short(s["url"]), "inside": [short(p) for p in s["parents"]]} for s in nested],
        ))

    # ============================================================== every URL
    url_rows: list[dict] = []
    non200, redirects, noindexed, canon_elsewhere, downgraded = [], [], [], [], []
    blocked, http_urls, soft404, orphans, dup_listed, param_urls, upper_urls = [], [], [], [], [], [], []
    long_urls, underscore_urls = [], []
    content_groups: dict[str, list[str]] = defaultdict(list)
    hosts: Counter = Counter()
    mixed_pages: list[dict] = []
    mixed_hosts: Counter = Counter()

    for key, entry in (discovery.get("urls") or {}).items():
        r = by_key.get(key) or {}
        url = entry["url"]
        problems: list[str] = []
        status = r.get("status", 0)
        hosts[urlparse(url).netloc.lower()] += 1

        if r.get("type") == "offsite":
            problems.append("off-site")
        elif status != 200:
            if r.get("chain"):
                final = r.get("final_url", "")
                redirects.append({"url": short(url), "status": r["chain"][0]["status"],
                                  "final": short(final), "final_status": status,
                                  "hops": len(r["chain"])})
                problems.append(f"redirects {r['chain'][0]['status']}")
                if status != 200:
                    non200.append({"url": short(url), "status": status or r.get("error", "unreachable"),
                                   "note": f"after redirect to {short(final)}"})
            else:
                non200.append({"url": short(url), "status": status or r.get("error", "unreachable")})
                problems.append(f"status {status or 'unreachable'}")
        elif r.get("chain"):
            redirects.append({"url": short(url), "status": r["chain"][0]["status"],
                              "final": short(r.get("final_url", "")), "final_status": 200,
                              "hops": len(r["chain"])})
            problems.append(f"redirects {r['chain'][0]['status']}")
        if r.get("chain") and downgrades(r["chain"], r.get("final_url", "")):
            downgraded.append({
                "url": short(url),
                "chain": " → ".join([h["url"] for h in r["chain"]] + [r.get("final_url", "")]),
            })
            problems.append("redirect via http")

        if r.get("noindex"):
            noindexed.append({"url": short(url), "via": "X-Robots-Tag" if "noindex" in (r.get("x_robots_tag") or "").lower() else "meta robots"})
            problems.append("noindex")
        if status == 200 and r.get("canonical_kind") == "other":
            canon_elsewhere.append({"url": short(url), "canonical": short(r["canonical"])})
            problems.append("canonical elsewhere")
        if parser is not None:
            try:
                if not parser.can_fetch("Googlebot", url):
                    blocked.append(short(url))
                    problems.append("blocked by robots.txt")
            except Exception:  # noqa: BLE001 — a malformed robots.txt is reported above
                pass
        if url.startswith("http://"):
            http_urls.append(short(url))
            problems.append("http://")
        if len(entry.get("sitemaps") or []) > 1:
            dup_listed.append({"url": short(url), "in": [short(s) for s in entry["sitemaps"]]})
            problems.append("listed twice")
        path = urlparse(url).path
        if urlparse(url).query:
            param_urls.append(short(url))
            problems.append("query string")
        if path != path.lower():
            upper_urls.append(short(url))
            problems.append("uppercase")
        if "_" in path:
            underscore_urls.append(short(url))
        if len(url) > 115:
            long_urls.append(short(url))
        if status == 200 and r.get("title") is not None and r.get("type") != "offsite":
            h1 = " ".join(r.get("h1") or [])
            if (_SOFT404.search(r.get("title", "")) or _SOFT404.search(h1)) or (
                    r.get("word_count", 0) < 40 and r.get("word_count_body", 0) < 120):
                soft404.append({"url": short(url), "title": r.get("title", "")[:90],
                                "words": r.get("word_count", 0)})
                problems.append("soft 404?")
            if r.get("inlinks", 0) == 0 and r.get("type") != "home":
                orphans.append(short(url))
                problems.append("orphan")
            if r.get("content_hash") and r.get("word_count", 0) >= 50:
                content_groups[r["content_hash"]].append(short(url))
            # Checked on EVERY sitemap page, blog posts included — the landing
            # audit covers landing pages only, and on this site the broken
            # resources were all in blog articles.
            if r.get("mixed_content"):
                sample = r.get("mixed_content_urls") or []
                mixed_pages.append({"url": short(url), "resources": r["mixed_content"], "examples": sample[:2]})
                for u in sample:
                    mixed_hosts[urlparse(u).hostname or "?"] += 1
                problems.append(f"{r['mixed_content']} http resources")

        url_rows.append({
            "url": short(url),
            "type": r.get("type", "unknown"),
            "subtype": r.get("subtype", ""),
            "status": status,
            "final": short(r["final_url"]) if r.get("chain") else "",
            "noindex": bool(r.get("noindex")),
            "canonical": r.get("canonical_kind", ""),
            "lastmod": entry.get("lastmod"),
            "inlinks": r.get("inlinks", 0),
            "sitemaps": [short(s) for s in entry.get("sitemaps") or []],
            "problems": problems,
        })

    total = len(url_rows)
    if non200:
        issues.append(_issue(
            "url-non200", "high", f"{len(non200)} sitemap URL(s) do not return 200",
            "Every dead URL in a sitemap is a URL Google is told to index and cannot. At scale it "
            "erodes Google's trust in the whole file.",
            "Remove dead URLs from the sitemap, or restore the pages. 410 is the right code for "
            "content that is gone for good.",
            detail=non200,
        ))
    if redirects:
        issues.append(_issue(
            "url-redirect", "medium", f"{len(redirects)} sitemap URL(s) redirect",
            "A sitemap should list final destinations. Each redirect costs a crawl and tells "
            "Google the listed URL is not the one to index.",
            "Replace each with its final URL.",
            detail=redirects,
        ))
    if downgraded:
        issues.append(_issue(
            "url-redirect-downgrade", "high",
            f"{len(downgraded)} sitemap URL(s) redirect through plain http",
            "The chain starts on https and passes through an http hop before returning to https. "
            "It lands in the right place, which is why it goes unnoticed — but that hop is sent "
            "unencrypted, where it can be intercepted and rewritten, and it is a wasted crawl.",
            "Make the server issue ONE redirect straight to the final https URL. This usually means "
            "the slash-adding rule runs before the https rule and builds an http URL — reorder them, "
            "or build the Location from the request's scheme.",
            detail=downgraded,
        ))
    if noindexed:
        issues.append(_issue(
            "url-noindex", "high", f"{len(noindexed)} sitemap URL(s) are noindex",
            "The sitemap asks Google to index these; the page tells it not to. Contradictory "
            "signals — Search Console reports every one as 'Submitted URL marked noindex'.",
            "Either remove them from the sitemap or remove the noindex, whichever is intended.",
            detail=noindexed,
        ))
    if canon_elsewhere:
        issues.append(_issue(
            "url-canonical-elsewhere", "high",
            f"{len(canon_elsewhere)} sitemap URL(s) canonicalise to a different URL",
            "A sitemap should list only canonical URLs. Listing a URL whose canonical points "
            "elsewhere asks Google to index a page the page itself disowns.",
            "List the canonical target instead, or fix the canonical if it is wrong.",
            detail=canon_elsewhere,
        ))
    if blocked:
        issues.append(_issue(
            "url-robots-blocked", "high", f"{len(blocked)} sitemap URL(s) are blocked by robots.txt",
            "Googlebot is told to index these and forbidden to fetch them.",
            "Unblock them in robots.txt or drop them from the sitemap.",
            urls=blocked,
        ))
    if http_urls:
        issues.append(_issue(
            "url-http", "high", f"{len(http_urls)} sitemap URL(s) use http://",
            "The sitemap must list the canonical https URL; http forces a redirect per URL.",
            "Emit https URLs.", urls=http_urls,
        ))
    if len(hosts) > 1:
        issues.append(_issue(
            "url-mixed-hosts", "medium", "Sitemap URLs use more than one hostname",
            "Mixed www / non-www hosts mean some listed URLs are not canonical.",
            "Use one canonical host for every URL.",
            detail=[{"host": h, "urls": n} for h, n in hosts.most_common()],
        ))
    if soft404:
        issues.append(_issue(
            "url-soft404", "medium", f"{len(soft404)} sitemap URL(s) look like soft 404s",
            "They return 200 but carry an error-page title or almost no content. Google treats "
            "these as soft 404s and drops them, while the sitemap keeps asking for them.",
            "Return a real 404/410 for missing content, or give the page real content.",
            detail=soft404,
        ))
    if mixed_pages:
        top = mixed_hosts.most_common(1)[0][0] if mixed_hosts else ""
        raw_ip = bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", top))
        issues.append(_issue(
            "url-mixed-content", "high",
            f"{len(mixed_pages)} sitemap page(s) load {sum(p['resources'] for p in mixed_pages)} "
            f"resource(s) over plain http",
            "On an https page the browser blocks http scripts and iframes outright, and tries to "
            "upgrade http images to https — which fails when the host has no certificate for that "
            "name, as a raw IP address never does. Either way the resource does not appear: broken "
            "images in the article for every visitor, and Google renders the same broken page."
            + (f" Most point at {top}, a raw server IP — an origin address leaking into published "
               "content." if raw_ip else ""),
            "Rewrite each URL to the https address that serves it (for article images, the media "
            f"library's public https URL), then search the CMS content for \"http://{top or ''}\" "
            "to catch every remaining reference.",
            detail=sorted(mixed_pages, key=lambda p: -p["resources"]),
        ))
    if orphans:
        issues.append(_issue(
            "url-orphan", "medium",
            f"{len(orphans)} sitemap URL(s) have no internal links from any other sitemap page",
            "A page reachable only through the sitemap gets no internal link equity and a weak "
            "importance signal — Google indexes orphans reluctantly and ranks them poorly.",
            "Link to each from a relevant hub, category or related page with a descriptive anchor.",
            urls=orphans,
        ))
    exact_dupes = [g for g in content_groups.values() if len(g) > 1]
    if exact_dupes:
        issues.append(_issue(
            "url-duplicate-content", "high",
            f"{sum(len(g) for g in exact_dupes)} sitemap URLs share identical main content "
            f"({len(exact_dupes)} group(s))",
            "Identical content on several listed URLs splits ranking signals and forces Google "
            "to pick one; the others are filtered out as duplicates.",
            "Consolidate to one URL (301 the rest), or make each genuinely distinct.",
            detail=[{"urls": g} for g in exact_dupes],
        ))
    if dup_listed:
        issues.append(_issue(
            "url-listed-twice", "low", f"{len(dup_listed)} URL(s) appear in more than one sitemap",
            "Harmless to indexing, but a sign two generators overlap.",
            "List each URL in exactly one sitemap.", detail=dup_listed,
        ))
    if param_urls:
        issues.append(_issue(
            "url-params", "medium", f"{len(param_urls)} sitemap URL(s) contain a query string",
            "Parameterised URLs are usually duplicates of a clean URL.",
            "List the clean canonical URL only.", urls=param_urls,
        ))
    if upper_urls:
        issues.append(_issue(
            "url-uppercase", "low", f"{len(upper_urls)} sitemap URL(s) contain uppercase letters",
            "URLs are case-sensitive; mixed case invites duplicate variants.",
            "Use lowercase URLs and 301 the old case variants.", urls=upper_urls,
        ))
    if underscore_urls:
        issues.append(_issue(
            "url-underscore", "low", f"{len(underscore_urls)} sitemap URL(s) use underscores",
            "Google treats hyphens as word separators and underscores as joiners.",
            "Use hyphens in new URLs; do not change existing ranking URLs just for this.",
            urls=underscore_urls,
        ))
    if long_urls:
        issues.append(_issue(
            "url-long", "low", f"{len(long_urls)} sitemap URL(s) are over 115 characters",
            "Long URLs are truncated in results and usually signal deep, keyword-stuffed paths.",
            "Keep URLs short and descriptive on new pages.", urls=long_urls,
        ))

    # ================================================= probes (host, slash, unlisted)
    bad_hosts = [h for h in probes.get("host_checks") or [] if not h["ok"]]
    if bad_hosts:
        insecure = [h for h in bad_hosts if h["url"].startswith("http://") and h["status"] == 200]
        canonical_back = all(h.get("canonical", "").startswith("https://") for h in insecure) if insecure else False
        issues.append(_issue(
            "host-not-canonicalised", "high",
            ("The site is served over plain http without redirecting to https"
             if insecure else "Non-canonical host variants do not redirect to the canonical host"),
            ("Every page answers on http:// with a full 200 response instead of a 301 to https — the "
             "whole site exists twice, and visitors who type or follow an http link get the insecure "
             "copy. " + ("The pages do declare an https canonical, which limits duplicate indexing, "
                         "but a canonical is a hint Google may ignore, and it does nothing for the "
                         "visitor or for crawl budget." if canonical_back else
                         "The http pages do not even point their canonical back to https."))
            if insecure else
            "If a host or scheme variant serves the site instead of redirecting, the site exists twice.",
            "301 every http:// request to the same path on https://, at the server or CDN, for every "
            "URL — not only the home page. Then send an HSTS header so browsers stop asking.",
            detail=[{"variant": h["url"], "status": h["status"], "ended_at": h["final_url"],
                     "canonical": h.get("canonical") or "—"} for h in bad_hosts],
        ))
    if probes.get("host_checks") is not None and "hsts" in probes and not probes.get("hsts"):
        issues.append(_issue(
            "no-hsts", "medium", "No HSTS header",
            "Strict-Transport-Security tells browsers to use https for this host on every future "
            "visit, without ever trying http first. Without it, each first visit and each typed "
            "address can start on the insecure copy.",
            "Send 'Strict-Transport-Security: max-age=31536000; includeSubDomains' on https responses "
            "(only once every page genuinely works on https).",
        ))
    dupe_slash = [c for c in probes.get("slash_checks") or []
                  if c["status"] == 200 and not c["redirects_to_listed"] and not c["canonical_to_listed"]]
    if dupe_slash:
        sections = sorted({c["section"] for c in dupe_slash})
        issues.append(_issue(
            "slash-duplicates", "high",
            f"Both slash variants of a URL serve 200 without consolidating ({len(sections)} section(s))",
            "When /page and /page/ both return the page with no redirect and no canonical back, "
            "every URL in that section has a duplicate. Sampled per section, so this is a "
            "site-wide behaviour, not a one-off.",
            "301 the non-canonical slash variant to the listed one, site-wide, at the server.",
            detail=[{"section": c["section"], "listed": short(c["listed"]), "variant": short(c["variant"]),
                     "variant_status": c["status"]} for c in dupe_slash],
        ))
    soft_slash = [c for c in probes.get("slash_checks") or []
                  if c["status"] == 200 and not c["redirects_to_listed"] and c["canonical_to_listed"]]
    if soft_slash:
        issues.append(_issue(
            "slash-canonical-only", "low",
            "The other slash variant serves 200 but canonicalises back",
            "Handled by the canonical, so not a duplicate-content problem — but a 301 is the "
            "stronger, cheaper signal and saves a crawl per variant.",
            "Prefer a 301 at the server.",
            detail=[{"section": c["section"], "variant": short(c["variant"])} for c in soft_slash[:12]],
        ))
    live_unlisted = [u for u in probes.get("unlisted") or []
                     if u["status"] == 200 and not u["redirected"] and not u["noindex"]
                     and u["canonical_kind"] != "other"]
    if live_unlisted:
        issues.append(_issue(
            "unlisted-live-pages", "medium",
            f"{len(live_unlisted)} live, indexable page(s) are linked internally but in no sitemap",
            "These are real pages the site links to, returning 200 with no noindex, that the "
            "sitemap never declares. Google finds them only by crawling links, later and with "
            "less priority.",
            "Add every indexable page to the appropriate sitemap.",
            detail=[{"url": short(u["url"]), "inlinks": u["inlinks"], "title": u["title"][:80],
                     "linked_from": short(u["linked_from"])} for u in live_unlisted],
        ))

    issues.sort(key=lambda i: (_SEV[i["severity"]], -i["count"]))
    affected = {row["url"] for row in url_rows if row["problems"]}
    score = _score(issues, total)

    doc = {
        "at": date.today().isoformat(),
        "domain": domain,
        "score": score,
        "url_count": total,
        "sitemap_count": len(sitemaps),
        "healthy_urls": total - len(affected),
        "affected_urls": len(affected),
        "sitemaps": [
            {**{k: v for k, v in s.items() if k not in ("url", "final_url", "parents")},
             "url": short(s["url"]), "parents": [short(p) for p in s["parents"]]}
            for s in sorted(sitemaps, key=lambda s: (s["depth"], s["url"]))
        ],
        "robots": {"status": robots.get("status"), "declared": [short(u) for u in robots.get("declared") or []]},
        "issues": issues,
        "probes": {
            "host_checks": probes.get("host_checks") or [],
            "slash_sections": sorted({c["section"] for c in probes.get("slash_checks") or []}),
            "unlisted_checked": len(probes.get("unlisted") or []),
        },
        "counts": {
            "by_type": dict(Counter(r["type"] for r in url_rows)),
            "by_status": {str(k): v for k, v in Counter(r["status"] for r in url_rows).items()},
            "with_lastmod": sum(1 for r in url_rows if r["lastmod"]),
        },
        # Kept for the dashboard tile, which predates this module.
        "present": any(s["status"] == 200 and s["kind"] in ("urlset", "index") for s in sitemaps),
        "is_index": any(s["kind"] == "index" for s in sitemaps),
        "child_sitemaps": sum(1 for s in sitemaps if s["parents"]),
        "lastmod_coverage_pct": round(100 * sum(1 for r in url_rows if r["lastmod"]) / total) if total else 0,
        "duplicate_count": len(dup_listed),
        "declared_in_robots": robots.get("declared") or [],
    }
    state.save(_DOC.format(brand["id"]), doc)
    jobs.save_list(_URLS.format(brand["id"]), url_rows)
    return doc


def _score(issues: list[dict], total: int) -> int:
    """0-100. File-level faults cost a flat amount; URL-level faults cost in
    proportion to the share of URLs they hit, so one 404 in 1,000 URLs is not
    scored like a site where half the sitemap is dead."""
    flat = {"high": 12, "medium": 5, "low": 2, "info": 0}
    per_url = {"high": 40, "medium": 18, "low": 5, "info": 0}
    penalty = 0.0
    for i in issues:
        if i["code"].startswith("url-") and total:
            penalty += per_url[i["severity"]] * min(1.0, i["count"] / total) + flat[i["severity"]] / 2
        else:
            penalty += flat[i["severity"]]
    return max(0, round(100 - penalty))


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))


def latest_urls(brand_id: str) -> list[dict]:
    return jobs.load_list(_URLS.format(brand_id))[0]
