"""Keyword cannibalization — pages on one site competing for the same search.

When two pages target the same query, Google has to choose between them. It
often alternates, ranks neither as well as one consolidated page would, or
ranks the wrong one (a blog post instead of the service page that converts).
Signals — links, relevance, click data — are split across both.

The naive check ("do two pages share keywords?") is useless on a real site: it
flags every location page against every other one. A rigorous check has to
separate three situations that look alike on the surface:

1. **Same target.** Two pages whose intent signatures are identical. Always a
   problem — one of them should not exist in its current form.
2. **Hub and spoke.** One page's target is strictly contained in another's —
   ``clat coaching jaipur`` inside ``clat coaching vaishali nagar jaipur``. This
   is normal site architecture, NOT cannibalization, *unless* the spoke is a
   near-copy of the hub. So its severity is decided by how similar the two
   pages' actual content is, measured exactly.
3. **Different targets that happen to share words.** ``clat coaching jaipur``
   and ``clat coaching delhi`` share two of three words and compete for nothing.
   The containment test below rejects these by construction: a geo token that
   differs is a different query.

Blog posts and landing pages are checked against each other too, because the
most expensive kind of cannibalization is an informational blog post outranking
the commercial page it should be feeding.

When Search Console is connected, the check stops inferring and reads the
evidence directly: the same query earning impressions on two of the site's own
URLs. That is cannibalization by definition, and it is reported separately as
*confirmed*.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from . import jobs, keyphrase as kp, state
from .crawl import norm_url

_DOC = "cannibal-{}"
_PAIRS = "cannibalpairs-{}"

#: Share of the smaller signature that must sit inside the larger for two pages
#: to be treated as targeting the same query. Below 1.0 only once a signature is
#: long enough that one differing word can be noise (7+ tokens).
CONTAINMENT = 0.85
MIN_SHARED = 2

#: Content similarity that turns an architectural hub/spoke pair into a problem.
SPOKE_HIGH = 0.80
SPOKE_MEDIUM = 0.50

#: A GSC query counts as split when the runner-up URL earns at least this share
#: of the query's impressions.
GSC_SPLIT_SHARE = 0.10

#: Shingles a page needs before its content can be compared at all (~40 words).
MIN_COMPARABLE = 35

INDEXABLE_TYPES = ("home", "landing", "blog_post", "blog_category")
COMMERCIAL = ("home", "landing")


def _indexable(r: dict) -> bool:
    return (r.get("status") == 200 and not r.get("noindex")
            and r.get("canonical_kind") != "other" and r.get("type") in INDEXABLE_TYPES)


def _strength(r: dict) -> tuple:
    """Which of two competing pages should be the one that survives.

    Commercial pages outrank blog posts for commercial intent, because they are
    the ones that convert; then more internal links (the site's own vote), then
    more content, then the shallower URL.
    """
    return (
        1 if r.get("type") in COMMERCIAL else 0,
        r.get("inlinks", 0),
        r.get("word_count", 0),
        -len((r.get("final_url") or r["url"]).rstrip("/").split("/")),
    )


def build(brand: dict, records: list[dict], texts: dict[str, str], *,
          gsc_rows: list | None = None, gsc_top: dict[str, str] | None = None) -> dict:
    brand_name = brand.get("name", "")
    gsc_top = gsc_top or {}
    pages = [r for r in records if _indexable(r)]

    sigs: list[frozenset[str]] = []
    phrase: list[str] = []
    for r in pages:
        kw = kp.target_keyword(r, brand_name, gsc_top.get(norm_url(r["url"])))
        sigs.append(frozenset(kw["tokens"]))
        phrase.append(kw["phrase"])

    # Candidates are found on the WORDS of each signature, ignoring numbers, so
    # that /papers/2025-pdf/ and /papers/2026-pdf/ still meet; whether they are a
    # problem is then decided on the full signature below.
    def base(sig: frozenset[str]) -> frozenset[str]:
        return frozenset(t for t in sig if not t.isdigit())

    bases = [base(s_) for s_ in sigs]
    posting: dict[str, set[int]] = defaultdict(set)
    for i, s_ in enumerate(bases):
        for t in s_:
            posting[t].add(i)

    shingle_cache: dict[int, frozenset[int]] = {}

    def sim(i: int, j: int) -> float | None:
        """Exact content overlap, or None when either page has too little text
        to compare. None is not 0: "0% the same" is a finding, "could not
        compare" is the absence of one, and printing both as 0% misleads."""
        for k in (i, j):
            if k not in shingle_cache:
                shingle_cache[k] = kp.shingles(texts.get(norm_url(pages[k]["url"]), ""))
        if len(shingle_cache[i]) < MIN_COMPARABLE or len(shingle_cache[j]) < MIN_COMPARABLE:
            return None
        return kp.jaccard(shingle_cache[i], shingle_cache[j])

    pairs: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for i, a in enumerate(sigs):
        a_base = bases[i]
        if len(a_base) < MIN_SHARED:
            continue
        ordered = sorted(a_base, key=lambda t: len(posting[t]))
        # Pages whose signature contains ALL of this one's tokens...
        cands = set.intersection(*(posting[t] for t in ordered))
        # ...or all but one, once a signature is long enough for that to be noise.
        if len(a_base) >= 7:
            for skip in ordered:
                rest = [posting[t] for t in ordered if t != skip]
                cands |= set.intersection(*rest) if rest else set()
        for j in cands:
            if j == i:
                continue
            b = sigs[j]
            key = (min(i, j), max(i, j))
            if key in seen:
                continue

            # A numbered series: identical words, different numbers — paper
            # 2025 and paper 2026, topper AIR 1 and AIR 2. Separate documents
            # by default. Only a problem when they are copies of each other,
            # which is what an annual page re-published instead of updated is.
            if a_base == bases[j] and a != b:
                seen.add(key)
                content = sim(i, j)
                if content is None or content < SPOKE_MEDIUM:
                    continue
                keep_i = _strength(pages[i]) >= _strength(pages[j])
                ph, ps = (pages[i], pages[j]) if keep_i else (pages[j], pages[i])
                pairs.append(_pair("numbered-series",
                                   "high" if content >= SPOKE_HIGH else "medium",
                                   a & b, ph, ps, phrase[i if keep_i else j],
                                   phrase[j if keep_i else i], content))
                continue

            shared = a & b
            if len(shared) < MIN_SHARED:
                continue
            small = a if len(a) <= len(b) else b
            if len(shared) / len(small) < CONTAINMENT:
                continue
            seen.add(key)

            same = a == b
            # The hub is the page with the more general (smaller) target.
            hub, spoke = (i, j) if len(a) < len(b) else (j, i)
            if same:
                hub, spoke = (i, j) if _strength(pages[i]) >= _strength(pages[j]) else (j, i)
            content = sim(hub, spoke)
            known = content is not None
            content_v = content or 0.0
            ph, ps = pages[hub], pages[spoke]
            cross = (ph.get("type") in COMMERCIAL) != (ps.get("type") in COMMERCIAL)

            if same:
                kind = "same-target"
                severity = "high"
            elif known and content_v >= SPOKE_HIGH:
                kind, severity = "copied-spoke", "high"
            elif cross and _close_variant(sigs[hub], sigs[spoke]):
                kind, severity = "blog-vs-landing", "medium" if content_v < SPOKE_MEDIUM else "high"
            elif known and content_v >= SPOKE_MEDIUM:
                kind, severity = "hub-spoke-overlap", "medium"
            else:
                kind, severity = "hub-spoke", "low"

            pairs.append(_pair(kind, severity, shared, ph, ps, phrase[hub], phrase[spoke], content))

    # ---- duplicate titles and H1s across every indexable page (supporting evidence)
    def dup_groups(field: str) -> list[dict]:
        g: dict[str, list[str]] = defaultdict(list)
        for r in pages:
            v = r.get(field)
            v = (v[0] if isinstance(v, list) and v else v) if v else ""
            if v and len(v.strip()) > 3:
                g[v.strip().lower()].append(r["url"])
        return [{"text": k, "urls": v} for k, v in g.items() if len(v) > 1]

    # ---- Search Console: the same query earning impressions on two of our URLs
    confirmed: list[dict] = []
    if gsc_rows:
        by_query: dict[str, list] = defaultdict(list)
        for row in gsc_rows:
            by_query[row.query].append(row)
        for q, rows in by_query.items():
            total = sum(r.impressions for r in rows)
            if total < 50 or len(rows) < 2:
                continue
            rows = sorted(rows, key=lambda r: -r.impressions)
            second = rows[1]
            if second.impressions / total < GSC_SPLIT_SHARE:
                continue
            confirmed.append({
                "query": q,
                "impressions": total,
                "pages": [{"url": r.page, "impressions": r.impressions, "clicks": r.clicks,
                           "position": round(r.position, 1)} for r in rows[:5]],
                "severity": "high" if abs(rows[0].position - second.position) < 5 else "medium",
            })
        confirmed.sort(key=lambda c: -c["impressions"])

    # ---- group pairs by the page that should win, for reading
    order = {"high": 0, "medium": 1, "low": 2}
    pairs.sort(key=lambda p: (order[p["severity"]], -(p["content_similarity"] or 0)))
    clusters: dict[str, dict] = {}
    for p in pairs:
        if p["severity"] == "low":
            continue
        c = clusters.setdefault(p["keep"], {
            "keep": p["keep"], "severity": p["severity"], "keyword": p["keyword"], "competitors": [],
        })
        other = p["spoke"] if p["keep"] == p["hub"] else p["hub"]
        c["competitors"].append({
            "url": other, "kind": p["kind"], "severity": p["severity"],
            "similarity": p["content_similarity"], "action": p["action"],
        })
        if order[p["severity"]] < order[c["severity"]]:
            c["severity"] = p["severity"]
    cluster_list = sorted(clusters.values(),
                          key=lambda c: (order[c["severity"]], -len(c["competitors"])))

    low_hubs: dict[str, int] = defaultdict(int)
    for p in pairs:
        if p["severity"] == "low":
            low_hubs[p["hub"]] += 1

    doc = {
        "at": date.today().isoformat(),
        "pages_checked": len(pages),
        "pairs": len(pairs),
        "by_severity": {s: sum(1 for p in pairs if p["severity"] == s) for s in ("high", "medium", "low")},
        "by_kind": {k: sum(1 for p in pairs if p["kind"] == k) for k in
                    ("same-target", "copied-spoke", "numbered-series", "blog-vs-landing",
                     "hub-spoke-overlap", "hub-spoke")},
        "clusters": cluster_list[:250],
        "healthy_hubs": [{"hub": h, "spokes": n} for h, n in
                         sorted(low_hubs.items(), key=lambda kv: -kv[1])[:60]],
        "duplicate_titles": dup_groups("title")[:150],
        "duplicate_h1s": dup_groups("h1")[:150],
        "confirmed": confirmed[:200],
        "gsc_connected": bool(gsc_rows),
        "method": {
            "signature": "target-keyword tokens (Search Console top query, else URL slug, else H1), "
                         "stemmed, without stop words or marketing words; numbers kept, because in a "
                         "numbered series the number is the page's identity",
            "series": "pages identical except for a number or year are a series — distinct documents "
                      f"unless their content is at least {int(SPOKE_MEDIUM * 100)}% identical",
            "same_intent": f"one signature contained in the other (≥{int(CONTAINMENT * 100)}%), "
                           f"sharing at least {MIN_SHARED} words",
            "content": "exact Jaccard similarity of 5-word shingles of the main content",
            "keep_rule": "commercial page over blog post, then more internal links, then more "
                         "content, then the shallower URL",
        },
    }
    state.save(_DOC.format(brand["id"]), doc)
    jobs.save_list(_PAIRS.format(brand["id"]), pairs)
    return doc


def _close_variant(general: frozenset[str], specific: frozenset[str]) -> bool:
    """Whether the more specific target is still the SAME search as the general one.

    A blog post competes with a landing page when it targets nearly the same
    query — ``clat coaching`` against ``clat coaching tips``. It does not when
    it narrows to a different entity: ``/blog/topper/air-15-clat-2025/`` adds a
    rank and a year, and nobody searching "clat toppers" is looking for one
    student's profile. So: at most one extra word, and no extra numbers.
    Treating every more-specific post as a competitor flagged the site's whole
    topper archive against its toppers page.
    """
    extra = specific - general
    return not any(t.isdigit() for t in extra) and len(extra) <= 1


def _short(url: str) -> str:
    import re

    return re.sub(r"^https?://[^/]+", "", url) or "/"


def _pair(kind: str, severity: str, shared: frozenset[str], ph: dict, ps: dict,
          hub_kw: str, spoke_kw: str, content: float | None) -> dict:
    return {
        "kind": kind,
        "severity": severity,
        # The shared target, read off the hub's own phrase so it is shown in the
        # page's words rather than as bare stems.
        "keyword": hub_kw,
        "hub": ph["url"], "hub_type": ph.get("type"), "hub_keyword": hub_kw,
        "hub_title": ph.get("title", ""), "hub_inlinks": ph.get("inlinks", 0),
        "hub_words": ph.get("word_count", 0),
        "spoke": ps["url"], "spoke_type": ps.get("type"), "spoke_keyword": spoke_kw,
        "spoke_title": ps.get("title", ""), "spoke_inlinks": ps.get("inlinks", 0),
        "spoke_words": ps.get("word_count", 0),
        "content_similarity": None if content is None else round(content, 3),
        "title_identical": bool(ph.get("title")) and
                           ph.get("title", "").strip().lower() == ps.get("title", "").strip().lower(),
        "keep": ph["url"] if _strength(ph) >= _strength(ps) else ps["url"],
        "action": _action(kind, ph, ps, content),
    }


def _action(kind: str, hub: dict, spoke: dict, content: float | None) -> str:
    h, s = _short(hub["url"]), _short(spoke["url"])
    pct = round((content or 0) * 100)
    if kind == "numbered-series":
        return (f"{s} and {h} are the same page with a different number or year — "
                f"{pct}% identical content. Search engines see one topic published twice. "
                f"Keep one evergreen URL and update it each cycle (or 301 the older one into "
                f"it), rather than re-publishing the page under a new number.")
    if kind == "same-target":
        return (f"Two pages target the same query. Keep the stronger one, merge anything unique "
                f"from the other into it, and 301 the other to it. If both must stay, retarget "
                f"one to a clearly different intent and change its title, H1 and slug to match.")
    if kind == "copied-spoke":
        return (f"{s} is {pct}% the same content as {h}. Google will index one and filter the "
                f"other. Rewrite the spoke around what is genuinely specific to it, or fold it "
                f"into the hub and 301.")
    if kind == "blog-vs-landing":
        blog, money = (s, h) if spoke.get("type") not in COMMERCIAL else (h, s)  # already short
        return (f"The blog post {blog} competes with the landing page {money}. Retarget the post "
                f"to an informational angle (how-to, eligibility, syllabus), keep the commercial "
                f"phrasing on the landing page, and link post → landing page with the head term "
                f"as the anchor.")
    if kind == "hub-spoke-overlap":
        return (f"{s} is a more specific version of {h} but shares {pct}% of its content. "
                f"Differentiate it and link it back to {h} with the broader keyword as the anchor.")
    return (f"Normal hub-and-spoke architecture — content is distinct ({pct}% shared). Make sure "
            f"{s} links to {h} and {h} links to its spokes.")


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))


def latest_pairs(brand_id: str) -> list[dict]:
    return jobs.load_list(_PAIRS.format(brand_id))[0]
