"""Rank tracker — pool building, budget guard, sweep, history, worklist."""
from __future__ import annotations

from datetime import date

import pytest

from seo_geo_agent import rank_tracker as rt
from seo_geo_agent import state


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
    assert [p["p"] for p in row["raw"]] == [9, 7]
    assert row["raw"][0]["h"] < row["raw"][1]["h"]


def test_append_history_tracks_rival_positions_only_for_tracked_competitors():
    top = [{"position": 2, "domain": "rival.com", "url": "https://rival.com/a", "title": ""},
           {"position": 3, "domain": "driveby.com", "url": "https://driveby.com/b", "title": ""}]
    rt.append_history("b1", [_result("clat coaching", 9, top)], ["rival.com"], now=_at(5))

    row = rt.history_for("b1", "clat coaching")
    assert list(row["rivals"]) == ["rival.com"]
    assert row["rivals"]["rival.com"]["raw"][0]["p"] == 2


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
    triple = row["daily"][0]
    assert triple["d"] == "2026-10-01"
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
    assert rt.history_for("b1", "retired query")["raw"][0]["p"] == 12


def test_rollup_keeps_days_with_all_none_positions():
    """A day where every point was None still gets a daily entry."""
    for hour in (8, 12, 16):
        rt.append_history("b1", [_result("q", None)], [], now=_at(1, hour))
    
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    assert len(row["daily"]) == 1
    triple = row["daily"][0]
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
    triple = row["daily"][0]
    assert triple["best"] == 3
    assert triple["worst"] == 5
    assert triple["last"] is None


def test_rollup_reroll_with_earlier_stale_point_widens_but_preserves_last():
    """When re-rolling a day, an earlier point widens best/worst but doesn't change last or at."""
    rt.append_history("b1", [_result("q", 5)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    original_at = row["daily"][0]["at"]
    original_last = row["daily"][0]["last"]
    
    # Now discover an earlier point that wasn't included before (simulating backfill)
    rt.append_history("b1", [_result("q", 9)], [], now=_at(1, 8))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    triple = row["daily"][0]
    assert triple["best"] == 5
    assert triple["worst"] == 9  # widened
    assert triple["last"] == original_last  # unchanged
    assert triple["at"] == original_at  # unchanged


def test_rollup_reroll_with_later_stale_point_updates_last_and_at():
    """When re-rolling a day, a later point does replace last and at."""
    rt.append_history("b1", [_result("q", 5)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    original_at = row["daily"][0]["at"]
    
    # Now discover a later point for the same day
    rt.append_history("b1", [_result("q", 3)], [], now=_at(1, 16))
    rt.rollup("b1", today=date(2026, 10, 20))
    
    row = rt.history_for("b1", "q")
    triple = row["daily"][0]
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
    triple = row["daily"][0]
    assert triple["best"] == 3
    assert triple["worst"] == 9
    assert triple["last"] == 3  # hour 16 is chronologically last
    assert triple["at"] == rt._epoch_hours(_at(1, 16))  # at hour 16


def test_rollup_reroll_with_equal_at_replaces_last_and_at():
    """A re-roll whose new final point has exactly the stored at should replace last and at."""
    rt.append_history("b1", [_result("q", 5)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    original_at = row["daily"][0]["at"]

    # Append new point at exactly the same hour (simulating backfill with same timestamp)
    rt.append_history("b1", [_result("q", 7)], [], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    triple = row["daily"][0]
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
    rival_triple = row["rivals"]["rival.com"]["daily"][0]
    assert rival_triple["best"] == 8
    assert rival_triple["worst"] == 12
    assert rival_triple["last"] == 8  # hour 16 is chronologically last


def test_rollup_rival_reroll_with_equal_at_replaces_last():
    """Rival series re-roll with equal at should replace last and at."""
    top = [{"position": 5, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, top)], ["rival.com"], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    original_at = row["rivals"]["rival.com"]["daily"][0]["at"]

    # Append new rival point at exactly the same hour
    top = [{"position": 3, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, top)], ["rival.com"], now=_at(1, 12))
    rt.rollup("b1", today=date(2026, 10, 20))

    row = rt.history_for("b1", "q")
    rival_triple = row["rivals"]["rival.com"]["daily"][0]
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
    data, and must not be written into history as a site-wide collapse.

    Two queries, one of which answers, so this stays a PARTIAL sweep — an
    all-errored one is a total outage and deliberately writes no rows at all
    (see ``test_a_total_outage_does_not_overwrite_the_previous_results``).
    """
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q", "ok"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, **kw):
        return _serp() if query == "q" else _serp((1, "https://lawpreptutorial.com/a"))

    out = rt.sweep(_brand(), search=search)

    assert out["errors"] == 1
    broken = next(r for r in rt.latest_rows("b1") if r["query"] == "q")
    assert broken["error"] == "empty SERP"
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


def test_sweep_charges_one_credit_per_query_when_found_on_page_1(monkeypatch):
    """The cheap case: a query already ranking on page 1 needs no second
    fetch, so three queries cost exactly three credits, not six."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["a", "b", "c"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    rt.sweep(_brand(), search=lambda q, **kw: _serp((1, "https://lawpreptutorial.com/")))

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
    rt.sweep(_brand(), search=lambda q, **kw: seen.append(q) or _serp((1, "https://lawpreptutorial.com/")))

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

    def fake_provider(query, client=None, *, gl="in", hl="en", num=10, page=1):
        seen_gl.append(gl)
        return _serp((1, "https://lawpreptutorial.com/"))  # found on page 1 — no page-2 call

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


# --- Adaptive page-2 fetch --------------------------------------------------
#
# Serper's page 1 caps at 10 organic results no matter what `num` asks for
# (measured against the live API), so a query at rank 11 looked identical to
# one at rank 95 — both `position: None`. Page 2 costs one more credit and
# returns the next 10, with `position` restarting at 1. The sweep now spends
# that second credit only when page 1 did not already find us.

def _paged_serp(page1, page2=()):
    """A `search(query, page=1)` double returning different organic entries
    per page — `page1`/`page2` are each a list of (position, url) pairs, like
    `_serp`'s own varargs."""
    def search(query, page=1, **kw):
        return _serp(*(page1 if page == 1 else page2))
    return search


def test_sweep_found_on_page_1_charges_one_credit_and_never_requests_page_2(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    pages_requested = []

    def search(query, page=1, **kw):
        pages_requested.append(page)
        return _serp((5, "https://lawpreptutorial.com/x"))

    out = rt.sweep(_brand(), search=search)

    assert pages_requested == [1]
    assert out["errors"] == 0
    row = rt.latest_rows("b1")[0]
    assert row["position"] == 5
    assert row["error"] is None
    assert rt.budget_status("b1")["searches"] == 1


def test_sweep_found_on_page_2_records_an_absolute_rank_and_a_full_twenty_top(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    page1 = [(p, f"https://rival{p}.com/") for p in range(1, 11)]
    page2 = [(p, f"https://rival{p}.com/p2") for p in range(1, 11)]
    page2[2] = (3, "https://lawpreptutorial.com/clat")  # page-relative #3 -> absolute 13

    out = rt.sweep(_brand(), search=_paged_serp(page1, page2))

    assert out["errors"] == 0
    row = rt.latest_rows("b1")[0]
    assert row["position"] == 13
    assert row["error"] is None
    assert len(row["top"]) == 20
    assert [e["position"] for e in row["top"]] == list(range(1, 21))
    assert rt.budget_status("b1")["searches"] == 2


def test_sweep_absent_from_both_pages_is_unranked_not_an_error(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    page1 = [(p, f"https://rival{p}.com/") for p in range(1, 11)]
    page2 = [(p, f"https://rival{p}.com/p2") for p in range(1, 11)]

    out = rt.sweep(_brand(), search=_paged_serp(page1, page2))

    assert out["errors"] == 0
    row = rt.latest_rows("b1")[0]
    assert row["position"] is None
    assert row["error"] is None
    assert len(row["top"]) == 20
    assert [e["position"] for e in row["top"]] == list(range(1, 21))
    assert rt.budget_status("b1")["searches"] == 2


def test_sweep_page_2_raising_keeps_page_1_and_notes_the_query_without_erroring(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching", "second one"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, page=1, **kw):
        if page == 1:
            return _serp((1, "https://rival.com/a"))
        raise RuntimeError("serper 500")

    out = rt.sweep(_brand(), search=search)

    assert out["errors"] == 0  # page 1 succeeded for both; page 2 failing is not a row error
    assert out["checked"] == 2 and out["ranked"] == 2
    for row in rt.latest_rows("b1"):
        assert row["error"] is None
        assert row["position"] is None
        assert len(row["top"]) == 1  # page 1's result only — page 2 never got appended
    assert any("clat coaching" in n for n in out["notes"])
    assert any("second one" in n for n in out["notes"])


def test_sweep_page_2_empty_organic_keeps_page_1_and_notes_it(monkeypatch):
    """Page 2 answering with no organic results (no exception) degrades the
    same way as a raise: page 1's result is kept, a note is added, and the
    row is not an error."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    out = rt.sweep(_brand(), search=_paged_serp([(1, "https://rival.com/a")], []))

    assert out["errors"] == 0
    row = rt.latest_rows("b1")[0]
    assert row["error"] is None
    assert row["position"] is None
    assert len(row["top"]) == 1
    assert any("clat coaching" in n for n in out["notes"])


def test_sweep_page_2_budget_refusal_keeps_page_1_result_and_continues(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["clat coaching"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))
    # Leave exactly one credit: enough for page 1's charge, not page 2's.
    rt.charge("b1", rt.MAX_SEARCHES_PER_DAY - 1, today=date(2026, 10, 5))

    page2_calls = []

    def search(query, page=1, **kw):
        if page == 2:
            page2_calls.append(query)
        return _serp((1, "https://rival.com/a"))  # never matches our domain

    out = rt.sweep(_brand(), search=search, now=_at(5, 9))

    assert page2_calls == []  # budget refused the charge before page 2 was ever requested
    assert out["blocked"] is None  # one query's page 2 being skipped does not block the sweep
    assert out["errors"] == 0
    row = rt.latest_rows("b1")[0]
    assert row["position"] is None
    assert len(row["top"]) == 1
    assert any("clat coaching" in n for n in out["notes"])


def test_sweep_empty_page_1_is_still_an_error_and_never_requests_page_2(monkeypatch):
    """Two queries, one erroring — so this stays a PARTIAL sweep. An
    all-errored sweep is a total outage and writes no rows at all (see
    `test_a_total_outage_does_not_overwrite_the_previous_results`), which
    would make this test about something else entirely."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q", "ok"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    pages_requested = []

    def search(query, page=1, **kw):
        pages_requested.append((query, page))
        if query == "q":
            return _serp()  # empty organic -> "empty SERP"
        return _serp((1, "https://lawpreptutorial.com/a"))

    out = rt.sweep(_brand(), search=search)

    # an empty page 1 is a provider failure, not "rank unknown" — page 2 is
    # never requested for it
    assert pages_requested == [("q", 1), ("ok", 1)]
    assert out["errors"] == 1
    row = next(r for r in rt.latest_rows("b1") if r["query"] == "q")
    assert row["error"] == "empty SERP"
    assert rt.budget_status("b1")["searches"] == 2


def test_sweep_harvest_comes_from_page_1_only(monkeypatch):
    """Page 2 repeats page 1's related/PAA blocks; harvesting it too would
    double-count the frequencies that drive pool promotion."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def search(query, page=1, **kw):
        serp = _serp((1, "https://rival.com/a"))  # never matches -> page 2 gets requested
        serp["related"] = [f"page {page} related"]
        serp["paa"] = [f"page {page} paa"]
        return serp

    rt.sweep(_brand(), search=search)

    harvested = set(rt._harvest_ranked("b1"))
    assert harvested == {"page 1 related", "page 1 paa"}


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


def test_worklist_dropped_flag_marks_dropout_rows_only():
    """The worklist row's `dropped` field badges a dropout distinctly from an
    ordinary losing row — the panel should not have to pattern-match `reason`."""
    _seed_latest([
        {"query": "slipping", "position": 12, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
        {"query": "dropped", "position": None, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
    ])
    rt.append_history("b1", [_result("slipping", 4), _result("dropped", 8)], [],
                      now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    rt.append_history("b1", [_result("slipping", 12), _result("dropped", None)], [],
                      now=datetime(2026, 10, 5, tzinfo=timezone.utc))

    rows = rt.worklist(_brand())
    by_query = {r["query"]: r for r in rows}

    assert by_query["dropped"]["dropped"] is True
    assert by_query["slipping"]["dropped"] is False


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


def test_worklist_loads_history_once_not_per_row(monkeypatch):
    """Verify that worklist() calls jobs.load_list a constant number of times,
    not once per row. This prevents a Firestore read storm on every panel GET."""
    from seo_geo_agent import competitors, jobs as j
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])

    # Seed 20 worklist-eligible queries (all with rivals above them).
    queries = [f"q{n}" for n in range(20)]
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(*[(q, 100) for q in queries]), []))
    _seed_latest([
        {"query": q, "position": 10, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]}
        for q in queries
    ])

    # Create some history for half the queries.
    for q in queries[:10]:
        rt.append_history("b1", [_result(q, 8)], [], now=_at(1))
        rt.append_history("b1", [_result(q, 10)], [], now=_at(5))

    # Monkeypatch jobs.load_list to count calls.
    call_count = [0]
    original_load_list = j.load_list
    def counting_load_list(*args, **kwargs):
        call_count[0] += 1
        return original_load_list(*args, **kwargs)
    monkeypatch.setattr(j, "load_list", counting_load_list)

    # Call worklist() — should load history once, not 20 times.
    rows = rt.worklist(_brand())

    # Expected calls: 1 for all_history, 1 for latest_rows = 2 constant calls
    # (plus any pool reads, but those are unrelated to row count).
    # We're specifically checking that lost_ranking and delta don't each
    # trigger fresh history loads.
    assert call_count[0] <= 3, f"Expected ≤3 load_list calls, got {call_count[0]}. History was loaded once per row."
    assert len(rows) > 0  # Sanity check: worklist produced output


# === annotate_rows() — Review Finding 3: every row, not just the worklist's
#     top 10. worklist() used to compute impressions/delta_7d/leader* for
#     every swept row and then discard all but the top `limit` with a slice.
#     That work is now kept: annotate_rows() returns it for every row, and
#     worklist() is a filter + sort + slice on top of it. ===

def test_annotate_rows_includes_a_query_we_already_lead(monkeypatch):
    """A query we rank #1 for has nobody above us — worklist() skips it with
    `continue`, but the full table still needs this row, with leader* null
    rather than a `top[0]` guess (there is no top[0] to guess from anyway)."""
    _seed_latest([
        {"query": "already won", "position": 1, "url": "", "error": None, "top": []},
    ])

    rows = rt.annotate_rows(_brand())

    assert len(rows) == 1
    row = rows[0]
    assert row["query"] == "already won"
    assert row["position"] == 1
    assert row["leader"] is None
    assert row["leader_position"] is None
    assert row["leader_url"] is None


def test_annotate_rows_every_row_carries_delta_and_impressions(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("led", 100), ("leads", 50)), []))
    _seed_latest([
        {"query": "led", "position": 5, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
        {"query": "leads", "position": 1, "url": "", "error": None, "top": []},
    ])

    rows = rt.annotate_rows(_brand())

    assert len(rows) == 2
    for row in rows:
        assert "delta_7d" in row
        assert "impressions" in row
        assert isinstance(row["impressions"], int)


def test_annotate_rows_excludes_errored_rows():
    _seed_latest([{"query": "broken", "position": None, "url": "",
                   "error": "serper 429", "top": []}])
    assert rt.annotate_rows(_brand()) == []


def test_worklist_output_unchanged_when_fed_precomputed_rows(monkeypatch):
    """`worklist(brand, rows=annotate_rows(brand))` — the shape `_rank_payload`
    uses to avoid loading history twice — must produce the exact same result
    as the default `worklist(brand)` call every existing test exercises."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("winnable", 5000), ("already won", 9000)), []))
    _seed_latest([
        {"query": "winnable", "position": 8, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "https://rival.com/a", "title": ""}]},
        {"query": "already won", "position": 1, "url": "", "error": None, "top": []},
    ])

    direct = rt.worklist(_brand())
    annotated = rt.annotate_rows(_brand())
    via_rows = rt.worklist(_brand(), rows=annotated)

    assert via_rows == direct
    assert [r["query"] for r in via_rows] == ["winnable"]


def test_rank_payload_shaped_call_loads_history_only_once(monkeypatch):
    """Pins the load-once invariant across the exact shape `_rank_payload` now
    uses: one `annotate_rows()` call, whose result is handed to `worklist()`
    rather than letting `worklist()` redo its own `annotate_rows()` (and the
    history read inside it) a second time for the same request."""
    from seo_geo_agent import competitors, jobs as j
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("q", 100)), []))
    _seed_latest([
        {"query": "q", "position": 9, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
    ])

    call_count = [0]
    original_load_list = j.load_list
    def counting_load_list(*args, **kwargs):
        call_count[0] += 1
        return original_load_list(*args, **kwargs)
    monkeypatch.setattr(j, "load_list", counting_load_list)

    brand = _brand()
    annotated = rt.annotate_rows(brand)          # 2 load_list calls (all_history, latest_rows)
    worklist_rows = rt.worklist(brand, rows=annotated)  # must add 0 more

    assert call_count[0] <= 3, (
        f"Expected worklist(rows=...) to add no further load_list calls, got {call_count[0]} total."
    )
    assert len(worklist_rows) == 1


# === Final whole-branch review fixes ======================================== #
# C1 (Critical): Firestore rejects an array whose elements are arrays, so the
# `[[hour, position], …]` encoding could never be written — every sweep paid
# its full Serper bill and then died before storing any history. The shape is
# pinned here offline; `test_rank_tracker_firestore.py` proves it against a
# real emulator, which is the check that was missing.

def test_history_points_are_maps_never_nested_arrays():
    """Firestore: `InvalidArgument 400 Property array contains an invalid
    nested entity`. No list in a history row may hold another list."""
    top = [{"position": 2, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, top)], ["rival.com"], now=_at(1, 9))
    rt.rollup("b1", today=date(2026, 10, 20))
    rt.append_history("b1", [_result("q", 7, top)], ["rival.com"], now=_at(20, 9))

    row = rt.history_for("b1", "q")

    def assert_no_nested_arrays(value, path="history"):
        if isinstance(value, list):
            for n, item in enumerate(value):
                assert not isinstance(item, (list, tuple)), \
                    f"{path}[{n}] is an array inside an array — Firestore rejects this"
                assert_no_nested_arrays(item, f"{path}[{n}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                assert_no_nested_arrays(item, f"{path}.{key}")

    assert_no_nested_arrays(row)
    assert row["raw"] == [{"h": rt._epoch_hours(_at(20, 9)), "p": 7}]
    assert row["daily"] == [{"d": "2026-10-01", "best": 9, "worst": 9, "last": 9,
                             "at": rt._epoch_hours(_at(1, 9))}]
    assert row["rivals"]["rival.com"]["daily"][0]["d"] == "2026-10-01"


# M1: one null point per tracked rival per query per sweep was ~90% of a
# measured 22.4 MB of history, storing only the fact that someone was absent.

def test_a_rival_absent_from_the_serp_gets_no_point_at_all():
    present = [{"position": 2, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, present)], ["rival.com", "ghost.com"], now=_at(1))
    rt.append_history("b1", [_result("q", 9, [])], ["rival.com", "ghost.com"], now=_at(2))

    row = rt.history_for("b1", "q")

    # A rival that never showed up has no series at all...
    assert "ghost.com" not in row["rivals"]
    # ...and one that showed up once has exactly one point, not two.
    assert row["rivals"]["rival.com"]["raw"] == [{"h": rt._epoch_hours(_at(1)), "p": 2}]


# M2: a competitor stored as a pasted URL or with a "www." prefix matched no
# SERP domain, so its series stayed empty and the tracked_rival scoring boost
# never fired — with nothing anywhere reporting a problem.

def test_a_rival_stored_as_a_url_still_matches_the_serp_domain():
    top = [{"position": 2, "domain": "rival.com", "url": "", "title": ""}]
    rt.append_history("b1", [_result("q", 9, top)], ["https://www.Rival.com/pricing?x=1"],
                      now=_at(1))

    row = rt.history_for("b1", "q")
    assert list(row["rivals"]) == ["rival.com"]
    assert row["rivals"]["rival.com"]["raw"][0]["p"] == 2


def test_tracked_rivals_normalises_dedups_and_caps():
    brand = _brand(competitors=["https://www.Rival.com/x", "rival.com", "RIVAL.com"]
                   + [f"c{n}.com" for n in range(10)])
    rivals = rt.tracked_rivals(brand)
    assert rivals[0] == "rival.com"
    assert len(rivals) == rt.MAX_RIVALS      # M7: same cap the sweep applies
    assert len(set(rivals)) == len(rivals)   # deduped after normalisation


def test_worklist_scores_a_tracked_rival_stored_as_a_url(monkeypatch):
    """M7/M2 together: `worklist` read `competitors` uncapped and bare-
    lowercased, so a rival the sweep had normalised away scored no boost."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("winnable", 100)), []))
    _seed_latest([
        {"query": "winnable", "position": 8, "url": "", "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "", "title": ""}]},
    ])

    rows = rt.worklist(_brand(competitors=["https://www.Rival.com/"]))

    assert rows[0]["tracked_rival"] is True


def test_worklist_ignores_competitors_past_the_cap(monkeypatch):
    """The sweep records history for the first MAX_RIVALS only; the worklist
    must not award a tracked-rival boost to a ninth it knows nothing about."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: [])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: (_rows(("winnable", 100)), []))
    _seed_latest([
        {"query": "winnable", "position": 8, "url": "", "error": None,
         "top": [{"position": 1, "domain": "ninth.com", "url": "", "title": ""}]},
    ])

    over_cap = [f"c{n}.com" for n in range(rt.MAX_RIVALS)] + ["ninth.com"]
    rows = rt.worklist(_brand(competitors=over_cap))

    assert rows[0]["tracked_rival"] is False


# I1: an all-errored sweep used to replace a real scoreboard with 200 unusable
# rows; annotate_rows then skipped every one, so the panel said "Nothing
# urgent…" over a total Serper outage.

def test_a_total_outage_does_not_overwrite_the_previous_results(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q", "r"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    good = rt.sweep(_brand(), search=lambda query, **kw: _serp((3, "https://lawpreptutorial.com/x")),
                    now=_at(5, 9))
    assert good["ranked"] == 2

    def every_query_fails(query, **kw):
        raise RuntimeError("serper 429")

    out = rt.sweep(_brand(), search=every_query_fails, now=_at(5, 11))

    assert out["errors"] == 2 and out["ranked"] == 0
    assert any("keeping the previous results" in n for n in out["notes"])
    # The good scoreboard survives, rather than being replaced by two errors.
    assert [r["position"] for r in rt.latest_rows("b1")] == [3, 3]
    assert rt.latest_meta("b1")["errors"] == 0


def test_a_partial_outage_still_writes_its_good_rows(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q", "r"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def half(query, **kw):
        if query == "q":
            raise RuntimeError("serper 429")
        return _serp((3, "https://lawpreptutorial.com/x"))

    out = rt.sweep(_brand(), search=half, now=_at(5, 9))

    assert out["errors"] == 1 and out["ranked"] == 1
    assert rt.latest_meta("b1")["errors"] == 1
    assert sorted(r["query"] for r in rt.latest_rows("b1")) == ["q", "r"]


# I4: a disabled or key-less sweep wrote nothing and `jobs.start` then marked
# the job done, so "Run now" stayed enabled and the panel said nothing at all.

def test_a_disabled_sweep_records_why_it_did_nothing(monkeypatch):
    out = rt.sweep(_brand(rank_tracking_enabled=False), search=lambda q, **kw: _serp())

    assert out["blocked"] == "disabled"
    recorded = rt.last_sweep("b1")
    assert recorded["blocked"] == "disabled"
    assert "switched off" in recorded["notes"][0]


def test_a_sweep_without_credentials_records_why_it_did_nothing(monkeypatch):
    from seo_geo_agent import sources as src
    monkeypatch.setattr(src, "brand_rank_available", lambda: False)

    out = rt.sweep(_brand())

    assert out["blocked"] == "credentials"
    assert rt.last_sweep("b1")["blocked"] == "credentials"
    assert "SEO_SERPER_API_KEY" in rt.last_sweep("b1")["notes"][0]


def test_a_successful_sweep_records_a_clean_outcome(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    rt.sweep(_brand(), search=lambda q, **kw: _serp((3, "https://lawpreptutorial.com/x")),
             now=_at(5, 9))

    recorded = rt.last_sweep("b1")
    assert recorded["blocked"] is None and recorded["errors"] == 0 and recorded["notes"] == []


# I5: `jobs.start` refuses a second MANUAL run in ONE process. The cron calls
# sweep() inline without it, and Cloud Run holds several processes.

def test_a_second_sweep_is_refused_while_one_holds_the_lease(monkeypatch):
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    with state.lease(rt.SWEEP_LOCK_DOC.format("b1"), ttl=60, wait=0):
        out = rt.sweep(_brand(), search=lambda q, **kw: _serp((3, "https://x.com/a")),
                       now=_at(5, 9))

    assert out["blocked"] == "running"
    assert out["checked"] == 0
    # Nothing was charged: the whole point of refusing rather than queuing.
    assert rt.budget_status("b1", today=date(2026, 10, 5))["searches"] == 0
    assert rt.last_sweep("b1")["blocked"] == "running"


def test_an_expired_sweep_lease_does_not_wedge_the_schedule(monkeypatch):
    """A Cloud Run instance killed mid-sweep leaves its lease behind. It must
    expire, or every later sweep is refused forever."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))
    state.save(rt.SWEEP_LOCK_DOC.format("b1"), {"token": "dead-instance", "until": 1})

    out = rt.sweep(_brand(), search=lambda q, **kw: _serp((3, "https://lawpreptutorial.com/a")),
                   now=_at(5, 9))

    assert out["blocked"] is None and out["ranked"] == 1


def test_overlapping_append_history_calls_do_not_lose_a_point(monkeypatch):
    """The lost update the reviewer forced by hand. Two writers read the same
    chunk set, each merge their own query in, and the second save used to
    overwrite the first — one query's whole history silently gone.

    `jobs.save_list` is slowed so the two writers genuinely overlap; the lease
    is what makes them take turns instead.
    """
    import threading
    import time as _time
    from seo_geo_agent import jobs as j

    real_save = j.save_list

    def slow_save(*args, **kwargs):
        _time.sleep(0.15)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(j, "save_list", slow_save)

    start = threading.Barrier(2)

    def writer(query: str):
        start.wait()
        rt.append_history("b1", [_result(query, 5)], [], now=_at(5, 9))

    threads = [threading.Thread(target=writer, args=(q,)) for q in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert rt.history_for("b1", "alpha") is not None, "alpha's history was lost"
    assert rt.history_for("b1", "beta") is not None, "beta's history was lost"


# README C2-adjacent: 200 TLS handshakes per sweep, one per query.

def test_the_sweep_shares_one_http_client_across_every_query(monkeypatch):
    from seo_geo_agent import competitors
    from seo_geo_agent import sources as src
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q", "r", "s"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))
    monkeypatch.setattr(src, "brand_rank_available", lambda: True)

    seen: list[object] = []

    def fake_search(query, client=None, **kw):
        seen.append(client)
        return _serp((3, "https://lawpreptutorial.com/x"))

    monkeypatch.setattr(src, "brand_rank_search", fake_search)

    rt.sweep(_brand(), now=_at(5, 9))

    assert len(seen) == 3
    assert seen[0] is not None, "the sweep must pass a client, not let each call open its own"
    assert all(c is seen[0] for c in seen), "every query must reuse the one connection pool"


def test_a_history_lease_timeout_is_not_reported_as_another_sweep(monkeypatch):
    """`blocked: "running"` says "nothing was charged". A lease taken deeper
    in — by append_history, after 200 searches are already paid for — must
    not be reported with that sentence."""
    from seo_geo_agent import competitors
    monkeypatch.setattr(competitors, "list_custom_queries", lambda bid: ["q"])
    monkeypatch.setattr(competitors, "tracked_keywords", lambda b: [])
    rt.build_pool(_brand(), rows_fn=lambda b: ([], []))

    def busy_history(*a, **kw):
        raise state.Busy("history lease held")

    monkeypatch.setattr(rt, "append_history", busy_history)

    with pytest.raises(state.Busy):
        rt.sweep(_brand(), search=lambda q, **kw: _serp((3, "https://lawpreptutorial.com/a")),
                 now=_at(5, 9))

    # The search WAS charged, so nothing may claim otherwise.
    assert rt.budget_status("b1", today=date(2026, 10, 5))["searches"] == 1
