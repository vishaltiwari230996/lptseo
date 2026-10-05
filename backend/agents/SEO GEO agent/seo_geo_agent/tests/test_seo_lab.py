"""Tests for the researcher layer: keyword lab, competitors, briefs, audit, advisor."""
import pytest

from seo_geo_agent import (advisor, audit, briefs, competitors, gsc_oauth, insights, keywords,
                           site_brain, topics)
from seo_geo_agent.sources import CredentialMissing, PageFacts, QueryStat


def row(query, page="https://x.com/a", clicks=10, impressions=500, position=8.0):
    return QueryStat(query=query, page=page, clicks=clicks, impressions=impressions,
                     ctr=clicks / impressions, position=position)


def facts(url, status=200, title="Title", meta="desc", h1=None, h2=None, h3=None,
          schema=None, words=1500, no_alt=0):
    f = PageFacts(url=url, status=status, title=title, meta_description=meta, word_count=words)
    f.h1 = h1 if h1 is not None else ["Main heading"]
    f.h2 = h2 or []
    f.h3 = h3 or []
    f.schema_types = schema or []
    f.images_no_alt = no_alt
    return f


BRAND = {"id": "b", "name": "Acme Legal", "domain": "x.com",
         "seeds": ["legal virtual assistant"], "competitors": ["comp.com"], "enabled": True}


# ------------------------------- keyword lab -------------------------------

def test_intent_heuristic():
    assert keywords.intent_of("hire a legal virtual assistant") == "transactional"
    assert keywords.intent_of("best clio alternatives") == "commercial"
    assert keywords.intent_of("how to answer legal calls") == "informational"
    assert keywords.intent_of("paralegal services near me") == "local"


def test_expand_offline_uses_seeds_and_gsc():
    kws, notes = keywords.expand_keywords(BRAND, [row("virtual receptionist for lawyers")])
    assert "legal virtual assistant" in kws
    assert "virtual receptionist for lawyers" in kws
    assert any("Serper" in n for n in notes) and any("LLM" in n for n in notes)


def test_heuristic_clusters_group_by_shared_tokens():
    clusters = keywords._heuristic_clusters([
        "legal virtual assistant", "legal virtual assistant cost", "law firm marketing ideas",
    ])
    sizes = sorted(len(c["keywords"]) for c in clusters)
    assert sizes == [1, 2]


def test_cluster_scoring_gap_vs_ranking():
    clusters = [
        {"name": "legal virtual assistant", "intent": "commercial",
         "keywords": ["legal virtual assistant"]},
        {"name": "law firm marketing", "intent": "informational",
         "keywords": ["law firm marketing"]},
    ]
    rows = [row("legal virtual assistant", impressions=1000, position=4.0)]
    keywords.score_clusters(clusters, rows)
    ranked = {c["name"]: c for c in clusters}
    assert ranked["legal virtual assistant"]["coverage"] == "ranking"
    assert ranked["law firm marketing"]["coverage"] == "gap"
    # Act-now tiers sort above already-ranking "watch" clusters, whatever the volume.
    assert clusters[0]["name"] == "law firm marketing"
    assert ranked["legal virtual assistant"]["tier"] == "watch"


def test_cluster_scoring_uses_rank_snapshot_when_no_gsc():
    clusters = [{"name": "legal virtual assistant", "intent": "commercial",
                 "keywords": ["legal virtual assistant"]}]
    keywords.score_clusters(clusters, [], ranks={"legal virtual assistant": 12.0})
    assert clusters[0]["coverage"] == "weak"
    assert clusters[0]["best_position"] == 12


def test_tiers_and_recommendations():
    clusters = [
        {"name": "hire legal virtual assistant", "intent": "transactional",
         "keywords": ["hire legal virtual assistant"]},
        {"name": "what is legal intake", "intent": "informational",
         "keywords": ["what is legal intake"]},
        {"name": "legal answering service", "intent": "commercial",
         "keywords": ["legal answering service"]},
    ]
    rows = [row("legal answering service", impressions=500, position=2.0)]
    keywords.score_clusters(clusters, rows)
    by_name = {c["name"]: c for c in clusters}
    assert by_name["hire legal virtual assistant"]["tier"] == "high"      # buyer + gap
    assert by_name["what is legal intake"]["tier"] == "medium"            # info + gap
    assert by_name["legal answering service"]["tier"] == "watch"          # already ranking
    assert "create a dedicated page" in by_name["hire legal virtual assistant"]["recommendation"]
    assert clusters[0]["tier"] == "high"  # high tier sorts first


def test_cluster_owners_recorded():
    clusters = [{"name": "kw one", "intent": "transactional", "keywords": ["kw one"],
                 "tier": "high", "coverage": "gap", "opportunity": 10}]
    keywords.add_cluster_owners(clusters, "x.com", search=lambda q: serp_with(2))
    assert clusters[0]["owned_by"] == ["comp.com", "reddit.com"]  # own domain excluded
    assert clusters[0]["aio_present"] is False


def test_topics_include_new_ideas_with_priority(monkeypatch):
    monkeypatch.setattr(topics.sources, "llm_json",
                        lambda system, prompt: ["paralegal outsourcing checklist"])
    brand = {"id": "b", "domain": "x.com", "seeds": []}
    ranked, _ = topics.build_topics(brand, [], [], search=None)
    idea = next(t for t in ranked if t["source"] == "new idea")
    assert idea["keyword"] == "paralegal outsourcing checklist"
    assert idea["priority"] in ("high", "medium", "low")
    assert idea["impact"]


def test_run_keyword_lab_persists_and_lists_gaps():
    doc = keywords.run_keyword_lab(BRAND, [], trigger="test")
    assert doc["degraded"]
    assert doc["clusters"]
    assert keywords.latest("b")["keyword_count"] == doc["keyword_count"]
    assert doc["gaps"]  # nothing ranks -> everything is a gap


# ------------------------------- competitors -------------------------------

def serp_with(pos_ours):
    organic = [{"link": "https://comp.com/p1", "title": "Comp", "position": 1},
               {"link": "https://reddit.com/r/law", "title": "Reddit", "position": 2}]
    if pos_ours:
        organic.insert(pos_ours - 1, {"link": "https://x.com/page", "title": "Us", "position": pos_ours})
    return {"organic": organic[:10], "related": [], "paa": ["How much does it cost?"], "aio_present": False}


def test_tracked_keywords_fall_back_to_site_review_seeds(monkeypatch):
    monkeypatch.setattr(site_brain.sources, "llm_json", lambda s, p, **kw: REVIEW_JSON)
    site_brain.expert_review(BRAND, {"brand_id": "b", "page_count": 1, "pages": [], "degraded": []})
    seedless = {**BRAND, "seeds": []}
    tracked = competitors.tracked_keywords(seedless)
    assert "legal virtual assistant" in tracked  # from the site review's suggested_seeds


def _seed_rank_tracker_latest(brand_id, position, at):
    """Stand in for a rank_tracker sweep: writes the chunked-list shape
    rank_snapshot now projects from (rank_tracker.latest_rows/latest_meta)."""
    from seo_geo_agent import jobs, rank_tracker

    jobs.save_list(rank_tracker.LATEST_PREFIX.format(brand_id), [
        {"query": "legal virtual assistant", "position": position, "url": "https://x.com/page",
         "error": None,
         "top": [{"position": 1, "domain": "comp.com", "url": "https://comp.com/p1", "title": "Comp"},
                 {"position": position, "domain": "x.com", "url": "https://x.com/page", "title": "Us"}]},
    ], meta={"at": at, "ranked": 1, "errors": 0, "rivals": []})


def test_rank_snapshot_and_shifts():
    """rank_shifts compares the two latest calendar-day snapshots; rank_snapshot
    now builds those by projecting rank_tracker's latest sweep rather than
    running its own, so two sweeps on different days must still produce two
    distinct, ordered snapshot entries rank_shifts can diff."""
    _seed_rank_tracker_latest("b", 5, "2026-10-03T09:00:00+00:00")
    competitors.rank_snapshot(BRAND)
    _seed_rank_tracker_latest("b", 3, "2026-10-04T09:00:00+00:00")
    doc = competitors.rank_snapshot(BRAND)

    shifts = competitors.rank_shifts("b")
    assert shifts[0]["position"] == 3 and shifts[0]["previous"] == 5 and shifts[0]["delta"] == 2
    assert "comp.com" in doc["suggested_competitors"]
    assert "x.com" not in doc["suggested_competitors"]


def test_rank_snapshot_dedupes_same_calendar_day():
    """rank_tracker sweeps twelve times a day; rank_snapshot must replace that
    day's entry on each projection, not append a duplicate every time it runs."""
    _seed_rank_tracker_latest("dedupe-brand", 4, "2026-10-05T09:00:00+00:00")
    competitors.rank_snapshot({**BRAND, "id": "dedupe-brand"})
    _seed_rank_tracker_latest("dedupe-brand", 2, "2026-10-05T15:00:00+00:00")
    doc = competitors.rank_snapshot({**BRAND, "id": "dedupe-brand"})

    assert len(doc["snapshots"]) == 1
    assert doc["snapshots"][0]["ranks"]["legal virtual assistant"]["position"] == 2


def test_rank_snapshot_ignores_a_total_outage_and_keeps_todays_snapshot():
    """If every row in the latest sweep errored (Serper rate-limit/outage),
    projecting an empty ranks dict would, via the same-day dedup, *replace*
    the real snapshot already recorded for today. rank_tracker.append_history
    already skips errored rows for exactly this reason; rank_snapshot needs
    the matching guard: on a total-error sweep, leave the stored doc alone."""
    from seo_geo_agent import jobs, rank_tracker

    _seed_rank_tracker_latest("outage-brand", 4, "2026-10-05T09:00:00+00:00")
    good = competitors.rank_snapshot({**BRAND, "id": "outage-brand"})
    assert good["snapshots"][0]["ranks"]["legal virtual assistant"]["position"] == 4

    jobs.save_list(rank_tracker.LATEST_PREFIX.format("outage-brand"), [
        {"query": "legal virtual assistant", "position": None, "url": "", "top": [],
         "error": "serper 429"},
        {"query": "some other query", "position": None, "url": "", "top": [],
         "error": "serper 429"},
        {"query": "a third query", "position": None, "url": "", "top": [],
         "error": "timeout"},
    ], meta={"at": "2026-10-05T15:00:00+00:00", "ranked": 0, "errors": 3, "rivals": []})

    after_outage = competitors.rank_snapshot({**BRAND, "id": "outage-brand"})
    assert after_outage == good  # untouched: still today's real snapshot, not erased


def test_rank_tracking_pool_prioritizes_custom_then_auto_then_pool_terms(monkeypatch):
    brand = {**BRAND, "id": "pool-brand", "seeds": ["seed one"]}
    competitors.add_custom_query("pool-brand", "custom query one")
    # tracked_keywords() will pull "seed one" as the sole auto-derived term
    # (no keyword-lab clusters seeded for this brand id, so heads == [])
    pool = competitors.rank_tracking_pool(brand)
    assert pool[0].lower() == "custom query one"  # custom first
    assert "seed one" in pool


def test_rank_tracking_pool_deduplicates_and_caps_at_fifty(monkeypatch):
    brand = {**BRAND, "id": "cap-brand", "seeds": []}
    # Seeded directly via state rather than add_custom_query(): this test is about
    # rank_tracking_pool()'s own dedup+truncate behavior (MAX_RANK_POOL), which is
    # independent of add_custom_query()'s per-brand storage cap (MAX_CUSTOM_QUERIES) —
    # going through add_custom_query for 60 unique queries would trip that cap instead.
    competitors.state.save("custom-queries-cap-brand", {"queries": [f"query {i}" for i in range(60)]})
    pool = competitors.rank_tracking_pool(brand)
    assert len(pool) == competitors.MAX_RANK_POOL
    assert pool[0] == "query 0"  # first-added custom queries survive the cap


def test_add_custom_query_caps_total_stored_queries():
    brand_id = "query-cap-brand"
    for i in range(competitors.MAX_CUSTOM_QUERIES):
        competitors.add_custom_query(brand_id, f"query {i}")
    with pytest.raises(ValueError, match="at most 50 custom queries"):
        competitors.add_custom_query(brand_id, "one too many")
    # Re-adding (or no-op adding) an existing query is never blocked by the cap.
    existing = competitors.add_custom_query(brand_id, "query 0")
    assert len(existing) == competitors.MAX_CUSTOM_QUERIES


def test_custom_query_reaches_the_snapshot_through_the_real_sweep_pipeline():
    """The user-visible promise of the whole feature, end to end, with no
    persistence monkeypatched: a custom query added through the real
    ``add_custom_query`` must be picked up by ``rank_tracker.build_pool`` (the
    pool the scheduled sweep actually consumes — NOT the legacy
    ``competitors.rank_tracking_pool``, which nothing live calls any more),
    survive a sweep, and show up in ``rank_snapshot``'s projected snapshot.
    Only the outbound SERP call is faked."""
    from seo_geo_agent import rank_tracker

    brand = {"id": "custom-e2e", "name": "Acme", "domain": "x.com",
             "seeds": [], "competitors": []}
    competitors.add_custom_query(brand["id"], "my custom query")

    rank_tracker.build_pool(brand, rows_fn=lambda b: ([], []))

    def fake_search(q):
        return {"organic": [{"link": "https://x.com/custom-page", "title": "Us", "position": 7}],
                "related": [], "paa": []}

    rank_tracker.sweep(brand, search=fake_search)

    doc = competitors.rank_snapshot(brand)
    assert doc["snapshots"][-1]["ranks"]["my custom query"]["position"] == 7


def test_sitemap_watch_flags_new_content():
    first = competitors.sitemap_watch(BRAND, fetch_sitemap=lambda d: ["https://comp.com/a", "https://comp.com/b"])
    assert first["comp.com"]["first_check"] is True and first["comp.com"]["new_count"] == 0
    second = competitors.sitemap_watch(
        BRAND, fetch_sitemap=lambda d: ["https://comp.com/a", "https://comp.com/b", "https://comp.com/new"])
    assert second["comp.com"]["new_count"] == 1
    assert second["comp.com"]["new_urls"] == ["https://comp.com/new"]


def test_serp_deep_dive_extracts_shared_structure():
    pages = {
        "https://comp.com/p1": facts("https://comp.com/p1", h2=["Pricing and costs", "How it works"], words=2000),
        "https://reddit.com/r/law": facts("https://reddit.com/r/law", h2=["Pricing and costs", "Is it worth it?"], words=1000),
        "https://x.com/page": facts("https://x.com/page", h2=["Our features"], words=900),
    }
    deep = competitors.serp_deep_dive(
        BRAND, "legal virtual assistant", search=lambda q: serp_with(2), fetch=lambda u: pages[u])
    assert deep["our_position"] == 2
    assert "Pricing and costs" in deep["common_themes"]
    assert "How much does it cost?" in deep["questions"]
    assert "Is it worth it?" in deep["questions"]
    assert deep["target_word_count"] == 1300
    assert deep["pages_analyzed"] == 3


# --------------------------------- briefs ---------------------------------

def test_build_brief_offline_falls_back_to_serp_outline():
    pages = {u: facts(u, h2=["Pricing and costs"], words=1000)
             for u in ("https://comp.com/p1", "https://reddit.com/r/law", "https://x.com/page")}
    brief = briefs.build_brief(BRAND, "legal virtual assistant", [],
                               search=lambda q: serp_with(None), fetch=lambda u: pages[u])
    assert brief["degraded"]  # LLM offline
    assert any("FAQ" in o["heading"] for o in brief["outline"])
    assert brief["target_word_count"] == 1000
    assert briefs.list_briefs("b")[0]["id"] == brief["id"]
    briefs.build_brief(BRAND, "legal virtual assistant", [],
                       search=lambda q: serp_with(None), fetch=lambda u: pages[u])
    assert len(briefs.list_briefs("b")) == 1  # same keyword replaces, no dupes


def test_update_plan_flags_missing_sections():
    rows = [row("legal virtual assistant", page="https://x.com/page", impressions=900)]
    pages = {
        "https://comp.com/p1": facts("https://comp.com/p1", h2=["Pricing and costs breakdown"], schema=["Article"], words=2000),
        "https://reddit.com/r/law": facts("https://reddit.com/r/law", h2=["Pricing and costs breakdown"], words=2000),
        "https://x.com/page": facts("https://x.com/page", h2=["Why choose us"], words=600),
    }
    plan = briefs.update_plan(BRAND, "https://x.com/page", rows,
                              search=lambda q: serp_with(3), fetch=lambda u: pages[u])
    assert any("Pricing and costs" in s for s in plan["suggestions"])
    assert any("structured data" in s.lower() for s in plan["suggestions"])
    assert any("Deepen" in s for s in plan["suggestions"])


def test_update_plan_needs_page_data():
    try:
        briefs.update_plan(BRAND, "https://x.com/unknown", [], search=lambda q: serp_with(None), fetch=facts)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# --------------------------------- audit ---------------------------------

def test_site_audit_reports_issues_and_score():
    site = {
        "https://x.com/": facts("https://x.com/", title="Home", schema=["Organization"]),
        "https://x.com/a": facts("https://x.com/a", title="", meta=""),
        "https://x.com/broken": facts("https://x.com/broken", status=404),
    }
    report = audit.site_audit(
        BRAND, fetch=lambda u: site[u], sitemap=lambda d: list(site.keys()),
        get_text=lambda u: {"status": 200, "text": "User-agent: *\nSitemap: https://x.com/sitemap.xml",
                            "final_url": "https://x.com/"})
    names = [i["issue"] for i in report["issues"]]
    assert "Broken or unreachable pages" in names
    assert "Missing page title" in names
    assert report["pages_checked"] == 3 and report["pages_ok"] == 2
    assert 0 <= report["health_score"] < 100
    assert audit.latest_audit("b")["at"] == report["at"]


def test_site_checks_flag_missing_foundations():
    texts = {
        "https://x.com/robots.txt": {"status": 404, "text": "", "final_url": ""},
        "http://x.com/": {"status": 200, "text": "", "final_url": "http://x.com/"},
    }
    checks = {c["name"]: c for c in audit._site_checks("x.com", [], get_text=lambda u: texts[u])}
    assert checks["Sitemap.xml"]["ok"] is False and checks["Sitemap.xml"]["fix"]
    assert checks["Robots.txt"]["ok"] is False
    assert checks["HTTP → HTTPS redirect"]["ok"] is False


# -------------------------------- site brain --------------------------------

SITE = {
    "https://x.com/": facts("https://x.com/", title="Acme Legal — Home", h2=["What we do"]),
    "https://x.com/pricing": facts("https://x.com/pricing", title="Pricing", h2=["Plans"]),
}
SITE["https://x.com/"].text = "Acme Legal offers virtual assistants for law firms."
SITE["https://x.com/pricing"].text = "Plans start small and scale with your firm."

REVIEW_JSON = {
    "positioning": "Virtual assistants for US law firms.",
    "scorecard": {
        "intent": {"grade": 7, "note": "clamped"},          # out of range -> 5
        "content_depth": {"grade": 2, "note": "thin"},
        "trust": {"grade": "bad", "note": "ignored"},        # unparsable -> dropped
    },
    "strengths": ["Clear services"],
    "issues": [
        {"insight": "No testimonials anywhere", "evidence": "https://x.com/",
         "action": "Add 3 client testimonials to the homepage", "priority": "high", "category": "trust"},
        {"insight": "Pricing page is thin", "evidence": "https://x.com/pricing",
         "action": "Expand the pricing page with plan comparisons", "priority": "medium", "category": "content"},
    ],
    "suggested_seeds": ["legal virtual assistant", "law firm answering service"],
    "covered_topics": ["virtual assistants"],
    "missing_topics": ["pricing comparisons"],
}


def test_corpus_falls_back_without_llm():
    corpus = site_brain.build_corpus(BRAND, fetch=lambda u, c=None: SITE[u],
                                     sitemap=lambda d: list(SITE.keys()))
    assert corpus["page_count"] == 2
    assert any("Acme Legal" in p["summary"] for p in corpus["pages"])
    assert corpus["degraded"]  # offline: no AI summaries


def test_corpus_cache_skips_unchanged_pages(monkeypatch):
    calls = {"n": 0}

    def fake_llm(system, prompt):
        calls["n"] += 1
        return [{"summary": "s", "type": "service", "topics": ["t"]}] * prompt.count("PAGE ")

    monkeypatch.setattr(site_brain.sources, "llm_json", fake_llm)
    site_brain.build_corpus(BRAND, fetch=lambda u, c=None: SITE[u], sitemap=lambda d: list(SITE.keys()))
    first = calls["n"]
    site_brain.build_corpus(BRAND, fetch=lambda u, c=None: SITE[u], sitemap=lambda d: list(SITE.keys()))
    assert first >= 1 and calls["n"] == first  # second run: all pages cached


def test_expert_review_todos_and_seeds(monkeypatch):
    monkeypatch.setattr(site_brain.sources, "llm_json", lambda s, p, **kw: REVIEW_JSON)
    corpus = {"brand_id": "b", "page_count": 2, "pages": [], "degraded": []}
    review = site_brain.expert_review(BRAND, corpus)
    assert review["positioning"].startswith("Virtual assistants")
    assert review["scorecard"]["intent"]["grade"] == 5      # clamped into 1-5
    assert review["scorecard"]["content_depth"]["grade"] == 2
    assert "trust" not in review["scorecard"]               # unparsable grade dropped

    todos = site_brain.site_todos("b")
    assert todos[0]["kind"] == "site" and todos[0]["action"].startswith("Add 3 client")
    assert todos[0]["est_monthly_clicks"] is None  # no invented numbers
    assert todos == site_brain.site_todos("b")  # stable ids + order

    empty_seed_brand = {**BRAND, "seeds": []}
    assert site_brain.effective_seeds(empty_seed_brand)["seeds"][0] == "legal virtual assistant"
    assert site_brain.effective_seeds(BRAND)["seeds"] == BRAND["seeds"]  # own seeds win


def test_run_brand_merges_site_todos(monkeypatch):
    monkeypatch.setattr(site_brain.sources, "llm_json", lambda s, p, **kw: REVIEW_JSON)
    brand = insights.list_brands()[0]
    site_brain.expert_review(brand, {"brand_id": brand["id"], "page_count": 1, "pages": [], "degraded": []})
    run = insights.run_brand(brand, trigger="t")
    assert any(t["kind"] == "site" for t in run["todos"])


# ----------------------------- GSC OAuth connect -----------------------------

def _oauth_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")


def test_oauth_state_roundtrip_and_tamper(monkeypatch):
    _oauth_env(monkeypatch)
    token = gsc_oauth.make_state("berry")
    assert gsc_oauth.read_state(token) == "berry"
    with pytest.raises(ValueError):
        gsc_oauth.read_state(token[:-2] + "xx")


def test_oauth_needs_config(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    with pytest.raises(CredentialMissing):
        gsc_oauth.auth_url("b", "http://cb")


def test_match_property_prefers_domain_property():
    sites = [
        {"siteUrl": "https://www.berryvirtual.com/", "permissionLevel": "siteOwner"},
        {"siteUrl": "sc-domain:berryvirtual.com", "permissionLevel": "siteFullUser"},
        {"siteUrl": "https://other.com/", "permissionLevel": "siteUnverifiedUser"},
    ]
    assert gsc_oauth.match_property("berryvirtual.com", sites) == "sc-domain:berryvirtual.com"
    assert gsc_oauth.match_property("other.com", sites) is None  # unverified doesn't count


def test_oauth_complete_stores_connection(monkeypatch):
    _oauth_env(monkeypatch)
    monkeypatch.setattr(gsc_oauth, "_exchange",
                        lambda code, uri: {"refresh_token": "r1", "access_token": "a1"})
    monkeypatch.setattr(gsc_oauth, "_sites",
                        lambda tok: [{"siteUrl": "sc-domain:x.com", "permissionLevel": "siteOwner"}])
    assert gsc_oauth.complete(BRAND, "code", "http://cb")["property"] == "sc-domain:x.com"
    assert gsc_oauth.connection("b")["refresh_token"] == "r1"
    gsc_oauth.disconnect("b")
    assert gsc_oauth.connection("b") is None


def test_oauth_complete_rejects_wrong_account(monkeypatch):
    _oauth_env(monkeypatch)
    monkeypatch.setattr(gsc_oauth, "_exchange",
                        lambda code, uri: {"refresh_token": "r1", "access_token": "a1"})
    monkeypatch.setattr(gsc_oauth, "_sites", lambda tok: [])
    with pytest.raises(ValueError):
        gsc_oauth.complete(BRAND, "code", "http://cb")


def test_run_brand_uses_connected_property(monkeypatch):
    brand = insights.list_brands()[0]
    seen = {}
    monkeypatch.setattr(insights.gsc_oauth, "service", lambda bid: object())
    monkeypatch.setattr(insights.gsc_oauth, "connection",
                        lambda bid: {"property": "sc-domain:custom.com"})

    def fake_fetch(prop, s, e, service=None):
        seen["prop"], seen["svc"] = prop, service
        return [row("legal virtual assistant")]

    monkeypatch.setattr(insights, "gsc_fetch", fake_fetch)
    run = insights.run_brand(brand, trigger="t")
    assert seen["prop"] == "sc-domain:custom.com" and seen["svc"] is not None
    assert run["summary"]["mode"] == "search-console"


# --------------------------------- advisor ---------------------------------

def test_advisor_context_survives_empty_state():
    ctx = advisor._context(BRAND)
    assert "x.com" in ctx and "tech_audit" in ctx


def test_advisor_ask_offline_raises():
    with pytest.raises(CredentialMissing):
        advisor.ask(BRAND, "what should we do first?")


def test_score_draft_good_vs_bad():
    body = "This is a sentence about work. " * 120  # ~720 filler words in short sentences
    good = (
        "# Legal virtual assistant guide\n"
        "A legal virtual assistant helps law firms handle calls and intake. Acme Legal explains how.\n"
        "## What they do\ntext\n## Pricing\ntext\n" + body
    )
    scored = audit.score_draft(BRAND, good, "legal virtual assistant")
    assert scored["score"] >= 80
    assert scored["verdict"] == "publish-ready"
    bad = audit.score_draft(BRAND, "Buy now. Short page.", "legal virtual assistant")
    assert bad["score"] < 60 and bad["verdict"] == "rework"


def test_draft_score_uses_brief_questions():
    brief = {"target_word_count": 100, "questions": ["How much does it cost?"], "entities": ["pricing"]}
    text = "# Legal virtual assistant\nlegal virtual assistant pricing. How much does it cost? " + \
           "It depends on hours. Acme Legal offers plans. " + ("More detail here. " * 30) + \
           "\n## Costs\nx\n## FAQ\ny"
    scored = audit.score_draft(BRAND, text, "legal virtual assistant", brief)
    names = {c["name"]: c["ok"] for c in scored["checks"]}
    assert names["Questions covered"] is True
    assert names["Entities covered"] is True


def test_rank_snapshot_reads_rank_tracker_and_makes_no_serper_calls(monkeypatch):
    """One rank engine. A second live sweep would double the Serper bill."""
    from seo_geo_agent import competitors, jobs, rank_tracker

    jobs.save_list(rank_tracker.LATEST_PREFIX.format("b1"), [
        {"query": "clat coaching", "position": 4, "url": "https://lawpreptutorial.com/c",
         "error": None,
         "top": [{"position": 1, "domain": "rival.com", "url": "https://rival.com/a", "title": ""},
                 {"position": 4, "domain": "lawpreptutorial.com", "url": "", "title": ""}]},
    ], meta={"at": "2026-10-05T09:00:00+00:00", "ranked": 1, "errors": 0, "rivals": []})

    def boom(*a, **kw):
        raise AssertionError("rank_snapshot must not call a SERP provider")

    monkeypatch.setattr(competitors.sources, "brand_rank_search", boom)
    monkeypatch.setattr(competitors.sources, "serper_search", boom)

    doc = competitors.rank_snapshot({"id": "b1", "domain": "lawpreptutorial.com"})

    assert doc["snapshots"][-1]["ranks"]["clat coaching"]["position"] == 4
    assert "rival.com" in doc["suggested_competitors"]
