"""Tests for the expert-knowledge layer: distilled playbooks the production
LLM receives, and the advisor context that must never truncate mid-JSON."""
from __future__ import annotations

import json

from seo_geo_agent import advisor, knowledge, state

BRAND = {"id": "b", "name": "Acme Legal", "domain": "x.com",
         "seeds": ["legal virtual assistant"], "competitors": []}


# ------------------------------- playbooks ---------------------------------

def test_every_playbook_loads_and_is_prompt_sized():
    for name in knowledge.PLAYBOOKS:
        text = knowledge.playbook(name)
        assert len(text) > 500, f"{name} is too thin to be worth injecting"
        assert len(text) <= knowledge.MAX_PLAYBOOK_CHARS, f"{name} would bloat the prompt"


def test_unknown_playbook_is_empty_not_a_crash():
    assert knowledge.playbook("no-such-playbook") == ""


def test_ai_questions_route_to_the_ai_seo_playbook():
    for q in ("why are we not in AI Overviews?",
              "ChatGPT me humara brand kyu nahi aata",
              "how do we get cited by Perplexity"):
        names = knowledge.route(q)
        assert "ai_seo" in names, q


def test_scale_questions_route_to_programmatic():
    names = knowledge.route("should we build city pages for every location at scale?")
    assert "programmatic_seo" in names


def test_generic_questions_fall_back_to_the_audit_playbook():
    assert knowledge.route("what should we do first?") == ["seo_audit"]
    assert knowledge.route("") == ["seo_audit"]


def test_route_never_returns_more_than_two():
    q = "AI Overview citations for our programmatic city template pages after the ranking drop"
    assert len(knowledge.route(q)) <= 2


def test_for_question_concatenates_routed_playbooks():
    text = knowledge.for_question("how to appear in AI Overviews")
    assert "answer" in text.lower()
    assert len(text) <= 2 * knowledge.MAX_PLAYBOOK_CHARS + 200


# ---------------------------- advisor context ------------------------------

def test_context_is_always_valid_json():
    ctx = advisor._context(BRAND)
    json.loads(ctx)  # must parse whole — no mid-structure truncation


def test_context_is_valid_json_even_under_a_tiny_budget():
    ctx = advisor._context(BRAND, budget=400)
    parsed = json.loads(ctx)
    assert "brand" in parsed  # the most important section survives first


def test_context_respects_the_budget():
    for budget in (400, 2000, 9000):
        assert len(advisor._context(BRAND, budget=budget)) <= budget


def test_context_drops_whole_sections_never_fields():
    """Pad one section until the default budget overflows: the packer must
    drop entire sections (later, less-important ones), keeping valid JSON."""
    state.save("sitereview-b", {"at": "2026-01-01", "positioning": "x" * 12000,
                                "scorecard": {}, "strengths": [], "issues": [],
                                "covered_topics": [], "missing_topics": []})
    try:
        ctx = advisor._context(BRAND)
        parsed = json.loads(ctx)
        assert len(ctx) <= advisor.CONTEXT_BUDGET
        assert "brand" in parsed
        assert "site_review" not in parsed  # the oversized section was dropped whole
    finally:
        state.delete("sitereview-b")


def test_context_includes_deep_audit_when_present():
    state.save("deepaudit-b", {"at": "2026-01-01", "domain": "x.com", "urls": 100,
                               "sitemaps": 2, "pages_by_type": {"landing": 40},
                               "live_pages": 98, "gsc_connected": False, "notes": [],
                               "sitemap": {"score": 55, "affected": 12, "issues": 4},
                               "landing": {"pages": 40, "avg_score": 61, "high": 9,
                                           "template_issues": 2, "grades": {"A": 1}},
                               "cannibalization": {"pairs": 3, "high": 1, "medium": 2, "confirmed": 1},
                               "density": {"posts": 20, "pass": 11, "average_only": 4,
                                           "fail": 5, "median_wpm": 180}})
    try:
        parsed = json.loads(advisor._context(BRAND))
        deep = parsed["deep_audit"]
        assert deep["sitemap"]["score"] == 55
        assert deep["landing"]["high"] == 9
    finally:
        state.delete("deepaudit-b")


def test_context_order_follows_the_question(monkeypatch):
    """Under a tight budget, a rank question keeps rank data and a technical
    question keeps the deep audit — the off-topic section is what drops."""
    from seo_geo_agent import digests

    rows = [{"query": "clat coaching", "position": 4, "url": "https://x.com/c", "top": [],
             "checked_at": "t", "error": None, "impressions": 100, "delta_7d": -2,
             "dropped": False, "leader": "rival.com", "leader_position": 1, "leader_url": "u"}]
    monkeypatch.setattr(digests.rank_tracker, "annotate_rows", lambda brand: rows)
    state.save("deepaudit-b", {"at": "2026-01-01", "domain": "x.com", "urls": 10, "sitemaps": 1,
                               "pages_by_type": {}, "live_pages": 10, "gsc_connected": False,
                               "notes": [], "sitemap": {"score": 90, "affected": 0, "issues": 0},
                               "landing": {"pages": 5, "avg_score": 80, "high": 0,
                                           "template_issues": 0, "grades": {}},
                               "cannibalization": {"pairs": 0, "high": 0, "medium": 0, "confirmed": 0},
                               "density": {"posts": 0, "pass": 0, "average_only": 0,
                                           "fail": 0, "median_wpm": None}})
    try:
        # Budget fits brand + roughly one more section — the on-topic one.
        rank_ctx = json.loads(advisor._context(BRAND, "why did our rank drop?", budget=900))
        tech_ctx = json.loads(advisor._context(BRAND, "run a technical audit summary", budget=900))
    finally:
        state.delete("deepaudit-b")
    assert "rank_tracking" in rank_ctx
    assert "deep_audit" in tech_ctx


def test_site_review_prompt_carries_the_playbooks(monkeypatch):
    from seo_geo_agent import site_brain

    sent = {}

    def fake_llm(system, prompt, **kw):
        sent["system"] = system
        return {"positioning": "p", "scorecard": {}, "strengths": [], "issues": [],
                "suggested_seeds": [], "covered_topics": [], "missing_topics": []}

    monkeypatch.setattr(site_brain.sources, "llm_json", fake_llm)
    site_brain.expert_review(BRAND, {"brand_id": "b", "page_count": 1, "pages": [], "degraded": []})
    assert "EXPERT PLAYBOOK" in sent["system"]
    assert "CRAWLABILITY" in sent["system"]          # seo_audit playbook
    assert "cited" in sent["system"].lower()         # ai_seo playbook
    # The honesty rules must survive the injection, not be replaced by it.
    assert "Never invent traffic numbers" in sent["system"]


def test_brief_outline_prompt_carries_the_content_rules(monkeypatch):
    from seo_geo_agent import briefs as briefs_mod

    sent = {}

    def fake_llm(system, prompt, **kw):
        sent["system"] = system
        return {"outline": [{"heading": "h", "note": "n"}]}

    monkeypatch.setattr(briefs_mod.sources, "llm_json", fake_llm)
    serp = {"organic": [], "related": [], "paa": [], "aio_present": False}
    briefs_mod.build_brief(BRAND, "legal virtual assistant", [],
                           search=lambda q: serp, fetch=lambda u: None)
    assert "40-60 words" in sent["system"]           # the extractability rule


def test_ask_sends_knowledge_and_grounded_data(monkeypatch):
    sent = {}

    def fake_llm(system, prompt):
        sent["system"], sent["prompt"] = system, prompt
        return "answer"

    monkeypatch.setattr(advisor.sources, "llm_text", fake_llm)
    out = advisor.ask(BRAND, "why are we not cited in AI Overviews?")
    assert out["answer"] == "answer"
    assert "EXPERT PLAYBOOK" in sent["system"]
    assert "answer-first" in sent["system"].lower() or "extractab" in sent["system"].lower()
    assert "DATA:" in sent["prompt"]
    json.loads(sent["prompt"].split("DATA:\n", 1)[1].rsplit("\n\nQUESTION:", 1)[0])
