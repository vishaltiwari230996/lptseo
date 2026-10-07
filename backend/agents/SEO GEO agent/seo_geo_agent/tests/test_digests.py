"""Tests for the digest layer — the ONLY shapes LLM prompts may be built from —
and the rank analysis that consumes the rank digest."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from seo_geo_agent import digests, rank_analysis, rank_tracker, state
from seo_geo_agent.sources import CredentialMissing

BRAND = {"id": "b", "name": "Acme Legal", "domain": "x.com",
         "seeds": ["legal virtual assistant"], "competitors": ["rival.com"]}


def _row(query, position, top=None, delta=None, dropped=False, impressions=0):
    return {"query": query, "position": position, "url": f"https://x.com/{query[:4]}",
            "top": top or [], "checked_at": "2026-10-07T00:00:00", "error": None,
            "impressions": impressions, "delta_7d": delta, "dropped": dropped,
            "leader": (top or [{}])[0].get("domain"), "leader_position": (top or [{}])[0].get("position"),
            "leader_url": (top or [{}])[0].get("url")}


ROWS = [
    _row("clat coaching", 4, top=[{"position": 1, "domain": "rival.com", "url": "https://rival.com/a", "title": "t"},
                                  {"position": 2, "domain": "other.com", "url": "https://other.com/b", "title": "t"}],
         delta=-3, impressions=900),
    _row("judiciary prep", 2, top=[{"position": 1, "domain": "rival.com", "url": "https://rival.com/c", "title": "t"}],
         delta=2, impressions=500),
    _row("law entrance", None, top=[{"position": 1, "domain": "other.com", "url": "https://other.com/d", "title": "t"}],
         dropped=True),
]


def test_rank_digest_shape_and_caps(monkeypatch):
    monkeypatch.setattr(digests.rank_tracker, "annotate_rows", lambda brand: ROWS)
    state.save("rank-sweep-b", {"at": "2026-10-07T06:00:00", "checked": 3, "ranked": 2,
                                "errors": 0, "blocked": None, "notes": []})
    try:
        d = digests.rank_digest(BRAND)
    finally:
        state.delete("rank-sweep-b")
    assert d["tracked"] == 3 and d["unranked"] == 1
    assert d["top3"] == 1 and d["striking_4_20"] == 1
    assert d["dropouts_7d"] == ["law entrance"]
    assert d["movers_down"][0]["query"] == "clat coaching"
    assert d["movers_down"][0]["from"] == 1  # position 4 + delta -3
    assert d["movers_up"][0]["query"] == "judiciary prep"
    # rival.com sits above us on two queries; other.com on two as well
    pressure = {p["domain"]: p["above_us_on"] for p in d["pressure"]}
    assert pressure["rival.com"] == 2
    assert len(json.dumps(d)) < 2500  # a digest is small by definition


def test_rank_digest_none_without_rows(monkeypatch):
    monkeypatch.setattr(digests.rank_tracker, "annotate_rows", lambda brand: [])
    assert digests.rank_digest(BRAND) is None


def test_every_digest_is_none_on_empty_state():
    assert digests.deep_digest("nope") is None
    assert digests.traffic_digest("nope") is None
    assert digests.keyword_digest("nope") is None
    assert digests.review_digest("nope") is None
    assert digests.competitor_digest("nope") is None


def test_keyword_digest_caps_rows():
    state.save("kwpool-b", {"at": "2026-10-07", "totals": {"keywords": 300, "ranked": 100,
                            "opportunity": 4000, "clicks": 1000, "impressions": 90000, "thin": 50},
                            "bands": {"top3": 5, "page1": 20, "page2": 30, "beyond": 45, "unranked": 200},
                            "keywords": [{"keyword": f"k{i}", "position": 12, "impressions": 100,
                                          "opportunity": 500 - i, "cluster": None} for i in range(300)],
                            "clusters": [], "notes": []})
    try:
        d = digests.keyword_digest("b")
    finally:
        state.delete("kwpool-b")
    assert len(d["top_opportunities"]) == 8
    assert d["totals"]["keywords"] == 300
    assert len(json.dumps(d)) < 2000


# ------------------------------ rank analysis ------------------------------

def _seed_rank_data(monkeypatch):
    monkeypatch.setattr(digests.rank_tracker, "annotate_rows", lambda brand: ROWS)


def test_analyse_uses_llm_and_caches(monkeypatch):
    _seed_rank_data(monkeypatch)
    calls = {"n": 0}

    def fake_llm(system, prompt, **kw):
        calls["n"] += 1
        assert "bullets" in system  # asks for the structured shape
        assert "clat coaching" in prompt  # digest reached the prompt
        assert "rank-latest" not in prompt  # raw docs never do
        return {"bullets": ["'clat coaching' fell #1→#4 — rival.com took the spot."]}

    monkeypatch.setattr(rank_analysis.sources, "llm_json", fake_llm)
    now = datetime(2026, 10, 7, 8, tzinfo=timezone.utc)
    try:
        first = rank_analysis.analyse(BRAND, now=now)
        again = rank_analysis.analyse(BRAND, now=now)
    finally:
        state.delete("rank-analysis-b")
    assert first["llm"] is True and calls["n"] == 1
    assert again["cached"] is True and calls["n"] == 1  # same digest → no second call


def test_analyse_degrades_to_deterministic_bullets(monkeypatch):
    _seed_rank_data(monkeypatch)

    def no_llm(system, prompt, **kw):
        raise CredentialMissing("offline")

    monkeypatch.setattr(rank_analysis.sources, "llm_json", no_llm)
    try:
        doc = rank_analysis.analyse(BRAND, now=datetime(2026, 10, 7, 8, tzinfo=timezone.utc))
    finally:
        state.delete("rank-analysis-b")
    assert doc["llm"] is False
    joined = " ".join(doc["bullets"])
    assert "clat coaching" in joined       # the drop is named
    assert "law entrance" in joined        # the dropout is named


def test_analyse_without_data_is_honest(monkeypatch):
    monkeypatch.setattr(digests.rank_tracker, "annotate_rows", lambda brand: [])
    doc = rank_analysis.analyse(BRAND, now=datetime(2026, 10, 7, 8, tzinfo=timezone.utc))
    assert doc["llm"] is False and doc["bullets"]
    assert "sweep" in doc["bullets"][0].lower()
