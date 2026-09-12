"""Landing-page audit — every commercial page, every check, every piece of evidence.

Landing pages are where search traffic converts, so they are audited first and
hardest. "Landing page" here means every indexable page that is not a blog
post, blog archive or utility page: the home page, service pages, location
pages, centre pages, proof pages and resource hubs.

Each page gets ~45 checks in ten groups — indexability, title, meta
description, headings, content, images, links, structured data, social,
technical, URL and (when the speed job has run) real-browser performance.
Every failed check carries three things: the evidence (the actual value found,
never just "fail"), why it matters in search terms, and the exact fix.

Two views come out of it, because a senior audit needs both:

* **Per page** — the full list of what is wrong with one URL, scored.
* **Per check, across the site** — the issue matrix. When a check fails on
  most landing pages it is a TEMPLATE problem: it is fixed once, in the
  template, not 300 times in content. Separating template faults from
  page-level faults is the single most useful thing an audit of a templated
  site can do, and it is flagged explicitly.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from urllib.parse import urlparse

from . import jobs, keyphrase as kp, state
from .crawl import SHARED_ON_PAGES, downgrades, norm_url

_DOC = "landing-{}"
_PAGES = "landingpages-{}"

#: A check failing on at least this share of landing pages (and on at least
#: TEMPLATE_MIN of them) is reported as a template-level fault.
TEMPLATE_SHARE = 0.8
TEMPLATE_MIN = 8

#: Near-duplicate thresholds on exact 5-word-shingle Jaccard of main content.
DUP_HIGH = 0.85
DUP_MEDIUM = 0.70

LANDING_TYPES = ("home", "landing")

_WEIGHT = {"high": 10, "medium": 4, "low": 1, "info": 0}


class _Checks:
    """Collects findings for one page. ``fail`` records evidence; checks that
    pass are counted so a page's score reflects how much was examined."""

    def __init__(self) -> None:
        self.findings: list[dict] = []
        self.passed = 0

    def ok(self) -> None:
        self.passed += 1

    def fail(self, check: str, group: str, severity: str, title: str,
             evidence: str, why: str, fix: str) -> None:
        self.findings.append({
            "check": check, "group": group, "severity": severity, "title": title,
            "evidence": evidence, "why": why, "fix": fix,
        })


def _schema_wanted(subtype: str, record: dict) -> list[tuple[str, tuple[str, ...], str]]:
    """(label, acceptable @types, why) the page type calls for."""
    wants = [("BreadcrumbList", ("BreadcrumbList",),
              "breadcrumb trails replace the raw URL in results and state the page's place in the site")]
    if subtype == "home":
        wants.append(("Organization", ("Organization", "EducationalOrganization", "LocalBusiness", "WebSite"),
                      "the home page is where Google looks for the entity behind the site"))
    if subtype in ("location", "centre"):
        wants.append(("LocalBusiness / EducationalOrganization",
                      ("LocalBusiness", "EducationalOrganization", "School", "CollegeOrUniversity"),
                      "a location page without a local entity (name, address, phone, geo) gives local "
                      "search nothing to attach the page to"))
    if subtype == "service":
        wants.append(("Course / Service", ("Course", "Service", "Product", "EducationalOccupationalProgram"),
                      "a service page should describe the offering as an entity, not only as prose"))
    return wants


def audit_page(r: dict, *, brand_name: str, text: str, dupes: dict[str, list[tuple[str, float]]],
               title_groups: dict[str, list[str]], meta_groups: dict[str, list[str]],
               h1_groups: dict[str, list[str]], speed: dict | None, gsc_top: str | None,
               robots_blocked: bool) -> dict:
    c = _Checks()
    url = r["url"]
    subtype = "home" if r.get("type") == "home" else r.get("subtype") or "page"
    kw = kp.target_keyword(r, brand_name, gsc_top)
    k_tokens = kw["tokens"]
    k_phrase = kw["phrase"]

    # ------------------------------------------------------------ indexability
    if r.get("status") != 200:
        c.fail("status", "Indexability", "high", f"Returns {r.get('status') or 'no response'}",
               f"HTTP {r.get('status') or r.get('error')}",
               "A landing page that does not return 200 cannot rank at all.",
               "Restore the page or 301 it to its replacement, and remove it from the sitemap.")
        return _result(r, c, kw, subtype, speed)
    c.ok()
    if r.get("chain") and downgrades(r["chain"], r.get("final_url", "")):
        c.fail("redirect-downgrade", "Indexability", "high", "Redirect chain passes through plain http",
               " → ".join([h["url"] for h in r["chain"]] + [r["final_url"]]),
               "An https URL that bounces through an http hop is sent unencrypted mid-chain and "
               "costs an extra crawl.",
               "Issue one redirect straight to the final https URL.")
    if r.get("chain"):
        c.fail("redirect", "Indexability", "medium", "Reached through a redirect",
               " → ".join(f"{h['url']} ({h['status']})" for h in r["chain"]) + f" → {r['final_url']}",
               "Internal links and the sitemap should point at the final URL; each hop costs crawl and "
               "leaks a little signal.",
               "Update every internal link and the sitemap entry to the final URL.")
    else:
        c.ok()
    if r.get("noindex"):
        c.fail("noindex", "Indexability", "high", "Page is set to noindex",
               f"robots directives: {r.get('meta_robots') or r.get('x_robots_tag')}",
               "A noindexed landing page is invisible in search, whatever else is right with it.",
               "Remove noindex unless the page is deliberately excluded.")
    else:
        c.ok()
    if robots_blocked:
        c.fail("robots-blocked", "Indexability", "high", "Blocked by robots.txt",
               "Googlebot is disallowed from fetching this URL",
               "Google cannot read a blocked page, so it cannot rank it on its content.",
               "Remove the matching Disallow rule.")
    else:
        c.ok()
    if r.get("canonical_kind") == "missing":
        c.fail("canonical-missing", "Indexability", "medium", "No canonical tag",
               "no <link rel=canonical>",
               "Without a self-referencing canonical, parameter and tracking variants of this URL "
               "compete with it as duplicates.",
               "Add <link rel=\"canonical\" href=\"<this exact URL>\">.")
    elif r.get("canonical_kind") == "other":
        c.fail("canonical-other", "Indexability", "high", "Canonical points to a different URL",
               f"canonical = {r.get('canonical')}",
               "This page tells Google to index a different URL instead of itself.",
               "Point the canonical at this URL, unless this page really is a duplicate.")
    else:
        c.ok()

    # ------------------------------------------------------------------ title
    title = r.get("title") or ""
    tlen = r.get("title_len", 0)
    if not title:
        c.fail("title-missing", "Title", "high", "No title tag", "<title> is empty or absent",
               "The title is the headline in search results and the strongest on-page ranking signal.",
               f"Write a unique title leading with \"{k_phrase}\".")
    else:
        c.ok()
        if tlen > 60:
            c.fail("title-long", "Title", "medium", f"Title is {tlen} characters",
                   f"\"{title}\"",
                   "Google truncates titles around 580px (about 55–60 characters); the cut-off words, "
                   "often the location or the offer, never show.",
                   "Keep it under 60 characters with the keyword first.")
        elif tlen < 30:
            c.fail("title-short", "Title", "low", f"Title is only {tlen} characters",
                   f"\"{title}\"",
                   "A very short title wastes the most valuable text on the results page.",
                   f"Expand to 50–60 characters: the keyword, a differentiator, the brand.")
        else:
            c.ok()
        present, missing = kp.coverage(k_tokens, kp.strip_brand(title, brand_name))
        if k_tokens and missing:
            c.fail("title-keyword", "Title", "high" if len(missing) == len(k_tokens) else "medium",
                   f"Title does not contain the target keyword \"{k_phrase}\"",
                   f"title \"{title}\" is missing: {', '.join(missing)}",
                   "The title is the strongest single on-page relevance signal for the target query.",
                   f"Put \"{k_phrase}\" in the title, as close to the start as reads naturally.")
        elif k_tokens:
            c.ok()
            first_half = kp.tokens(title[: max(1, len(title) // 2)], keep_years=True)
            if k_tokens and k_tokens[0] not in first_half:
                c.fail("title-keyword-late", "Title", "low", "Keyword sits in the back half of the title",
                       f"\"{title}\"",
                       "Words at the start of a title carry the most weight and survive truncation.",
                       "Lead with the keyword.")
            else:
                c.ok()
        group = title_groups.get(title.strip().lower(), [])
        if len(group) > 1:
            others = [u for u in group if u != url]
            c.fail("title-duplicate", "Title", "high", f"Same title as {len(others)} other landing page(s)",
                   "shared with: " + ", ".join(others[:8]) + (" …" if len(others) > 8 else ""),
                   "Duplicate titles tell Google these pages are interchangeable, so it picks one and "
                   "filters the rest.",
                   "Give every page a unique title that names what makes it different.")
        else:
            c.ok()

    # ------------------------------------------------------- meta description
    desc = r.get("meta_description") or ""
    dlen = r.get("meta_desc_len", 0)
    if not desc:
        c.fail("meta-missing", "Meta description", "medium", "No meta description",
               "<meta name=description> absent",
               "Google then writes its own snippet from page text — usually a worse sales pitch.",
               f"Write a 140–155 character description that includes \"{k_phrase}\" and a reason to click.")
    else:
        c.ok()
        if dlen > 160:
            c.fail("meta-long", "Meta description", "low", f"Description is {dlen} characters",
                   f"\"{desc[:200]}\"", "Cut off after ~155 characters on desktop, less on mobile.",
                   "Trim to 140–155 characters.")
        elif dlen < 70:
            c.fail("meta-short", "Meta description", "low", f"Description is only {dlen} characters",
                   f"\"{desc}\"", "A short description leaves Google room to replace it.",
                   "Expand to 140–155 characters.")
        else:
            c.ok()
        _, missing = kp.coverage(k_tokens, desc)
        if k_tokens and missing:
            c.fail("meta-keyword", "Meta description", "low", "Description does not contain the keyword",
                   f"missing: {', '.join(missing)}",
                   "Google bolds query words in the snippet; a description without them looks less relevant.",
                   f"Work \"{k_phrase}\" into the description naturally.")
        else:
            c.ok()
        group = meta_groups.get(desc.strip().lower(), [])
        if len(group) > 1:
            others = [u for u in group if u != url]
            c.fail("meta-duplicate", "Meta description", "medium",
                   f"Same description as {len(others)} other landing page(s)",
                   "shared with: " + ", ".join(others[:8]) + (" …" if len(others) > 8 else ""),
                   "Identical snippets make distinct pages look like duplicates in results.",
                   "Write a unique description per page.")
        else:
            c.ok()

    # ---------------------------------------------------------------- headings
    h1s = r.get("h1") or []
    if not h1s:
        c.fail("h1-missing", "Headings", "high", "No H1", "no <h1> on the page",
               "The H1 is the page's own statement of its topic, read by users and by Google.",
               f"Add one H1 containing \"{k_phrase}\".")
    else:
        c.ok()
        if len(h1s) > 1:
            c.fail("h1-multiple", "Headings", "medium", f"{len(h1s)} H1 headings",
                   " | ".join(f"\"{h}\"" for h in h1s[:5]),
                   "Several H1s blur which one states the page's topic.",
                   "Keep exactly one H1; demote the rest to H2.")
        else:
            c.ok()
        _, missing = kp.coverage(k_tokens, " ".join(h1s))
        if k_tokens and missing:
            c.fail("h1-keyword", "Headings", "medium", "H1 does not contain the target keyword",
                   f"H1 \"{h1s[0]}\" is missing: {', '.join(missing)}",
                   "The H1 should confirm the query the searcher typed.",
                   f"Include \"{k_phrase}\" in the H1.")
        else:
            c.ok()
        group = h1_groups.get(h1s[0].strip().lower(), [])
        if len(group) > 1:
            others = [u for u in group if u != url]
            c.fail("h1-duplicate", "Headings", "medium", f"Same H1 as {len(others)} other landing page(s)",
                   "shared with: " + ", ".join(others[:8]),
                   "Identical H1s are one of the signals that marks pages as duplicates.",
                   "Make each H1 name what is specific to this page.")
        else:
            c.ok()
    if r.get("heading_skips"):
        c.fail("heading-skips", "Headings", "low", f"Heading levels skip {r['heading_skips']} time(s)",
               "outline: " + " > ".join(f"H{h['level']}" for h in (r.get("headings") or [])[:14]),
               "Skipped levels (H2 straight to H4) break the document outline screen readers and "
               "parsers rely on.",
               "Nest headings in order: H1, then H2, then H3.")
    else:
        c.ok()
    if r.get("h2_count", 0) == 0 and r.get("word_count", 0) >= 300:
        c.fail("h2-none", "Headings", "medium", "No H2 subheadings on a page with real content",
               f"{r.get('word_count')} words, 0 H2",
               "Subheadings are how both readers and Google map the sections a page covers.",
               "Break the content into H2 sections that match what searchers ask.")
    else:
        c.ok()

    # ----------------------------------------------------------------- content
    wc = r.get("word_count", 0)
    body = r.get("word_count_body", 0) or 1
    # Judged on the words that are this page's own (crawl._mark_unique): 277
    # words of centre-page template repeated on 88 pages is not 277 words of
    # content as far as ranking this page goes.
    uniq = r.get("unique_word_count")
    own = wc if uniq is None else min(uniq, wc)
    shared = wc - own
    what = (f"{wc} words of main content, only {own} unique to this page — the other {shared} "
            f"also appear on {SHARED_ON_PAGES - 1}+ other landing pages" if shared
            else f"{wc} main-content words") + f" ({body} including navigation and footer)"
    title = f"Only {own} words unique to this page" if shared else f"Only {wc} words of main content"
    if own < 150:
        c.fail("thin-content", "Content", "high", title, what,
               "Too little unique content for Google to judge relevance; thin landing pages rarely "
               "rank for anything competitive, and template copy shared with sibling pages does not "
               "count in their favour.",
               "Add at least 400–600 words specific to this page — what a searcher for it actually "
               "needs (for a centre: faculty, batches, results, address, fees, reviews).")
    elif own < 300:
        c.fail("thin-content", "Content", "medium", title, what,
               "Thin for a commercial landing page competing on a head term.",
               "Expand with genuinely useful, page-specific content.")
    else:
        c.ok()
    if body > 0 and wc / body < 0.25 and body >= 400:
        c.fail("boilerplate-heavy", "Content", "medium",
               f"Only {round(100 * wc / body)}% of the page's text is its own content",
               f"{wc} main-content words in {body} total — the rest is template",
               "When most of a page is shared template, the unique part is too small a signal and "
               "sibling pages look alike.",
               "Grow the page-specific content, or trim the repeated template blocks.")
    else:
        c.ok()
    if k_tokens:
        _, missing = kp.coverage(k_tokens, r.get("first_100") or "")
        if missing:
            c.fail("keyword-intro", "Content", "medium", "Keyword not in the first 100 words",
                   f"first 100 words are missing: {', '.join(missing)}",
                   "Early placement confirms relevance to both the reader and the ranking system.",
                   f"Use \"{k_phrase}\" in the opening paragraph.")
        else:
            c.ok()
    for twin, sim in dupes.get(url, [])[:1]:
        sev = "high" if sim >= DUP_HIGH else "medium"
        c.fail("near-duplicate", "Content", sev, f"{round(sim * 100)}% identical to another landing page",
               f"main content {round(sim * 100)}% identical to {twin}"
               + (f" (and {len(dupes[url]) - 1} more)" if len(dupes[url]) > 1 else ""),
               "Templated pages with only a name swapped are treated as duplicates: Google indexes "
               "one and filters the rest, however many locations the site has.",
               "Rewrite each page around what is genuinely local or specific — faculty, results, "
               "address, batch timings, reviews, directions — not a name swap.")
        break
    else:
        c.ok()

    # ------------------------------------------------------------------ images
    imgs = r.get("images", 0)
    if r.get("img_missing_alt", 0):
        c.fail("img-alt", "Images", "medium", f"{r['img_missing_alt']} of {imgs} images have no alt attribute",
               "e.g. " + ", ".join((r.get("img_missing_alt_sample") or [])[:4]),
               "Alt text is how Google understands images and how screen readers describe them; "
               "images without it earn no image-search traffic.",
               "Add descriptive alt text to content images; use alt=\"\" only for decoration.")
    else:
        c.ok()
    if imgs and r.get("img_no_dims", 0):
        share = r["img_no_dims"] / imgs
        c.fail("img-dimensions", "Images", "medium" if share > 0.3 else "low",
               f"{r['img_no_dims']} of {imgs} images have no width/height",
               f"{round(share * 100)}% of images without explicit dimensions",
               "The browser cannot reserve space for an image of unknown size, so content jumps when "
               "it loads — this is the main cause of a poor Cumulative Layout Shift score.",
               "Set width and height attributes (or CSS aspect-ratio) on every image.")
    else:
        c.ok()
    if imgs > 20 and r.get("img_lazy", 0) == 0:
        c.fail("img-lazy", "Images", "medium", f"{imgs} images and none lazy-loaded",
               "no loading=\"lazy\" on any image",
               "Every image downloads up front, competing with the content the visitor came for.",
               "Add loading=\"lazy\" to below-the-fold images (never to the hero).")
    else:
        c.ok()
    if imgs > 120:
        c.fail("img-count", "Images", "low", f"{imgs} images on one page",
               f"{imgs} <img> elements",
               "A very high image count inflates page weight and request count.",
               "Audit which images earn their place; sprite or remove repeated decorative ones.")
    else:
        c.ok()

    # ------------------------------------------------------------------- links
    inl = r.get("inlinks", 0)
    if inl == 0 and subtype != "home":
        c.fail("orphan", "Links", "high", "No internal links point to this page",
               "0 inbound links from any other page in the sitemap",
               "An orphan landing page gets no internal link equity and a weak importance signal.",
               "Link to it from its hub (city, service or centre page) with a keyword anchor.")
    elif inl < 3 and subtype != "home":
        c.fail("weak-inlinks", "Links", "low", f"Only {inl} internal link(s) point here",
               f"{inl} inbound internal links",
               "Few internal links mean little internal authority for a page meant to rank.",
               "Add contextual links from related landing pages and blog posts.")
    else:
        c.ok()
    if r.get("nofollow_internal"):
        c.fail("nofollow-internal", "Links", "medium", f"{r['nofollow_internal']} internal links are nofollow",
               f"{r['nofollow_internal']} rel=nofollow links to this site's own pages",
               "Nofollow on internal links throws away the site's own link equity.",
               "Remove rel=nofollow from internal links.")
    else:
        c.ok()
    if r.get("generic_anchors", 0) >= 3:
        c.fail("generic-anchors", "Links", "low", f"{r['generic_anchors']} generic anchors",
               "\"click here\" / \"read more\" style link text",
               "Anchor text tells Google what the target page is about; generic anchors say nothing.",
               "Use descriptive anchors that name the destination.")
    else:
        c.ok()
    if r.get("empty_anchors", 0) >= 3:
        c.fail("empty-anchors", "Links", "low", f"{r['empty_anchors']} links with no text",
               "links with no text, aria-label or image alt",
               "Empty links carry no relevance signal and fail accessibility checks.",
               "Give every link text or an aria-label.")
    else:
        c.ok()
    if r.get("internal_link_count", 0) > 300:
        c.fail("link-overload", "Links", "low", f"{r['internal_link_count']} internal links on one page",
               f"{r['internal_link_count']} internal links",
               "Very high link counts dilute the equity each link passes.",
               "Trim mega-menus and footer link farms.")
    else:
        c.ok()

    # --------------------------------------------------------- structured data
    types = set(r.get("schema_types") or [])
    if r.get("schema_errors"):
        c.fail("schema-broken", "Structured data", "high",
               f"{r['schema_errors']} JSON-LD block(s) fail to parse",
               f"{r['schema_errors']} <script type=application/ld+json> with invalid JSON",
               "Google discards an unparseable JSON-LD block entirely — whatever it was meant to say "
               "is lost.",
               "Validate the JSON (Rich Results Test) and fix the generator; a stray comma or unescaped "
               "quote breaks the whole block.")
    else:
        c.ok()
    if not types:
        c.fail("schema-none", "Structured data", "medium", "No structured data at all",
               "no JSON-LD, microdata types found",
               "Structured data is how a page states what it is as an entity; without it Google infers.",
               "Add JSON-LD appropriate to the page type (see the next checks).")
    else:
        c.ok()
    for label, acceptable, why in _schema_wanted(subtype, r):
        if not (types & set(acceptable)):
            sev = "medium" if label.startswith(("LocalBusiness", "Organization")) else "low"
            c.fail(f"schema-{label.split()[0].lower()}", "Structured data", sev,
                   f"No {label} markup", f"present: {', '.join(sorted(types)) or 'none'}",
                   why[0].upper() + why[1:] + ".",
                   f"Add {label} JSON-LD.")
        else:
            c.ok()
    questions = [h["text"] for h in (r.get("headings") or []) if h["text"].strip().endswith("?")]
    if len(questions) >= 3 and "FAQPage" not in types:
        c.fail("schema-faq", "Structured data", "info",
               f"{len(questions)} question headings without FAQPage markup",
               "; ".join(questions[:3]),
               "Google limited FAQ rich results in 2023 to authoritative government and health "
               "sites, so this will not earn a rich result — but the markup still states the Q&A "
               "structure explicitly.",
               "Optional: mark up the Q&A with FAQPage.")

    # ------------------------------------------------------------------ social
    og = r.get("og") or {}
    missing_og = [k for k in ("title", "description", "image") if not og.get(k)]
    if missing_og:
        c.fail("og-missing", "Social", "low", f"Open Graph missing: {', '.join(missing_og)}",
               f"og:{', og:'.join(missing_og)} absent",
               "Shares on WhatsApp, LinkedIn and Facebook fall back to guessed text and no image — "
               "on an Indian education site, WhatsApp shares are a real channel.",
               "Add og:title, og:description and a 1200×630 og:image.")
    else:
        c.ok()

    # --------------------------------------------------------------- technical
    if not r.get("viewport"):
        c.fail("viewport", "Technical", "high", "No viewport meta tag", "<meta name=viewport> absent",
               "Google indexes mobile-first; without a viewport the page renders as a zoomed-out desktop page.",
               "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.")
    else:
        c.ok()
    if not r.get("lang"):
        c.fail("lang", "Technical", "low", "No lang attribute on <html>", "<html> has no lang",
               "Language declaration helps search and assistive tech serve the right audience.",
               "Add <html lang=\"en-IN\"> (or the page's language).")
    else:
        c.ok()
    if r.get("mixed_content"):
        c.fail("mixed-content", "Technical", "medium", f"{r['mixed_content']} http:// resource(s) on an https page",
               f"{r['mixed_content']} insecure subresources",
               "Browsers block or warn on mixed content, breaking images and scripts and flagging the page insecure.",
               "Serve every resource over https.")
    else:
        c.ok()
    if r.get("head_js_blocking"):
        c.fail("render-blocking-js", "Technical", "medium",
               f"{r['head_js_blocking']} render-blocking script(s) in <head>",
               f"{r['head_js_blocking']} <script src> without async or defer",
               "A synchronous script in <head> stops the browser rendering anything until it downloads "
               "and runs — it directly delays First and Largest Contentful Paint.",
               "Add defer (or async for independent scripts), or move them to the end of <body>.")
    else:
        c.ok()
    if r.get("head_css", 0) > 3:
        c.fail("render-blocking-css", "Technical", "low", f"{r['head_css']} stylesheets in <head>",
               f"{r['head_css']} blocking <link rel=stylesheet>",
               "Each stylesheet blocks rendering until it loads.",
               "Combine them, inline the critical CSS and load the rest asynchronously.")
    else:
        c.ok()
    if r.get("html_bytes", 0) > 250_000:
        c.fail("html-weight", "Technical", "low", f"HTML document is {r['html_bytes'] // 1024}KB",
               f"{r['html_bytes'] // 1024}KB of HTML before any assets",
               "A heavy HTML document delays first render on slow mobile connections.",
               "Remove inlined data, duplicated markup and unused template blocks.")
    else:
        c.ok()
    if r.get("ttfb_ms", 0) > 800:
        c.fail("ttfb", "Technical", "medium", f"Server responded in {round(r['ttfb_ms'])}ms",
               f"time to first byte {round(r['ttfb_ms'])}ms from the crawler",
               "Google's guidance is under 800ms; nothing can render before the first byte arrives.",
               "Cache the HTML at the edge (CDN) and profile the slowest server-side work.")
    else:
        c.ok()

    # --------------------------------------------------------------------- URL
    path = urlparse(r.get("final_url") or url).path
    if len(path) > 75:
        c.fail("url-long", "URL", "low", f"URL path is {len(path)} characters", path,
               "Long URLs are truncated in results and read as keyword-stuffed.", "Keep paths short.")
    else:
        c.ok()
    if path != path.lower():
        c.fail("url-case", "URL", "low", "URL contains uppercase", path,
               "URLs are case-sensitive; mixed case invites duplicate variants.",
               "Use lowercase and 301 old variants.")
    else:
        c.ok()
    if k_tokens and kw["source"] != "url":
        _, missing = kp.coverage(k_tokens, path)
        if len(missing) == len(k_tokens):
            c.fail("url-keyword", "URL", "low", "URL does not contain the target keyword", path,
                   "A descriptive URL is a small relevance signal and a larger click-through one.",
                   "Do not change a ranking URL for this alone; apply it to new pages.")
        else:
            c.ok()

    # ------------------------------------------------------------- performance
    if speed and speed.get("lcp_ms") is not None:
        lcp = speed["lcp_ms"]
        overlay = speed.get("lcp_overlay")
        # The page's own content is rated on its own. A popup that paints last
        # is a separate finding with a separate fix: optimising the hero image
        # does nothing for an LCP that belongs to a modal opening at 16s.
        content = speed.get("content_lcp_ms") if overlay else lcp
        if overlay:
            before = (f"; the page's own content ({speed.get('content_lcp_element')}) was visible at "
                      f"{content / 1000:.1f}s") if content else ""
            c.fail("lcp-popup", "Performance", "high",
                   f"A popup is the LCP: {lcp / 1000:.1f}s" +
                   (f", though the content was visible at {content / 1000:.1f}s" if content else ""),
                   f"LCP element {speed.get('lcp_element')} inside {overlay}, painted at "
                   f"{lcp / 1000:.1f}s on throttled mobile{before}",
                   "Chrome records LCP as the largest thing painted before the visitor interacts, so a "
                   "popup that opens after the content becomes the number Google measures. A mobile "
                   "popup that covers the page just after arrival from search is also what Google "
                   "calls an intrusive interstitial.",
                   "Do not open it on arrival: trigger it on exit intent, after a scroll or a second "
                   "page view, or show it as a small bar instead of a modal."
                   + (f" Without it this page's LCP is {content / 1000:.1f}s." if content else ""))
        else:
            c.ok()
        if content is None:
            pass
        elif content > 4000:
            c.fail("lcp", "Performance", "high", f"Main content visible after {content / 1000:.1f}s (LCP)",
                   f"LCP {content / 1000:.1f}s on throttled mobile" +
                   (f"; LCP element: {speed.get('content_lcp_element') or speed.get('lcp_element')}"
                    if (speed.get("content_lcp_element") or speed.get("lcp_element")) else ""),
                   "Google rates LCP over 4s as poor; it is a ranking signal and most visitors on "
                   "mobile leave before a 4s page finishes.",
                   "Optimise the LCP element named here: preload it, serve it at the displayed size "
                   "in WebP/AVIF, never lazy-load it, and remove render-blocking resources ahead of it.")
        elif content > 2500:
            c.fail("lcp", "Performance", "medium", f"Main content visible after {content / 1000:.1f}s (LCP)",
                   f"LCP {content / 1000:.1f}s on throttled mobile",
                   "Needs improvement by Google's threshold (good is under 2.5s).",
                   "Preload and compress the LCP element.")
        else:
            c.ok()
        cls = speed.get("cls")
        moved = "; ".join(f"{', '.join(s['nodes'][:2]) or 'unattributed'} moved {s['v']:.3f} at "
                          f"{s['t'] / 1000:.1f}s" for s in (speed.get("shifts") or [])[:3])
        if cls is not None and cls > 0.25:
            c.fail("cls", "Performance", "high", f"Layout shift {cls:.2f} (CLS)",
                   f"CLS {cls:.3f}" + (f" — {moved}" if moved else ""),
                   "Over 0.25 is poor: content jumps under the reader's thumb.",
                   "Reserve the space of the elements named here before they render (a fixed height "
                   "or aspect-ratio on carousels and banners, width/height on every image).")
        elif cls is not None and cls > 0.1:
            c.fail("cls", "Performance", "medium", f"Layout shift {cls:.2f} (CLS)",
                   f"CLS {cls:.3f}" + (f" — {moved}" if moved else ""),
                   "Needs improvement (good is under 0.1).",
                   "Reserve space for the elements named here before they render.")
        elif cls is not None:
            c.ok()
        if speed.get("bytes", 0) > 3 * 1024 * 1024:
            c.fail("page-weight", "Performance", "medium",
                   f"Page downloads {speed['bytes'] / 1048576:.1f}MB", f"{speed['bytes'] / 1048576:.1f}MB, "
                   f"{speed.get('requests')} requests",
                   "Heavy pages are slow on Indian mobile networks and expensive on metered data.",
                   "Compress images, drop unused scripts and third-party tags.")
        else:
            c.ok()

    return _result(r, c, kw, subtype, speed)


def _result(r: dict, c: _Checks, kw: dict, subtype: str, speed: dict | None) -> dict:
    penalty = sum(_WEIGHT[f["severity"]] for f in c.findings)
    examined = c.passed + len(c.findings)
    score = max(0, round(100 - penalty * 100 / max(1, examined * 3)))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return {
        "url": r["url"],
        "subtype": subtype,
        "title": r.get("title", ""),
        "h1": (r.get("h1") or [""])[0],
        "keyword": kw["phrase"],
        "keyword_source": kw["source"],
        "status": r.get("status"),
        "words": r.get("word_count", 0),
        "unique_words": r.get("unique_word_count"),
        "inlinks": r.get("inlinks", 0),
        "score": score,
        "grade": grade,
        "checks_run": examined,
        "checks_failed": len(c.findings),
        "high": sum(1 for f in c.findings if f["severity"] == "high"),
        "medium": sum(1 for f in c.findings if f["severity"] == "medium"),
        "low": sum(1 for f in c.findings if f["severity"] == "low"),
        "findings": sorted(c.findings, key=lambda f: order[f["severity"]]),
        "lcp_ms": (speed or {}).get("lcp_ms"),
        "content_lcp_ms": (speed or {}).get("content_lcp_ms"),
        "lcp_overlay": (speed or {}).get("lcp_overlay"),
    }


def near_duplicates(pages: list[dict], texts: dict[str, str]) -> dict[str, list[tuple[str, float]]]:
    """For every landing page, the other landing pages whose main content is at
    least DUP_MEDIUM identical, most similar first. Exact Jaccard over shingles."""
    sh = {}
    for p in pages:
        t = texts.get(norm_url(p["url"]), "")
        if len(t.split()) >= 60:
            sh[p["url"]] = kp.shingles(t)
    urls = list(sh)
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for i, a in enumerate(urls):
        sa = sh[a]
        for b in urls[i + 1:]:
            sb = sh[b]
            # Cheap size bound first: Jaccard can never exceed min/max of the sizes.
            if min(len(sa), len(sb)) / max(len(sa), len(sb)) < DUP_MEDIUM:
                continue
            sim = kp.jaccard(sa, sb)
            if sim >= DUP_MEDIUM:
                out[a].append((b, sim))
                out[b].append((a, sim))
    for k in out:
        out[k].sort(key=lambda x: -x[1])
    return out


def build(brand: dict, records: list[dict], texts: dict[str, str], *,
          speed: dict[str, dict] | None = None, gsc_top: dict[str, str] | None = None,
          robots_parser=None, dupes: dict[str, list[tuple[str, float]]] | None = None) -> dict:
    """Audit every landing page; return the site summary and persist per-page detail."""
    brand_name = brand.get("name", "")
    speed = speed or {}
    gsc_top = gsc_top or {}
    pages = [r for r in records if r.get("type") in LANDING_TYPES]

    def groups(field: str) -> dict[str, list[str]]:
        g: dict[str, list[str]] = defaultdict(list)
        for r in pages:
            if r.get("status") != 200:
                continue
            v = r.get(field)
            if isinstance(v, list):
                v = v[0] if v else ""
            if v and v.strip():
                g[v.strip().lower()].append(r["url"])
        return g

    title_groups, meta_groups, h1_groups = groups("title"), groups("meta_description"), groups("h1")
    if dupes is None:
        dupes = near_duplicates([p for p in pages if p.get("status") == 200], texts)

    results = []
    for r in pages:
        blocked = False
        if robots_parser is not None:
            try:
                blocked = not robots_parser.can_fetch("Googlebot", r["url"])
            except Exception:  # noqa: BLE001
                blocked = False
        results.append(audit_page(
            r, brand_name=brand_name, text=texts.get(norm_url(r["url"]), ""), dupes=dupes,
            title_groups=title_groups, meta_groups=meta_groups, h1_groups=h1_groups,
            speed=speed.get(norm_url(r["url"])), gsc_top=gsc_top.get(norm_url(r["url"])),
            robots_blocked=blocked,
        ))

    # ---- the issue matrix: each check, across every landing page
    n = len(results) or 1
    matrix: dict[str, dict] = {}
    for res in results:
        for f in res["findings"]:
            m = matrix.setdefault(f["check"], {
                "check": f["check"], "group": f["group"], "severity": f["severity"],
                "title": f["title"], "why": f["why"], "fix": f["fix"], "pages": [],
            })
            # Keep the most severe variant's wording (e.g. thin content < 150 over < 300).
            if _WEIGHT[f["severity"]] > _WEIGHT[m["severity"]]:
                m.update(severity=f["severity"], title=f["title"], why=f["why"], fix=f["fix"])
            m["pages"].append({"url": res["url"], "evidence": f["evidence"]})
    for m in matrix.values():
        m["count"] = len(m["pages"])
        m["share"] = round(m["count"] / n, 3)
        m["template"] = m["count"] >= TEMPLATE_MIN and m["share"] >= TEMPLATE_SHARE
        # A generic title for the matrix row — per-page titles carry numbers.
        m["label"] = _MATRIX_LABEL.get(m["check"], m["title"])
    issues = sorted(matrix.values(),
                    key=lambda m: (-int(m["template"]), -_WEIGHT[m["severity"]], -m["count"]))

    by_sub: dict[str, list[dict]] = defaultdict(list)
    for res in results:
        by_sub[res["subtype"]].append(res)
    subtypes = [{
        "subtype": s,
        "pages": len(rs),
        "avg_score": round(sum(x["score"] for x in rs) / len(rs)),
        "high": sum(x["high"] for x in rs),
    } for s, rs in sorted(by_sub.items(), key=lambda kv: -len(kv[1]))]

    dup_pairs = sorted({
        tuple(sorted((a, b))) + (round(s, 3),)
        for a, lst in dupes.items() for b, s in lst
    }, key=lambda t: -t[2])

    results.sort(key=lambda x: (x["score"], -x["high"]))
    doc = {
        "at": date.today().isoformat(),
        "pages": len(results),
        "avg_score": round(sum(x["score"] for x in results) / n) if results else 0,
        "grades": {g: sum(1 for x in results if x["grade"] == g) for g in "ABCDF"},
        "high_total": sum(x["high"] for x in results),
        "template_issues": sum(1 for m in issues if m["template"]),
        "issues": [{k: v for k, v in m.items() if k != "pages"} | {"pages": m["pages"][:400]} for m in issues],
        "subtypes": subtypes,
        "near_duplicate_pairs": [{"a": a, "b": b, "similarity": s} for a, b, s in dup_pairs[:300]],
        "speed_included": bool(speed),
        "keyword_sources": dict(sorted(
            {src: sum(1 for x in results if x["keyword_source"] == src)
             for src in {x["keyword_source"] for x in results}}.items())),
    }
    state.save(_DOC.format(brand["id"]), doc)
    jobs.save_list(_PAGES.format(brand["id"]), results)
    return doc


_MATRIX_LABEL = {
    "thin-content": "Too little unique content", "img-alt": "Images without alt text",
    "img-dimensions": "Images without width/height", "img-lazy": "No lazy-loaded images",
    "img-count": "Very high image count", "title-long": "Title too long",
    "title-short": "Title too short", "title-keyword": "Title missing target keyword",
    "title-duplicate": "Duplicate title", "meta-long": "Meta description too long",
    "meta-short": "Meta description too short", "meta-missing": "No meta description",
    "meta-duplicate": "Duplicate meta description", "meta-keyword": "Description missing keyword",
    "h1-multiple": "Multiple H1s", "h1-keyword": "H1 missing target keyword",
    "h1-duplicate": "Duplicate H1", "h1-missing": "No H1", "heading-skips": "Heading levels skipped",
    "h2-none": "No H2 subheadings", "boilerplate-heavy": "Mostly template text",
    "keyword-intro": "Keyword not in first 100 words", "near-duplicate": "Near-duplicate of another page",
    "orphan": "No internal links in", "weak-inlinks": "Few internal links in",
    "generic-anchors": "Generic anchor text", "empty-anchors": "Links with no text",
    "link-overload": "Too many internal links", "schema-broken": "Broken JSON-LD",
    "schema-none": "No structured data", "og-missing": "Open Graph tags missing",
    "render-blocking-js": "Render-blocking scripts", "render-blocking-css": "Render-blocking stylesheets",
    "html-weight": "Heavy HTML", "ttfb": "Slow server response", "lcp": "Slow main-content paint (LCP)",
    "lcp-popup": "A popup is the LCP element",
    "cls": "Layout shift (CLS)", "page-weight": "Heavy page download", "url-long": "Long URL",
    "title-keyword-late": "Keyword late in title", "canonical-missing": "No canonical",
    "canonical-other": "Canonical points elsewhere", "noindex": "Noindex", "redirect": "Redirected",
}


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))


def latest_pages(brand_id: str) -> list[dict]:
    return jobs.load_list(_PAGES.format(brand_id))[0]
