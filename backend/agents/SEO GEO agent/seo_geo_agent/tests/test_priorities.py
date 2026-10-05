"""priorities.py — cross-source synthesis into one ranked action list."""
from __future__ import annotations

from unittest.mock import patch

from seo_geo_agent import priorities


def _brand() -> dict:
    return {"id": "b1", "domain": "lawpreptutorial.com"}


def test_critical_vitals_finding_ranks_above_a_warning():
    # Real vitals.latest() shape: mobile/desktop live under "origin_vitals",
    # and the assessment vocabulary is "passing"/"needs-improvement"/"failing"/
    # "insufficient-data" (see vitals._assessment) — not "poor".
    vitals_doc = {
        "origin_used": "https://www.lawpreptutorial.com",
        "origin_vitals": {
            "mobile": {
                "assessment": "failing",
                "metrics": {"largest_contentful_paint": {"category": "poor"}},
            },
        },
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
    assert severities.index("critical") < severities.index("warning")
    assert any(item["source"] == "vitals" for item in doc["items"])
    assert any(item["source"] == "keyword_pool" for item in doc["items"])


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


def test_deep_audit_surfaces_high_severity_and_template_findings():
    # Real deep_audit.latest() nests aggregate counts (not a per-check list) —
    # see deep_audit.run()'s summary["landing"]/["sitemap"] construction:
    # landing = {pages, avg_score, high, template_issues, grades},
    # sitemap = {score, affected, issues}.
    deep_doc = {
        "landing": {
            "pages": 40,
            "avg_score": 61,
            "high": 12,
            "template_issues": 3,
            "grades": {"A": 5, "B": 10, "C": 15, "D": 8, "F": 2},
        },
        "sitemap": {"score": 30, "affected": 20, "issues": 4},
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

    sources = [item["source"] for item in doc["items"]]
    assert sources.count("deep_audit") >= 3  # high-severity, template-level, sitemap score
    assert any(item["severity"] == "critical" for item in doc["items"])


def test_list_is_capped_at_ten_items():
    # Real insights.latest_run() todos have "action"/"why" fields (not "title")
    # and "status" in {"todo", "assigned", "done"} — see insights._todo().
    insights_doc = {
        "todos": [
            {
                "id": f"t{i}",
                "kind": "striking",
                "page": f"/page-{i}",
                "query": f"keyword {i}",
                "action": f"Refresh content targeting keyword {i}",
                "why": "Ranks on page 2; a content refresh could realistically reach page 1.",
                "est_monthly_clicks": 10,
                "position": 12.0,
                "impressions": 500,
                "status": "todo",
            }
            for i in range(15)
        ],
    }
    with (
        patch("seo_geo_agent.priorities.vitals.latest", return_value=None),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=None),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=None),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=insights_doc),
    ):
        doc = priorities.build("b1")

    assert len(doc["items"]) <= 10


def test_insights_todos_marked_done_are_excluded():
    insights_doc = {
        "todos": [
            {
                "id": "t1", "kind": "striking", "page": "/a", "query": "kw",
                "action": "Refresh /a", "why": "why", "est_monthly_clicks": 10,
                "position": 8.0, "impressions": 200, "status": "done",
            },
        ],
    }
    with (
        patch("seo_geo_agent.priorities.vitals.latest", return_value=None),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=None),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=None),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=insights_doc),
    ):
        doc = priorities.build("b1")

    assert doc["items"] == []


def test_a_source_raising_degrades_with_a_note_instead_of_blowing_up_build():
    # A transient error (e.g. a Firestore hiccup) inside any _from_* source
    # must not propagate out of build() — priorities/Insights is the default
    # landing view, so an unhandled exception here would blank the first
    # thing a user sees when opening a brand.
    with (
        patch("seo_geo_agent.priorities.vitals.latest", side_effect=RuntimeError("boom")),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=None),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=None),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=None),
    ):
        doc = priorities.build("b1")

    assert doc["items"] == []
    assert any("Core Web Vitals" in n and "RuntimeError" in n for n in doc["notes"])


def test_insights_todo_missing_id_is_skipped_not_raised():
    insights_doc = {
        "todos": [
            {"action": "Refresh /a", "why": "why", "status": "todo"},  # no "id"
            {
                "id": "t2", "action": "Refresh /b", "why": "why", "status": "todo",
            },
        ],
    }
    with (
        patch("seo_geo_agent.priorities.vitals.latest", return_value=None),
        patch("seo_geo_agent.priorities.keyword_pool.latest", return_value=None),
        patch("seo_geo_agent.priorities.competitors.latest_profiles", return_value=None),
        patch("seo_geo_agent.priorities.deep_audit.latest", return_value=None),
        patch("seo_geo_agent.priorities.insights.latest_run", return_value=insights_doc),
    ):
        doc = priorities.build("b1")

    assert [item["id"] for item in doc["items"]] == ["insights-t2"]


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
