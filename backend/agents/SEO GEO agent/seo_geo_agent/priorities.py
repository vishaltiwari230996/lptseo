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
    origin_vitals = doc.get("origin_vitals") or {}
    for form_factor in ("mobile", "desktop"):
        slice_ = origin_vitals.get(form_factor) or {}
        assessment = slice_.get("assessment")
        if assessment == "failing":
            items.append({
                "id": f"vitals-{form_factor}",
                "title": f"Core Web Vitals are failing on {form_factor}",
                "why_it_matters": "Real visitors are experiencing slow/unstable pages, which Google uses as a ranking signal.",
                "severity": "critical",
                "source": "vitals",
                "action_link": "#vitals",
            })
        elif assessment == "needs-improvement":
            items.append({
                "id": f"vitals-{form_factor}",
                "title": f"Core Web Vitals need improvement on {form_factor}",
                "why_it_matters": "Some visitors are getting a degraded experience — closing the gap protects the ranking signal before it turns critical.",
                "severity": "warning",
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
    """Deep audit findings, from the aggregate counts the summary carries.

    ``deep_audit.latest()`` only stores per-source aggregate counts (see
    ``deep_audit.run()``), not a per-check list of faults — the detailed
    issue matrix (check name, per-page evidence) lives in
    ``landing_audit``'s own persisted doc, which is out of scope for this
    module's declared interface. So the items below are built from
    ``landing.high`` / ``landing.template_issues`` / ``sitemap.score``.
    """
    doc = deep_audit.latest(brand_id)
    if not doc:
        return [], "Deep audit: no report yet"
    items: list[dict] = []
    landing = doc.get("landing") or {}
    high = landing.get("high") or 0
    if high:
        items.append({
            "id": "deep-audit-landing-high",
            "title": f"{high} high-severity landing page issue(s) found",
            "why_it_matters": "High-severity findings — missing titles, noindex, broken canonicals, thin content — directly block a page from ranking.",
            "severity": "critical",
            "source": "deep_audit",
            "action_link": "#deep-audit",
        })
    template_issues = landing.get("template_issues") or 0
    if template_issues:
        items.append({
            "id": "deep-audit-template-issues",
            "title": f"{template_issues} template-level issue(s) repeat across many pages",
            "why_it_matters": "A template-level fault is fixed once and clears on every page built from that template.",
            "severity": "warning",
            "source": "deep_audit",
            "action_link": "#deep-audit",
        })
    sitemap = doc.get("sitemap") or {}
    score = sitemap.get("score")
    if score is not None and score < 50:
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
    items: list[dict] = []
    for todo in (run.get("todos") or []):
        if todo.get("status", "todo") == "done":
            continue
        todo_id = todo.get("id")
        if not todo_id:
            # A malformed/legacy todo missing its id is skipped, not raised —
            # this list must never fail to render because one entry is bad.
            continue
        items.append({
            "id": f"insights-{todo_id}",
            "title": todo.get("action", "Untitled"),
            "why_it_matters": todo.get("why", "Estimated traffic gain available in the Traffic & rankings section."),
            "severity": "warning",
            "source": "insights",
            "action_link": "#traffic",
        })
    return items, None


_SOURCES = (
    ("Core Web Vitals", _from_vitals),
    ("Keyword pool", _from_keyword_pool),
    ("Competitors", _from_competitors),
    ("Deep audit", _from_deep_audit),
    ("Rank tracking", _from_insights),
)


def build(brand_id: str) -> dict:
    items: list[dict] = []
    notes: list[str] = []
    for name, source_fn in _SOURCES:
        # A source's own `_from_*` helper already turns "nothing to say" into
        # ([], note) — this try/except covers the other failure mode: a
        # transient error (e.g. a Firestore hiccup) raised mid-read. Either
        # way this list degrades with a note, it never fails to render.
        try:
            source_items, note = source_fn(brand_id)
        except Exception as exc:
            source_items, note = [], f"{name}: unexpected error ({type(exc).__name__})"
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
