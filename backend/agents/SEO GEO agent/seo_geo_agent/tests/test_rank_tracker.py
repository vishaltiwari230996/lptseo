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


def test_pool_degrades_gracefully_when_rows_fn_raises(monkeypatch):
    """A failing rows_fn records a note and continues with other sources."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["custom only"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: ["seed only"])
    rt.record_harvest("b1", ["harvest only"])

    def failing_rows_fn(brand):
        raise RuntimeError("Network timeout")

    doc = rt.build_pool(_brand(), rows_fn=failing_rows_fn)

    assert [q["query"] for q in doc["queries"]] == ["custom only", "harvest only", "seed only"]
    assert any("Search Console" in n and "Network timeout" in n for n in doc["notes"])
    assert "gsc" not in doc["sources_used"]


def test_pool_takes_max_impressions_when_query_appears_in_multiple_sources(monkeypatch):
    """Earlier source wins identity, but maximum impressions survives."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    gsc = _rows(("CLAT Coaching", 777))

    doc = rt.build_pool(_brand(), rows_fn=lambda b: (gsc, []))

    assert len(doc["queries"]) == 1
    assert doc["queries"][0]["source"] == "custom"
    assert doc["queries"][0]["impressions"] == 777


from datetime import datetime, timezone


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def _result(query: str, position, top=None) -> dict:
    return {"query": query, "position": position, "url": "", "top": top or [], "error": None}


def test_append_history_records_one_raw_point_per_run():
    rt.append_history("b1", [_result("clat coaching", 9)], ["rival.com"], now=_at(5, 9))
    rt.append_history("b1", [_result("clat coaching", 7)], ["rival.com"], now=_at(5, 11))

    row = rt.history_for("b1", "clat coaching")
    assert [p[1] for p in row["raw"]] == [9, 7]
    assert row["raw"][0][0] < row["raw"][1][0]


def test_append_history_tracks_rival_positions_only_for_tracked_competitors():
    top = [{"position": 2, "domain": "rival.com", "url": "https://rival.com/a", "title": ""},
           {"position": 3, "domain": "driveby.com", "url": "https://driveby.com/b", "title": ""}]
    rt.append_history("b1", [_result("clat coaching", 9, top)], ["rival.com"], now=_at(5))

    row = rt.history_for("b1", "clat coaching")
    assert list(row["rivals"]) == ["rival.com"]
    assert row["rivals"]["rival.com"]["raw"][0][1] == 2


def test_append_history_skips_errored_results():
    """An empty or failed SERP is missing data, not a rank of None."""
    rt.append_history("b1", [{"query": "q", "position": None, "url": "", "top": [],
                              "error": "serper 429"}], [], now=_at(5))
    assert rt.history_for("b1", "q") is None


def test_rollup_collapses_raw_points_older_than_the_window_into_daily_triples():
    for hour in (8, 12, 16):
        rt.append_history("b1", [_result("q", {8: 11, 12: 7, 16: 9}[hour])], [], now=_at(1, hour))
    rt.append_history("b1", [_result("q", 5)], [], now=_at(20))  # inside the raw window

    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    daily_entry = row["daily"][0]
    assert daily_entry[0] == "2026-10-01"
    triple = daily_entry[1]
    assert triple["best"] == 7
    assert triple["worst"] == 11
    assert triple["last"] == 9
    assert "at" in triple and isinstance(triple["at"], int)


def test_rollup_is_idempotent_within_a_day():
    rt.append_history("b1", [_result("q", 4)], [], now=_at(1))
    rt.rollup("b1", today=date(2026, 10, 20))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    assert len(row["daily"]) == 1


def test_rollup_trims_dailies_past_the_retention_window():
    rt.append_history("b1", [_result("q", 4)], [], now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    rt.rollup("b1", today=date(2026, 10, 20))     # 2026-01-01 is 292 days back
    assert rt.history_for("b1", "q")["daily"] == []


def test_history_survives_a_query_leaving_the_pool():
    rt.append_history("b1", [_result("retired query", 12)], [], now=_at(5))
    assert rt.history_for("b1", "retired query")["raw"][0][1] == 12


def test_rollup_keeps_days_with_all_none_positions():
    """A day where every point was None still gets a daily entry."""
    for hour in (8, 12, 16):
        rt.append_history("b1", [_result("q", None)], [], now=_at(1, hour))
    
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    assert len(row["daily"]) == 1
    triple = row["daily"][0][1]
    assert triple["best"] is None
    assert triple["worst"] is None
    assert triple["last"] is None
    assert "at" in triple


def test_rollup_last_is_the_final_point_even_when_none():
    """On a mixed day, last reports the chronologically final position, even if None."""
    rt.append_history("b1", [_result("q", 5)], [], now=_at(1, 8))
    rt.append_history("b1", [_result("q", 3)], [], now=_at(1, 12))
    rt.append_history("b1", [_result("q", None)], [], now=_at(1, 16))  # fell out of results
    
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    triple = row["daily"][0][1]
    assert triple["best"] == 3
    assert triple["worst"] == 5
    assert triple["last"] is None


def test_rollup_reroll_with_earlier_stale_point_widens_but_preserves_last():
    """When re-rolling a day, an earlier point widens best/worst but doesn't change last or at."""
    rt.append_history("b1", [_result("q", 5)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    original_at = row["daily"][0][1]["at"]
    original_last = row["daily"][0][1]["last"]
    
    # Now discover an earlier point that wasn't included before (simulating backfill)
    rt.append_history("b1", [_result("q", 9)], [], now=_at(1, 8))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    triple = row["daily"][0][1]
    assert triple["best"] == 5
    assert triple["worst"] == 9  # widened
    assert triple["last"] == original_last  # unchanged
    assert triple["at"] == original_at  # unchanged


def test_rollup_reroll_with_later_stale_point_updates_last_and_at():
    """When re-rolling a day, a later point does replace last and at."""
    rt.append_history("b1", [_result("q", 5)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    original_at = row["daily"][0][1]["at"]
    
    # Now discover a later point for the same day
    rt.append_history("b1", [_result("q", 3)], [], now=_at(1, 16))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    triple = row["daily"][0][1]
    assert triple["best"] == 3
    assert triple["worst"] == 5
    assert triple["last"] == 3  # updated to later point
    assert triple["at"] > original_at  # updated to later hour
