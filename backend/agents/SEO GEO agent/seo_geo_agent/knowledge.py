"""Expert playbooks for the production LLM — the distilled judgement layer.

The repo carries three full marketing skills (``.agents/skills/`` — seo-audit,
ai-seo, programmatic-seo, ~230KB). Those exist for the *development* agent;
the production model never sees them. What it gets instead is this module:
each skill distilled to a few KB of operational rules, stored in
``knowledge/*.md`` and injected into the system prompt of whichever call is
giving advice (the advisor chat, the site-brain expert review, content
briefs).

Deliberately NOT the full skills, and deliberately not a vector store:
- A 30KB skill in the prompt buries the brand's actual data under generic
  prose — the model starts answering from the essay instead of the DATA
  block, which is precisely the hallucination failure this agent avoids.
- The playbooks carry judgement (priorities, thresholds, what-beats-what);
  every number about THIS brand still comes only from the DATA context.

Routing is a keyword match, not an LLM call: the advisor answers in one
round-trip today and should stay that way. Unknown questions fall back to the
audit playbook — "what should we do" is an audit question.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "knowledge"

#: name -> filename. Order matters: when several match, earlier wins the cap.
PLAYBOOKS = {
    "seo_audit": "seo_audit.md",
    "ai_seo": "ai_seo.md",
    "programmatic_seo": "programmatic_seo.md",
}

#: A playbook larger than this would start crowding out the DATA block.
MAX_PLAYBOOK_CHARS = 5000

#: At most two playbooks per question — three means nothing was routed.
MAX_ROUTED = 2

_TRIGGERS: dict[str, tuple[str, ...]] = {
    "ai_seo": (
        "ai overview", "ai overviews", "ai mode", "chatgpt", "gpt", "perplexity",
        "claude", "gemini", "copilot", "llm", "cited", "citation", "citations",
        "aeo", " geo", "geo ", "answer engine", "generative", "ai search",
        "ai answer", "ai me", "ai visibility", "llms.txt", "zero-click",
    ),
    "programmatic_seo": (
        "programmatic", "pseo", "at scale", "scale pages", "template page",
        "templated", "location page", "city page", "centre page", "center page",
        "location pages", "city pages", "directory", "100 pages", "1000 pages",
        "bulk pages", "har city", "har location", "every city", "every location",
    ),
    "seo_audit": (
        "audit", "technical", "canonical", "noindex", "robots", "sitemap",
        "index", "crawl", "speed", "vitals", "lcp", "cls", "title", "meta",
        "h1", "schema", "cannibal", "thin", "duplicate", "orphan", "internal link",
        "drop", "fell", "gir", "traffic", "rank", "decay", "ctr", "snippet",
    ),
}


@lru_cache(maxsize=None)
def playbook(name: str) -> str:
    """One playbook's text, capped; empty string for anything unknown —
    knowledge must never be the reason an answer 500s."""
    filename = PLAYBOOKS.get(name)
    if not filename:
        return ""
    try:
        return (_DIR / filename).read_text(encoding="utf-8").strip()[:MAX_PLAYBOOK_CHARS]
    except OSError:
        return ""


def route(question: str) -> list[str]:
    """Which playbooks a question needs, most specific first, at most two.

    ``seo_audit`` is the fallback: a question that names nothing specific
    ("what should we do first?") is an audit question. It also deliberately
    loses ties to the two specialist playbooks — they exist precisely for the
    questions the audit playbook is too general for.
    """
    q = f" {re.sub(r'\\s+', ' ', (question or '').lower())} "
    hits = [name for name, triggers in _TRIGGERS.items()
            if name != "seo_audit" and any(t in q for t in triggers)]
    if any(t in q for t in _TRIGGERS["seo_audit"]):
        hits.append("seo_audit")
    return hits[:MAX_ROUTED] or ["seo_audit"]


def for_question(question: str) -> str:
    """The playbook text to inject for one advisor question."""
    return "\n\n---\n\n".join(filter(None, (playbook(n) for n in route(question))))


def for_site_review() -> str:
    """The expert review grades intent/content/architecture/trust/conversion
    AND ai_search — so it gets both the audit and the AI-search rulebooks."""
    return "\n\n---\n\n".join(filter(None, (playbook("seo_audit"), playbook("ai_seo"))))


def for_briefs() -> str:
    """Content briefs are written to be ranked AND cited — the AI-search
    structure rules are the ones a writer can act on."""
    return playbook("ai_seo")
