"""Fan-out panel: sub-query gathering, coverage verdicts, cache, degradation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from seo_geo_agent import fanout, jobs, rank_tracker, state
from seo_geo_agent.sources import CredentialMissing

BRAND = {"id": "b", "name": "Acme", "domain": "x.com", "seeds": [], "competitors": []}
NOW = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)

SERP = {"organic": [], "related": ["clat coaching fees", "online clat coaching"],
        "paa": ["Which CLAT coaching is best?"], "aio_present": True}
LLM = {"queries": ["clat coaching fees", "clat coaching in jaipur", "when to start clat preparation"]}


def _seed_state():
    jobs.save_list(rank_tracker.LATEST_PREFIX.format("b"), [
        {"query": "clat coaching fees", "position": 4, "url": "https://x.com/fees",
         "top": [], "checked_at": "t", "error": None},
    ], meta={"at": "t", "ranked": 1, "errors": 0, "rivals": []})
    state.save("kwpool-b", {"keywords": [
        {"keyword": "online clat coaching", "impressions": 700, "position": 14.0, "page": "https://x.com/online"},
    ], "totals": {}, "bands": {}, "clusters": [], "notes": []})
    state.save("corpus-b", {"pages": [
        {"url": "https://x.com/jaipur", "target_query": "clat coaching jaipur",
         "topics": ["clat coaching", "jaipur centre"]},
    ]})


def _clean():
    for doc in ("kwpool-b", "corpus-b", "fanouts-b"):
        state.delete(doc)


def test_build_merges_sources_and_judges_coverage(monkeypatch):
    _seed_state()
    try:
        doc = fanout.build(BRAND, "clat coaching", search=lambda q: SERP,
                           llm=lambda s, p: LLM, now=NOW)
        rows = {r["query"].lower(): r for r in doc["subqueries"]}

        fees = rows["clat coaching fees"]
        assert set(fees["sources"]) == {"related", "ai"}   # provenance merged
        assert fees["verdict"] == "covered" and fees["position"] == 4

        online = rows["online clat coaching"]
        assert online["verdict"] == "weak"                  # impressions, no top-10
        assert online["impressions"] == 700

        jaipur = rows["clat coaching in jaipur"]
        assert jaipur["verdict"] == "weak"                  # corpus page targets it
        assert jaipur["covered_by"] == "https://x.com/jaipur"

        start = rows["when to start clat preparation"]
        assert start["verdict"] == "gap"

        assert doc["summary"] == {"covered": 1, "weak": 3, "gap": 1}
        assert doc["subqueries"][0]["verdict"] == "gap"     # gaps lead the list
        assert fanout.recent("b") == ["clat coaching"]
    finally:
        _clean()
        state.delete(f"fanout-b-{fanout._qhash('clat coaching')}")


def test_head_query_itself_is_excluded(monkeypatch):
    doc = fanout.build(BRAND, "clat coaching",
                       search=lambda q: {**SERP, "related": ["CLAT Coaching", "x"]},
                       llm=lambda s, p: {"queries": []}, now=NOW)
    try:
        assert all(r["query"].lower() != "clat coaching" for r in doc["subqueries"])
    finally:
        state.delete(f"fanout-b-{fanout._qhash('clat coaching')}")
        state.delete("fanouts-b")


def test_cache_prevents_a_second_spend(monkeypatch):
    calls = {"n": 0}

    def search(q):
        calls["n"] += 1
        return SERP

    try:
        fanout.build(BRAND, "clat syllabus", search=search, llm=lambda s, p: LLM, now=NOW)
        again = fanout.build(BRAND, "clat syllabus", search=search, llm=lambda s, p: LLM, now=NOW)
        assert again["cached"] is True and calls["n"] == 1
    finally:
        state.delete(f"fanout-b-{fanout._qhash('clat syllabus')}")
        state.delete("fanouts-b")


def test_degrades_to_serp_only_and_says_so():
    def no_llm(system, prompt):
        raise CredentialMissing("offline")

    doc = fanout.build(BRAND, "judiciary exam", search=lambda q: SERP, llm=no_llm, now=NOW)
    try:
        assert doc["subqueries"]                          # SERP evidence still there
        assert any("Model unavailable" in n for n in doc["notes"])
    finally:
        state.delete(f"fanout-b-{fanout._qhash('judiciary exam')}")
        state.delete("fanouts-b")


def test_raises_honestly_when_both_sources_are_down():
    def down(*a, **kw):
        raise CredentialMissing("offline")

    with pytest.raises(CredentialMissing):
        fanout.build(BRAND, "clat", search=down, llm=down, now=NOW)


def test_empty_serp_with_dead_model_is_a_query_problem_not_credentials():
    """A successful-but-empty SERP plus a dead model must say 'broaden the
    query', never 'set the credentials' — the credentials were fine."""
    empty = {"organic": [], "related": [], "paa": [], "aio_present": False}

    def no_llm(system, prompt):
        raise CredentialMissing("Insufficient credits")

    with pytest.raises(ValueError, match="broader head query"):
        fanout.build(BRAND, "very narrow query 2031", search=lambda q: empty, llm=no_llm, now=NOW)


def test_build_many_caps_reports_and_survives_a_bad_query():
    calls = []

    def search(q):
        calls.append(q)
        if q == "bad one":
            raise CredentialMissing("serp down for this test")
        return SERP

    def no_llm(system, prompt):
        raise CredentialMissing("off")

    queries = ["q one", "bad one", "q one", *[f"filler {i}" for i in range(12)]]
    try:
        out = fanout.build_many(BRAND, queries, search=search, llm=no_llm, now=NOW)
        by_q = {r["query"]: r for r in out["results"]}
        assert by_q["q one"]["ok"] is True
        assert by_q["bad one"]["ok"] is False and by_q["bad one"]["error"]  # named, not silent
        assert len(out["results"]) == fanout.MAX_BULK      # capped
        assert len(out["skipped"]) == 4                    # 14 unique - 10
        assert calls.count("q one") == 1                   # deduped before spend
    finally:
        for q in queries:
            state.delete(f"fanout-b-{fanout._qhash(q)}")
        state.delete("fanouts-b")


def test_remove_single_and_multiple():
    try:
        fanout.build(BRAND, "alpha", search=lambda q: SERP, llm=lambda s, p: LLM, now=NOW)
        fanout.build(BRAND, "beta", search=lambda q: SERP, llm=lambda s, p: LLM, now=NOW)
        fanout.build(BRAND, "gamma", search=lambda q: SERP, llm=lambda s, p: LLM, now=NOW)
        assert set(fanout.recent("b")) == {"alpha", "beta", "gamma"}

        remaining = fanout.remove("b", ["Beta"])           # case-insensitive single
        assert set(remaining) == {"alpha", "gamma"}
        assert fanout.latest("b", "beta") is None          # the doc went too

        remaining = fanout.remove("b", ["alpha", "gamma"])  # multiple at once
        assert remaining == []
        assert fanout.latest("b", "alpha") is None
    finally:
        for q in ("alpha", "beta", "gamma"):
            state.delete(f"fanout-b-{fanout._qhash(q)}")
        state.delete("fanouts-b")


def test_a_pasted_list_is_rejected_as_a_single_query():
    """One 'query' holding a whole pasted list must be refused before any
    spend — it burned a real SERP credit in production and polluted the
    recent chips with an 800-char blob."""
    blob = "clat coaching\nclat mock test\nclat syllabus"
    with pytest.raises(ValueError, match="looks like a list"):
        fanout.build(BRAND, blob, search=lambda q: SERP, llm=lambda s, p: LLM, now=NOW)
    long_one = "clat " * 40
    with pytest.raises(ValueError, match="looks like a list"):
        fanout.build(BRAND, long_one, search=lambda q: SERP, llm=lambda s, p: LLM, now=NOW)


def test_run_bulk_maps_everything_with_progress_and_outcome():
    class P:
        def __init__(self): self.steps = 0; self.total = 0; self.notes = []
        def phase(self, name, *, total=0): self.total = total
        def step(self, n=1): self.steps += n
        def note(self, line): self.notes.append(line)

    progress = P()
    queries = [f"topic {i}" for i in range(14)]  # past the old sync cap of 10
    try:
        out = fanout.run_bulk(BRAND, queries, progress,
                              search=lambda q: SERP, llm=lambda s, p: LLM, now=NOW)
        assert out["ok"] == 14 and out["failed"] == 0 and out["skipped"] == []
        assert progress.total == 14 and progress.steps == 14
        assert fanout.bulk_status("b")["outcome"]["ok"] == 14
        assert len(fanout.recent("b")) == 14
    finally:
        for q in queries:
            state.delete(f"fanout-b-{fanout._qhash(q)}")
        state.delete("fanouts-b")
        state.delete("fanout-bulk-b")


def test_overview_aggregates_every_mapped_topic():
    try:
        fanout.build(BRAND, "alpha", search=lambda q: SERP, llm=lambda s, p: LLM, now=NOW)
        fanout.build(BRAND, "beta",
                     search=lambda q: {**SERP, "paa": ["x?", "y?", "z?"], "related": []},
                     llm=lambda s, p: {"queries": []}, now=NOW)
        ov = fanout.overview("b")
        assert ov["topics"] == 2
        assert ov["gap"] == sum(t["gap"] for t in ov["top_gaps"])
        assert ov["top_gaps"][0]["gap"] >= ov["top_gaps"][-1]["gap"]  # ranked
    finally:
        for q in ("alpha", "beta"):
            state.delete(f"fanout-b-{fanout._qhash(q)}")
        state.delete("fanouts-b")


def test_overview_none_when_nothing_mapped():
    assert fanout.overview("nobody") is None
