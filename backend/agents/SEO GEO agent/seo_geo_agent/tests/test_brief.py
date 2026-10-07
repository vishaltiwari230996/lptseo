"""Tests for the daily brief — five deterministic blocks from the digests."""
from __future__ import annotations

from seo_geo_agent import brief, digests, state

BRAND = {"id": "b", "name": "Acme Legal", "domain": "x.com",
         "seeds": [], "competitors": ["rival.com"]}

ROWS = [
    {"query": "clat coaching", "position": 4, "url": "https://x.com/clat", "checked_at": "t",
     "top": [{"position": 1, "domain": "rival.com", "url": "u", "title": "t"}], "error": None,
     "impressions": 900, "delta_7d": -3, "dropped": False,
     "leader": "rival.com", "leader_position": 1, "leader_url": "u"},
    {"query": "judiciary prep", "position": 2, "url": "https://x.com/jud", "checked_at": "t",
     "top": [], "error": None, "impressions": 500, "delta_7d": 2, "dropped": False,
     "leader": None, "leader_position": None, "leader_url": None},
    {"query": "law entrance", "position": None, "url": "", "checked_at": "t",
     "top": [], "error": None, "impressions": 0, "delta_7d": None, "dropped": True,
     "leader": None, "leader_position": None, "leader_url": None},
]

DEEP = {"at": "2026-10-01", "domain": "x.com", "urls": 100, "sitemaps": 2,
        "pages_by_type": {}, "live_pages": 98, "gsc_connected": True, "notes": [],
        "sitemap": {"score": 45, "affected": 12, "issues": 4},
        "landing": {"pages": 40, "avg_score": 61, "high": 9, "template_issues": 1,
                    "grades": {"A": 15, "B": 10, "C": 10, "D": 3, "F": 2}},
        "cannibalization": {"pairs": 3, "high": 1, "medium": 2, "confirmed": 2},
        "density": {"posts": 20, "pass": 16, "average_only": 2, "fail": 2, "median_wpm": 150}}

RUN = {"at": "2026-10-07", "degraded": [],
       "summary": {"mode": "search-console", "clicks_28d": 800, "clicks_prev_28d": 1000,
                   "impressions_28d": 50000, "avg_position": 9.1, "est_potential_clicks": 300},
       "todos": [
           {"id": "t1", "kind": "ctr_gap", "action": "Rewrite the title for 'clat 2027'", "why": "w",
            "est_monthly_clicks": 90, "position": 3.0, "status": "todo"},
           {"id": "t2", "kind": "striking", "action": "Refresh the CLAT syllabus page", "why": "w",
            "est_monthly_clicks": 40, "position": 7.0, "status": "todo"},
           {"id": "t3", "kind": "striking", "action": "Already handled", "why": "w",
            "est_monthly_clicks": 10, "position": 9.0, "status": "done"},
       ],
       "topics": [{"keyword": "clat cutoffs 2027", "priority": "high", "trend": "rising",
                   "difficulty": "easy win", "impact": "i"}]}


def _seed(monkeypatch):
    monkeypatch.setattr(digests.rank_tracker, "annotate_rows", lambda brand: ROWS)
    state.save("deepaudit-b", DEEP)
    state.save("run-b", RUN)


def _clean():
    state.delete("deepaudit-b")
    state.delete("run-b")


def test_brief_builds_all_five_blocks(monkeypatch):
    _seed(monkeypatch)
    try:
        doc = brief.build(BRAND)
    finally:
        _clean()
    assert doc["current_rank"]["tracked"] == 3
    assert doc["current_rank"]["dropouts"] == 1

    not_working = " ".join(l["text"] for l in doc["not_working"])
    assert "law entrance" in not_working          # dropout named
    assert "clat coaching" in not_working         # fall named, with the leader
    assert "rival.com" in not_working
    assert "Sitemap health is 45/100" in not_working
    assert "9 high-severity" in not_working
    assert "800" in not_working                   # clicks decline

    working = " ".join(l["text"] for l in doc["working"])
    assert "judiciary prep" in working            # climb named
    assert "16 of 20 blog articles" in working

    immediate = " ".join(l["text"] for l in doc["immediate"])
    assert "Rewrite the title for 'clat 2027'" in immediate
    assert "+90" in immediate                     # the estimated gain rides along

    secondary = " ".join(l["text"] for l in doc["secondary"])
    assert "Refresh the CLAT syllabus page" in secondary
    assert "Already handled" not in secondary     # done todos stay out
    assert "clat cutoffs 2027" in secondary       # high-priority topic

    # Every line links to a real console section.
    valid = {"rank-board", "traffic", "keywords", "vitals", "landing",
             "sitemap", "cannibal", "density", "overview", "rank-tracker"}
    for block in ("working", "not_working", "immediate", "secondary"):
        for line in doc[block]:
            assert line["link"] in valid, line


def test_brief_is_honest_about_missing_sources(monkeypatch):
    monkeypatch.setattr(digests.rank_tracker, "annotate_rows", lambda brand: [])
    doc = brief.build(BRAND)
    assert doc["current_rank"] is None
    assert any("sweep" in n.lower() for n in doc["notes"])
    assert any("deep audit" in n.lower() for n in doc["notes"])
