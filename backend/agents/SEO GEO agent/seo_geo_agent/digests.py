"""Typed digests — the only shapes an LLM prompt may be built from.

Every data source in this agent persists far more than a prompt should carry:
200 swept queries each with a top-20 list, a full crawl, per-page findings.
Dumping those into a prompt is how a model starts "summarising" data it never
actually read. So each source gets ONE digest function here: stable keys,
counts plus a capped list of named examples, `None` when the source has
nothing — and the callers (advisor context, rank analysis, the insights
brief) compose prompts from digests only.

The discipline, in one line: **Python aggregates, the model reads aggregates.**
A digest function must never fetch anything over the network — it reads what
previous runs persisted, so building a prompt is always free and offline-safe.
"""
from __future__ import annotations

from collections import Counter

from . import (competitors, deep_audit, insights, keyword_pool, landing_audit,
               rank_tracker, site_brain, state, vitals)

#: Named examples per list — enough to act on, never a table dump.
MAX_EXAMPLES = 5


def rank_digest(brand: dict) -> dict | None:
    """Where the brand stands in live SERPs, pre-chewed: counts by band, the
    week's movers with who overtook us, dropouts, and which rival domains sit
    above us most often ("pressure")."""
    rows = rank_tracker.annotate_rows(brand)
    if not rows:
        return None
    ranked = [r for r in rows if r.get("position")]
    movers_down = sorted((r for r in rows if (r.get("delta_7d") or 0) < 0),
                         key=lambda r: r["delta_7d"])[:MAX_EXAMPLES]
    movers_up = sorted((r for r in rows if (r.get("delta_7d") or 0) > 0),
                       key=lambda r: -r["delta_7d"])[:MAX_EXAMPLES]
    pressure = Counter()
    for r in rows:
        position = r.get("position")
        above = {e["domain"] for e in r.get("top") or []
                 if e.get("domain") and (position is None or e.get("position", 99) < position)}
        pressure.update(above)
    sweep = rank_tracker.last_sweep(brand["id"]) or {}

    def mover(r: dict) -> dict:
        return {"query": r["query"],
                "from": (r["position"] + r["delta_7d"]) if r.get("position") else None,
                "to": r.get("position"),
                "leader": r.get("leader"), "leader_position": r.get("leader_position")}

    return {
        "at": sweep.get("at"),
        "tracked": len(rows),
        "top3": sum(1 for r in ranked if r["position"] <= 3),
        "page1": sum(1 for r in ranked if r["position"] <= 10),
        "striking_4_20": sum(1 for r in ranked if 4 <= r["position"] <= 20),
        "unranked": len(rows) - len(ranked),
        "best": [{"query": r["query"], "position": r["position"]}
                 for r in sorted(ranked, key=lambda r: r["position"])[:MAX_EXAMPLES]],
        "movers_down": [mover(r) for r in movers_down],
        "movers_up": [mover(r) for r in movers_up],
        "dropouts_7d": [r["query"] for r in rows if r.get("dropped")][:MAX_EXAMPLES],
        "pressure": [{"domain": d, "above_us_on": n} for d, n in pressure.most_common(MAX_EXAMPLES)],
    }


def deep_digest(brand_id: str) -> dict | None:
    """The deep audit's already-aggregated verdicts: per-diagnostic counts,
    the template faults worth fixing once, and real-visitor CWV assessments.
    The raw crawl never comes anywhere near a prompt."""
    summary = deep_audit.latest(brand_id)
    if not summary:
        return None
    section = {
        "at": summary.get("at"),
        "urls_crawled": summary.get("urls"),
        "live_pages": summary.get("live_pages"),
        "sitemap": summary.get("sitemap"),
        "landing": summary.get("landing"),
        "cannibalization": summary.get("cannibalization"),
        "blog_keyword_density": summary.get("density"),
    }
    landing = landing_audit.latest(brand_id) or {}
    template_faults = [
        {"check": m.get("label"), "severity": m.get("severity"), "pages": m.get("count")}
        for m in landing.get("issues", []) if m.get("template")
    ][:6]
    if template_faults:
        section["template_faults_fix_once"] = template_faults
    cwv = vitals.latest(brand_id) or {}
    origin = cwv.get("origin_vitals") or {}
    assessments = {ff: (origin.get(ff) or {}).get("assessment")
                   for ff in ("mobile", "desktop") if (origin.get(ff) or {}).get("assessment")}
    if assessments:
        section["core_web_vitals_field"] = assessments
    return section


def traffic_digest(brand_id: str) -> dict | None:
    """The latest run's summary plus its highest-value to-dos."""
    run = insights.latest_run(brand_id)
    if not run:
        return None
    return {
        "at": run.get("at"),
        "summary": run.get("summary"),
        "data_gaps": run.get("degraded"),
        "top_fixes": [
            {k: t.get(k) for k in ("kind", "action", "why", "est_monthly_clicks", "position", "status")}
            for t in run.get("todos", [])[:8]
        ],
        "top_blog_topics": [
            {k: t.get(k) for k in ("keyword", "priority", "trend", "difficulty", "impact")}
            for t in run.get("topics", [])[:8]
        ],
    }


def keyword_digest(brand_id: str) -> dict | None:
    """The pool's totals and bands, plus the top opportunities by estimated
    winnable clicks — never the 200-row table."""
    doc = keyword_pool.latest(brand_id)
    if not doc:
        return None
    return {
        "at": doc.get("at"),
        "totals": doc.get("totals"),
        "bands": doc.get("bands"),
        "top_opportunities": [
            {k: r.get(k) for k in ("keyword", "position", "impressions", "opportunity", "cluster")}
            for r in doc.get("keywords", [])[:8]
        ],
    }


def review_digest(brand_id: str) -> dict | None:
    """The expert site review, grades and top findings only."""
    review = site_brain.latest_review(brand_id)
    if not review:
        return None
    return {
        "at": review.get("at"),
        "positioning": review.get("positioning"),
        "scorecard": {k: v.get("grade") for k, v in (review.get("scorecard") or {}).items()},
        "strengths": review.get("strengths", [])[:4],
        "top_issues": [
            {k: i.get(k) for k in ("insight", "priority", "category")}
            for i in review.get("issues", [])[:MAX_EXAMPLES]
        ],
        "missing_topics": review.get("missing_topics", [])[:6],
    }


def competitor_digest(brand_id: str) -> dict | None:
    """Tracked rivals' profiles, one line each."""
    doc = competitors.latest_profiles(brand_id)
    if not doc or not doc.get("profiles"):
        return None
    return {
        "at": doc.get("at"),
        "profiles": [
            {"domain": p.get("domain"), "visibility_pct": p.get("visibility_pct"),
             "avg_position": p.get("avg_position"), "keywords_won": p.get("keywords_won"),
             "hot_topics": (p.get("hot_topics") or [])[:3],
             "new_posts": len(p.get("recent_posts") or [])}
            for p in doc["profiles"][:MAX_EXAMPLES]
        ],
    }


def briefs_digest(brand_id: str) -> list[str] | None:
    from . import briefs
    keywords = [b["keyword"] for b in briefs.list_briefs(brand_id)]
    return keywords or None


def sitemap_watch_digest(brand_id: str) -> dict | None:
    """Competitors' newly published URLs, counts plus a few examples."""
    sitemaps = state.load(f"sitemaps-{brand_id}") or {}
    feed = sitemaps.get("last_feed") or {}
    if not feed:
        return None
    return {
        d: {"new_count": e.get("new_count"), "new_urls": (e.get("new_urls") or [])[:MAX_EXAMPLES]}
        for d, e in feed.items()
    }
