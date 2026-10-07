"""Ask the SEO expert — grounded chat over everything the agent knows about a brand.

The answer is only as good as the stored data, so the context builder gathers
every doc the agent has persisted (runs, keyword tiers, ranks, audits, briefs)
and the system prompt forbids inventing numbers that aren't in it.

Two rules keep the model honest, learned the hard way:

* **The context is packed, never truncated.** This used to be
  ``json.dumps(ctx)[:9000]`` — which hands the model a JSON document whose
  last field is cut mid-value. A model given broken data completes it, and a
  completed number is a hallucinated number. Sections are now added whole, in
  priority order, and a section that would blow the budget is dropped whole.
  Whatever reaches the model always parses.

* **The packing order follows the question.** A rank question packs rank
  data first, a technical question the deep audit — so when the budget
  squeezes, the off-topic tail is what drops, never the answer.

* **Playbooks carry judgement, DATA carries facts.** The system prompt gets a
  distilled expert playbook (``knowledge.py``) routed by the question — audit
  judgement, AI-citation rules, pages-at-scale rules — but every number about
  THIS brand still comes only from the DATA block.
"""
from __future__ import annotations

import json

from . import audit, digests, keywords, knowledge, sources, state

#: Character budget for the DATA block. Generous for a chat prompt, small
#: enough that the model reads all of it.
CONTEXT_BUDGET = 9000


def _sections(brand: dict) -> dict[str, object]:
    """Every context section, built from the digest layer — the raw docs
    (crawl records, 200-row rank tables, full keyword pools) never appear
    here, only ``digests.py``'s capped shapes."""
    brand_id = brand["id"]
    lab = keywords.latest(brand_id) or {}
    report = audit.latest_audit(brand_id) or {}
    ranks = state.load(f"ranks-{brand_id}") or {}

    return {
        "brand": {"name": brand["name"], "domain": brand["domain"],
                  "seeds": brand.get("seeds", []),
                  "tracked_competitors": brand.get("competitors", [])},
        "latest_run": digests.traffic_digest(brand_id),
        "deep_audit": digests.deep_digest(brand_id),
        "rank_tracking": digests.rank_digest(brand),
        "keyword_pool": digests.keyword_digest(brand_id),
        "keyword_map": {
            "at": lab.get("at"), "keyword_count": lab.get("keyword_count"),
            "clusters": [
                {k: c.get(k) for k in ("name", "tier", "intent", "coverage", "best_position",
                                       "recommendation", "owned_by")}
                for c in lab.get("clusters", [])[:12]
            ],
            "content_gaps": lab.get("gaps", [])[:10],
        } if lab else None,
        "tech_audit": {
            "at": report.get("at"), "health_score": report.get("health_score"),
            "site_checks": report.get("site_checks"),
            "issues": [
                {k: i.get(k) for k in ("issue", "severity", "count", "fix")}
                for i in report.get("issues", [])
            ],
        } if report else None,
        "site_review": digests.review_digest(brand_id),
        "competitors": digests.competitor_digest(brand_id),
        "suggested_competitors": ranks.get("suggested_competitors", []),
        "competitor_new_content": digests.sitemap_watch_digest(brand_id),
        "existing_briefs": digests.briefs_digest(brand_id),
    }


#: Packing order per question topic. ``brand`` and ``latest_run`` always lead;
#: after that, the sections the question is actually about — so when the
#: budget squeezes, what gets dropped is the off-topic tail, not the answer.
_ORDERS: dict[str, tuple[str, ...]] = {
    "default": ("brand", "latest_run", "deep_audit", "rank_tracking", "keyword_pool",
                "keyword_map", "tech_audit", "site_review", "competitors",
                "suggested_competitors", "competitor_new_content", "existing_briefs"),
    "rank": ("brand", "latest_run", "rank_tracking", "competitors", "suggested_competitors",
             "keyword_pool", "deep_audit", "keyword_map", "tech_audit", "site_review",
             "competitor_new_content", "existing_briefs"),
    "technical": ("brand", "latest_run", "deep_audit", "tech_audit", "rank_tracking",
                  "keyword_pool", "site_review", "keyword_map", "competitors",
                  "suggested_competitors", "competitor_new_content", "existing_briefs"),
    "content": ("brand", "latest_run", "keyword_map", "keyword_pool", "site_review",
                "existing_briefs", "competitor_new_content", "rank_tracking", "deep_audit",
                "competitors", "suggested_competitors", "tech_audit"),
    "ai": ("brand", "latest_run", "site_review", "keyword_map", "deep_audit",
           "keyword_pool", "rank_tracking", "competitors", "existing_briefs",
           "suggested_competitors", "competitor_new_content", "tech_audit"),
}

_TOPIC_TRIGGERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai", ("ai overview", "chatgpt", "perplexity", "gemini", "copilot", "llm",
            "cited", "citation", "aeo", "ai search", "ai answer", "ai visibility")),
    ("rank", ("rank", "position", "serp", "drop", "fell", "gir", "overtake", "upar",
              "neeche", "competitor", "beat", "outrank")),
    ("technical", ("audit", "canonical", "noindex", "robots", "sitemap", "index",
                   "crawl", "speed", "vitals", "lcp", "cls", "slow", "technical")),
    ("content", ("blog", "content", "article", "topic", "keyword", "brief",
                 "write", "likh", "publish")),
)


def _order(question: str) -> tuple[str, ...]:
    q = f" {(question or '').lower()} "
    for topic, triggers in _TOPIC_TRIGGERS:
        if any(t in q for t in triggers):
            return _ORDERS[topic]
    return _ORDERS["default"]


def _context(brand: dict, question: str = "", budget: int = CONTEXT_BUDGET) -> str:
    """Compact, honest snapshot: sections added whole, ordered by what the
    question is about; a section that would overflow the budget is dropped
    whole, so the model always receives valid JSON. Empty sections are dropped
    too — absence is the honest signal the system prompt knows how to answer."""
    sections = _sections(brand)
    ctx: dict[str, object] = {}
    rendered = "{}"
    for key in _order(question):
        value = sections.get(key)
        if value in (None, {}, []):
            continue
        trial = dict(ctx)
        trial[key] = value
        serialized = json.dumps(trial, ensure_ascii=False)
        if len(serialized) > budget and ctx:
            continue  # this section doesn't fit — a smaller, later one still may
        ctx = trial
        rendered = serialized
        if len(rendered) > budget:
            # First section alone overflows: keep it (brand identity beats
            # nothing) but stop adding and cut at the top level only.
            break
    if len(rendered) > budget:
        # Degenerate case — even the first section is enormous. Fall back to
        # the brand header alone rather than emitting broken JSON.
        rendered = json.dumps({"brand": {"name": brand["name"], "domain": brand["domain"]}},
                              ensure_ascii=False)
    return rendered


SYSTEM = (
    "You are the dedicated SEO strategist for the brand in DATA. Answer the owner's question "
    "directly and concretely, like a senior consultant on a call.\n"
    "Rules:\n"
    "- Ground every claim in DATA. Never invent traffic numbers, rankings, or facts not present.\n"
    "- The EXPERT PLAYBOOK below is your judgement — priorities, thresholds, what beats what. "
    "It contains industry benchmarks, never facts about this brand: any brand-specific number "
    "must come from DATA.\n"
    "- If DATA is missing what the question needs, say exactly which dashboard action fills it "
    "(Refresh data / Map keywords / Check now / Run audit / Run deep audit / Build brief).\n"
    "- Always end with 1-3 specific next actions, most valuable first.\n"
    "- Mirror the user's language (English or Hinglish). Keep it under 250 words, no headers, "
    "short paragraphs or dashes."
)


def ask(brand: dict, question: str) -> dict:
    system = f"{SYSTEM}\n\nEXPERT PLAYBOOK:\n{knowledge.for_question(question)}"
    answer = sources.llm_text(
        system, f"DATA:\n{_context(brand, question)}\n\nQUESTION: {question}")
    return {"question": question, "answer": answer}
