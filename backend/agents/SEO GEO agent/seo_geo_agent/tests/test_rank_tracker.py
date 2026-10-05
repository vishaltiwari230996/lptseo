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


def test_rollup_last_is_chronologically_final_when_points_appended_out_of_order():
    """Even if points for the same day are appended out of hour order, last is the final hour's value."""
    rt.append_history("b1", [_result("q", 3)], [], now=_at(1, 16))
    rt.append_history("b1", [_result("q", 9)], [], now=_at(1, 8))  # earlier, appended second

    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    triple = row["daily"][0][1]
    assert triple["best"] == 3
    assert triple["worst"] == 9
    assert triple["last"] == 3  # hour 16 is chronologically last
    assert triple["at"] == rt._epoch_hours(_at(1, 16))  # at hour 16


def test_rollup_reroll_with_equal_at_replaces_last_and_at():
    """A re-roll whose new final point has exactly the stored at should replace last and at."""
    rt.append_history("b1", [_result("q", 5)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    original_at = row["daily"][0][1]["at"]

    # Append new point at exactly the same hour (simulating backfill with same timestamp)
    rt.append_history("b1", [_result("q", 7)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    triple = row["daily"][0][1]
    assert triple["last"] == 7  # replaced with new observation at same hour
    assert triple["at"] == original_at  # same hour


def test_rollup_rival_last_is_chronologically_final():
    """Rival series follow the same logic: last is the chronologically final point."""
    top = [{"position": 8, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 5, top)], ["rival.com"], now=_at(1, 16))
    top = [{"position": 12, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, top)], ["rival.com"], now=_at(1, 8))

    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    rival_triple = row["rivals"]["rival.com"]["daily"][0][1]
    assert rival_triple["best"] == 8
    assert rival_triple["worst"] == 12
    assert rival_triple["last"] == 8  # hour 16 is chronologically last


def test_rollup_rival_reroll_with_equal_at_replaces_last():
    """Rival series re-roll with equal at should replace last and at."""
    top = [{"position": 5, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, top)], ["rival.com"], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    original_at = row["rivals"]["rival.com"]["daily"][0][1]["at"]

    # Append new rival point at exactly the same hour
    top = [{"position": 3, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, top)], ["rival.com"], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    rival_triple = row["rivals"]["rival.com"]["daily"][0][1]
    assert rival_triple["last"] == 3  # replaced with new observation
    assert rival_triple["at"] == original_at


def _serp(*entries):
    """entries: (position, url)."""
    return {"organic": [{"position": p, "link": u, "title": f"t{p}"} for p, u in entries],
            "related": [], "paa": [], "aio_present": False}


def test_sweep_records_our_position_and_the_full_top_list(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        return _serp((1, "https://rival.com/a"), (2, "https://lawpreptutorial.com/clat"))

    out = rt.sweep(_brand(), search=search)

    assert out["checked"] == 1 and out["ranked"] == 1 and out["errors"] == 0
    row = rt.latest_rows("b1")[0]
    assert row["position"] == 2
    assert row["url"] == "https://lawpreptutorial.com/clat"
    assert [e["domain"] for e in row["top"]] == ["rival.com", "lawpreptutorial.com"]


def test_sweep_does_not_match_a_lookalike_domain(monkeypatch):
    """`domain in link` matches fake-lawpreptutorial.com.spam.io — a phantom rank."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        return _serp((1, "https://fake-lawpreptutorial.com.spam.io/x"),
                     (2, "https://www.lawpreptutorial.com/clat"))

    rt.sweep(_brand(), search=search)

    row = rt.latest_rows("b1")[0]
    assert row["position"] == 2           # the www. form of our real domain
    assert row["url"].endswith("/clat")


def test_sweep_continues_past_a_query_that_raises(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["good", "bad", "also good"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        if query == "bad":
            raise RuntimeError("serper 429")
        return _serp((3, "https://lawpreptutorial.com/x"))

    out = rt.sweep(_brand(), search=search)

    assert out["checked"] == 3 and out["ranked"] == 2 and out["errors"] == 1
    bad = next(r for r in rt.latest_rows("b1") if r["query"] == "bad")
    assert "429" in bad["error"] and bad["position"] is None


def test_sweep_treats_an_empty_serp_as_an_error_not_an_unranked_result(monkeypatch):
    """A rate-limited provider returns nothing for everything; that is missing
    data, and must not be written into history as a site-wide collapse."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    out = rt.sweep(_brand(), search=lambda query, **kw: _serp())

    assert out["errors"] == 1
    assert rt.latest_rows("b1")[0]["error"] == "empty SERP"
    assert rt.history_for("b1", "q") is None


def test_sweep_refuses_when_the_daily_budget_is_exhausted(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))
    rt.charge("b1", rt.MAX_SEARCHES_PER_DAY)

    calls = []
    out = rt.sweep(_brand(), search=lambda q, **kw: calls.append(q) or _serp())

    assert out["blocked"] == "budget"
    assert calls == []


def test_sweep_refuses_when_rank_tracking_is_disabled_for_the_brand(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    calls = []
    out = rt.sweep(_brand(rank_tracking_enabled=False),
                   search=lambda q, **kw: calls.append(q) or _serp())

    assert out["blocked"] == "disabled" and calls == []


def test_sweep_charges_one_credit_per_query(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["a", "b", "c"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    rt.sweep(_brand(), search=lambda q, **kw: _serp((1, "https://x.com/")))

    assert rt.budget_status("b1")["searches"] == 3


def test_sweep_harvests_related_and_paa_for_the_next_rebuild(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        serp = _serp((1, "https://x.com/"))
        serp["related"] = ["clat syllabus"]
        serp["paa"] = ["how hard is clat"]
        return serp

    rt.sweep(_brand(), search=search)

    assert set(rt._harvest_ranked("b1")) == {"clat syllabus", "how hard is clat"}


def test_sweep_skips_inactive_queries(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["old"])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["new"])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    seen = []
    rt.sweep(_brand(), search=lambda q, **kw: seen.append(q) or _serp((1, "https://x.com/")))

    assert seen == ["new"]


def test_sweep_uses_brand_serp_country_override_for_the_real_provider(monkeypatch):
    """Global Constraints: SERP country is overridable per brand via
    ``serp_country``. The sweep is the only caller holding the brand doc, so
    it must plumb that override into the real provider call — but an
    injected ``search`` (every other test here) must still be called as
    ``search(query)`` with no extra kwargs."""
    from seo_geo_agent import competitors, sources
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    monkeypatch.setattr(sources, "brand_rank_available", lambda: True)
    seen_gl = []

    def fake_provider(query, client=None, *, gl="in", hl="en", num=20):
        seen_gl.append(gl)
        return _serp((1, "https://x.com/"))

    monkeypatch.setattr(sources, "brand_rank_search", fake_provider)

    rt.sweep(_brand(serp_country="ae"))
    assert seen_gl == ["ae"]

    seen_gl.clear()
    rt.sweep(_brand())
    assert seen_gl == ["in"]


def test_sweep_backfills_position_from_the_normalised_top_list_when_the_provider_omits_it(monkeypatch):
    """`top` already backfills a missing `position` via enumerate; `row["position"]`
    must read from that same normalised list rather than the raw organic entry a
    second time — otherwise a provider that omits `position` on our own listing
    silently records a real rank as unranked."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        return {"organic": [
                    {"link": "https://rival.com/a", "title": "t1"},
                    {"link": "https://other.com/b", "title": "t2"},
                    {"link": "https://lawpreptutorial.com/clat", "title": "t3"},
                ],  # no "position" key anywhere
                "related": [], "paa": [], "aio_present": False}

    rt.sweep(_brand(), search=search)

    row = rt.latest_rows("b1")[0]
    assert row["position"] == 3
    assert [e["position"] for e in row["top"]] == [1, 2, 3]


def test_build_pool_between_two_same_day_sweeps_does_not_retrigger_rollup(monkeypatch):
    """The rollup stamp is sweep state, not pool state: a same-day pool
    rebuild sitting between two sweeps must not cause a second rollup."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    calls = []
    original = rt.rollup
    monkeypatch.setattr(rt, "rollup", lambda *a, **kw: calls.append(1) or original(*a, **kw))

    rt.sweep(_brand(), search=lambda q, **kw: _serp((1, "https://x.com/")), now=_at(5, 9))
    assert calls == [1]

    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))  # same-day rebuild, between sweeps

    rt.sweep(_brand(), search=lambda q, **kw: _serp((1, "https://x.com/")), now=_at(5, 11))
    assert calls == [1]  # still just the one rollup from the first sweep


def test_sweep_rolls_up_again_on_a_new_utc_date(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    calls = []
    original = rt.rollup
    monkeypatch.setattr(rt, "rollup", lambda *a, **kw: calls.append(1) or original(*a, **kw))

    rt.sweep(_brand(), search=lambda q, **kw: _serp((1, "https://x.com/")), now=_at(5, 9))
    rt.sweep(_brand(), search=lambda q, **kw: _serp((1, "https://x.com/")), now=_at(6, 9))

    assert calls == [1, 1]


def test_position_band_peaks_in_striking_distance():
    assert rt._position_band(8) > rt._position_band(2)     # already won: low value
    assert rt._position_band(8) > rt._position_band(60)    # unwinnable: low value
    assert rt._position_band(None) < rt._position_band(8)  # unranked: floor, not zero
    assert rt._position_band(None) > 0


def _seed_latest(rows, rivals=("rival.com",)):
    from seo_geo_agent import jobs as j
    j.save_list(rt.LATEST_PREFIX.format("b1"), rows,
                meta={"at": "2026-10-05T09:00:00+00:00", "ranked": len(rows),
                      "errors": 0, "rivals": list(rivals)})


def test_worklist_ranks_high_demand_striking_distance_queries_first(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("winnable", 5000), ("obscure", 5),
                                                     ("already won", 9000)), []))
    _seed_latest([
        {"query": "winnable", "position": 8, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "https://rival.com/a", "title": ""}]},
        {"query": "obscure", "position": 7, "url": "", "error": None,
         "top": [{"position": 1, "domain": "other.com", "url": "https://other.com/a", "title": ""}]},
        {"query": "already won", "position": 1, "url": "", "error": None, "top": []},
    ])

    rows = rt.worklist(_brand())

    assert [r["query"] for r in rows][:1] == ["winnable"]
    assert rows[0]["leader"] == "rival.com" and rows[0]["tracked_rival"] is True
    assert "already won" not in [r["query"] for r in rows[:2]]


def test_worklist_excludes_errored_rows():
    _seed_latest([{"query": "broken", "position": None, "url": "",
                   "error": "serper 429", "top": []}])
    assert rt.worklist(_brand()) == []


def test_worklist_promotes_a_query_that_lost_ground_this_week():
    _seed_latest([
        {"query": "slipping", "position": 12, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
        {"query": "steady", "position": 12, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
    ])
    rt.append_history("b1", [_result("slipping", 4), _result("steady", 12)], [],
                      now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    rt.append_history("b1", [_result("slipping", 12), _result("steady", 12)], [],
                      now=datetime(2026, 10, 5, tzinfo=timezone.utc))

    rows = rt.worklist(_brand())

    assert rows[0]["query"] == "slipping"
    assert rows[0]["delta_7d"] == -8      # negative = we fell


def test_worklist_respects_the_limit():
    _seed_latest([
        {"query": f"q{n}", "position": 9, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]}
        for n in range(30)
    ])
    assert len(rt.worklist(_brand(), limit=5)) == 5


# === Comprehensive boundary and edge-case tests ===

def test_position_band_boundary_3_vs_4_and_20_vs_21():
    """Position band thresholds are inclusive/exclusive as documented."""
    assert rt._position_band(3) == 0.2       # < 4 is won
    assert rt._position_band(4) == 1.0       # >= 4 is striking distance
    assert rt._position_band(20) == 1.0      # <= 20 is striking distance
    assert rt._position_band(21) == 0.5      # > 20 is fading


def test_position_band_boundary_40_vs_41():
    """Position band thresholds at the 40-boundary."""
    assert rt._position_band(40) == 0.5      # <= 40 is mid-tier
    assert rt._position_band(41) == 0.1      # > 40 is nearly impossible


def test_delta_on_empty_history():
    """delta returns None when history row is missing."""
    assert rt.delta("b1", "never seen", hours=168) is None


def test_delta_on_single_point():
    """delta returns None when there is only one ranked point."""
    rt.append_history("b1", [_result("single", 5)], [], now=_at(5))
    assert rt.delta("b1", "single", hours=168) is None


def test_delta_on_all_none_positions():
    """delta returns None when all points are unranked (None)."""
    rt.append_history("b1", [_result("all none", None)], [], now=_at(1))
    rt.append_history("b1", [_result("all none", None)], [], now=_at(5))
    assert rt.delta("b1", "all none", hours=168) is None


def test_delta_latest_point_is_none_returns_none():
    """delta returns None when current position is unranked, even if history exists."""
    rt.append_history("b1", [_result("dropout", 8)], [], now=_at(1))
    rt.append_history("b1", [_result("dropout", None)], [], now=_at(5))
    assert rt.delta("b1", "dropout", hours=168) is None


def test_delta_series_entirely_within_window():
    """delta compares against the earliest point when series is younger than window."""
    rt.append_history("b1", [_result("young", 10)], [], now=_at(5, 9))
    rt.append_history("b1", [_result("young", 7)], [], now=_at(5, 11))
    # Both points are within the 168-hour window; compare against the first.
    assert rt.delta("b1", "young", hours=168) == 3  # 10 - 7


def test_delta_series_spanning_cutoff_uses_cutoff_point():
    """delta uses the most recent point at or before the cutoff."""
    rt.append_history("b1", [_result("old", 10)], [], now=_at(1, 9))     # day 1 at 9am
    rt.append_history("b1", [_result("old", 8)], [], now=_at(5, 9))      # day 5 at 9am
    rt.append_history("b1", [_result("old", 6)], [], now=_at(8, 12))     # day 8 at noon
    # Window is 7 days (168 hours). Latest is day 8 12:00, cutoff is day 1 12:00.
    # Point at day 1 9:00 is before cutoff (1 9:00 < 1 12:00), so it's the baseline.
    # Point at day 5 9:00 is after cutoff, so skip it.
    # delta = 10 - 6 = 4 (we improved by 4 positions).
    assert rt.delta("b1", "old", hours=7 * 24) == 4


def test_lost_ranking_on_current_dropout():
    """lost_ranking is true when latest point is None and we were ranked in window."""
    rt.append_history("b1", [_result("dropped", 8)], [], now=_at(1))
    rt.append_history("b1", [_result("dropped", None)], [], now=_at(5))
    assert rt.lost_ranking("b1", "dropped", hours=168) is True


def test_lost_ranking_on_no_dropout():
    """lost_ranking is false when currently ranked."""
    rt.append_history("b1", [_result("stable", 8)], [], now=_at(1))
    rt.append_history("b1", [_result("stable", 7)], [], now=_at(5))
    assert rt.lost_ranking("b1", "stable", hours=168) is False


def test_lost_ranking_outside_window():
    """lost_ranking is false when ranked point is outside the window."""
    rt.append_history("b1", [_result("outside", 8)], [], now=_at(1))  # 7+ days ago
    rt.append_history("b1", [_result("outside", None)], [], now=_at(8, 12))  # just now
    # Window is 168 hours (7 days). Point at day 1 is older than cutoff (day 8 - 7 = day 1, 00:00).
    # At day 8 12:00, cutoff is day 1 12:00. Point at day 1 is before cutoff, so outside window.
    assert rt.lost_ranking("b1", "outside", hours=168) is False


def test_worklist_handles_dropout_with_band_1_0_and_trend_1_6(monkeypatch):
    """A query dropped from results gets band=1.0 (striking distance) and trend=1.6 multiplier."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("dropped", 5000)), []))

    # Seed latest: query is now unranked (position: None) but has no competitor above it yet.
    # We need to set it up so it has a leader via some top entry.
    _seed_latest([
        {"query": "dropped", "position": None, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
    ])

    # History: ranked at 8, then dropped to None.
    rt.append_history("b1", [_result("dropped", 8)], [], now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    rt.append_history("b1", [_result("dropped", None)], [], now=datetime(2026, 10, 5, tzinfo=timezone.utc))

    rows = rt.worklist(_brand())

    assert len(rows) == 1
    row = rows[0]
    assert row["query"] == "dropped"
    assert row["delta_7d"] is None  # No numeric delta
    assert row["reason"] == "dropped out of the results this week; 5,000 impressions/28d; a tracked competitor is above us"
    # Score should use band=1.0 and trend=1.6.
    # demand = log1p(5000) ≈ 8.517, gap = 1.4 (tracked), band = 1.0, trend = 1.6
    # expected ≈ 8.517 * 1.0 * 1.4 * 1.6 ≈ 19.07
    assert row["score"] > 19.0


def test_worklist_already_won_absent_from_list(monkeypatch):
    """Queries we already rank for (#1) are completely absent from the worklist, not just ranked low."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("winnable", 5000), ("already won", 9000)), []))
    _seed_latest([
        {"query": "winnable", "position": 8, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "https://rival.com/a", "title": ""}]},
        {"query": "already won", "position": 1, "url": "", "error": None, "top": []},
    ])

    rows = rt.worklist(_brand())

    all_queries = [r["query"] for r in rows]
    assert "already won" not in all_queries


def test_demand_curve_is_monotonic(monkeypatch):
    """Verify that log-based demand is monotonically non-decreasing."""
    # The formula max(1.0, log1p(shown)) ensures demand(0)=1.0, demand(n) >= 1.0.
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])

    # Set up two queries: one with 1 impression, one with 5.
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("one imp", 1), ("five imp", 5)), []))
    _seed_latest([
        {"query": "one imp", "position": 10, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
        {"query": "five imp", "position": 10, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
    ])

    rows = rt.worklist(_brand())

    # Same position, same rivals → scores differ only by demand.
    scores = {r["query"]: r["score"] for r in rows}
    assert scores["five imp"] >= scores["one imp"]
