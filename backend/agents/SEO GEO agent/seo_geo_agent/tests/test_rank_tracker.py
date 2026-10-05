"""Rank tracker — pool building, budget guard, sweep, history, worklist."""
from __future__ import annotations

from datetime import date

from seo_geo_agent import rank_tracker as rt


def _brand(**over) -> dict:
    brand = {"id": "b1", "name": "Law Prep Tutorial", "domain": "lawpreptutorial.com",
             "seeds": ["clat coaching"], "competitors": ["rival.com"]}
    brand.update(over)
    return brand


def test_budget_starts_empty_and_reports_the_cap():
    status = rt.budget_status("b1", today=date(2026, 10, 5))
    assert status == {"date": "2026-10-05", "searches": 0,
                      "cap": rt.MAX_SEARCHES_PER_DAY,
                      "remaining": rt.MAX_SEARCHES_PER_DAY}


def test_charge_accumulates_within_the_day():
    rt.charge("b1", 10, today=date(2026, 10, 5))
    rt.charge("b1", 5, today=date(2026, 10, 5))
    assert rt.budget_status("b1", today=date(2026, 10, 5))["searches"] == 15


def test_charge_refuses_past_the_cap_and_does_not_partially_spend():
    rt.charge("b1", rt.MAX_SEARCHES_PER_DAY, today=date(2026, 10, 5))
    assert rt.charge("b1", 1, today=date(2026, 10, 5)) is False
    assert rt.budget_status("b1", today=date(2026, 10, 5))["searches"] == rt.MAX_SEARCHES_PER_DAY


def test_budget_resets_on_a_new_date():
    """A sweep that crosses midnight charges against the date at charge time."""
    rt.charge("b1", 2900, today=date(2026, 10, 5))
    assert rt.charge("b1", 1, today=date(2026, 10, 6)) is True
    assert rt.budget_status("b1", today=date(2026, 10, 6))["searches"] == 1


def test_enabled_defaults_true_and_honours_the_brand_flag_and_env(monkeypatch):
    assert rt.enabled(_brand()) is True
    assert rt.enabled(_brand(rank_tracking_enabled=False)) is False
    monkeypatch.setenv("SEO_RANK_SWEEP_DISABLED", "1")
    assert rt.enabled(_brand()) is False


def test_norm_collapses_case_and_whitespace():
    assert rt._norm("  CLAT   Coaching  ") == "clat coaching"


def _rows(*pairs):
    """Minimal GSC rows: (query, impressions)."""
    class Row:
        def __init__(self, query, impressions):
            self.query, self.impressions = query, impressions
            self.page, self.clicks, self.ctr, self.position = "", 0, 0.0, 0.0
    return [Row(q, i) for q, i in pairs]


def test_pool_puts_custom_queries_first_and_keeps_them_past_the_cap(monkeypatch):
    from seo_geo_agent import competitors
    custom = [f"custom query {n}" for n in range(5)]
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: custom)
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(*[(f"gsc query {n}", 100) for n in range(rt.MAX_POOL)])

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    queries = [q["query"] for q in doc["queries"]]
    assert len(queries) == rt.MAX_POOL
    assert queries[:5] == custom          # custom survives the cap
    assert doc["queries"][0]["source"] == "custom"


def test_pool_sorts_gsc_by_impressions_and_drops_noise(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(("low demand", 5), ("high demand", 900), ("noise", 1))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert [q["query"] for q in doc["queries"]] == ["high demand", "low demand"]
    assert doc["queries"][0]["impressions"] == 900


def test_pool_sums_impressions_for_one_query_across_pages(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(("clat coaching", 60), ("clat coaching", 40), ("other", 80))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert [q["query"] for q in doc["queries"]] == ["clat coaching", "other"]
    assert doc["queries"][0]["impressions"] == 100


def test_pool_dedups_on_case_and_whitespace(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["CLAT Coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: ["clat   coaching"])
    gsc = _rows(("  clat coaching  ", 500))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert len(doc["queries"]) == 1
    assert doc["queries"][0]["source"] == "custom"


def test_pool_degrades_with_a_note_when_search_console_is_missing(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: ["clat coaching"])

    doc = rt.build_pool(_brand(), rows_fn=lambda b: ([], ["Search Console: no access"]))

    assert [q["query"] for q in doc["queries"]] == ["clat coaching"]
    assert any("Search Console" in n for n in doc["notes"])
    assert "gsc" not in doc["sources_used"]


def test_harvested_queries_enter_the_pool_most_frequent_first(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.record_harvest("b1", ["rare question"])
    rt.record_harvest("b1", ["common question", "common question"])
    rt.record_harvest("b1", ["common question"])

    doc = rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    assert [q["query"] for q in doc["queries"]] == ["common question", "rare question"]
    assert doc["queries"][0]["source"] == "harvest"


def test_pool_drops_over_long_queries(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(("x" * (rt.MAX_QUERY_LEN + 1), 900), ("fine", 10))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert [q["query"] for q in doc["queries"]] == ["fine"]


def test_rebuild_marks_dropped_queries_inactive_instead_of_deleting_them(monkeypatch):
    """History stays readable for a query that left the pool."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["going away"])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["still here"])
    doc = rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    by_query = {q["query"]: q for q in doc["queries"]}
    assert by_query["going away"]["active"] is False
    assert by_query["still here"]["active"] is True
    assert rt.active_queries("b1") == ["still here"]
