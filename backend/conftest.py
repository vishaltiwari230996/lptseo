# backend/conftest.py
"""Repo-root test guards — apply to EVERY test in this extract.

Carried over from the AgentOS backend's root conftest, cut down to the two
guards the SEO agent can actually trip (the DataForSEO and Cloud Storage guards
there belong to the GEO and Graphics Designer agents, which are not here):

1. ``SEO_OFFLINE`` defaults ON for the whole suite, so a suite without its own
   conftest can never talk to prod.
2. ``firestore_repo._db`` is replaced with a loud failure. Tests that
   legitimately exercise Firestore paths already monkeypatch ``_db`` (or a
   higher-level function) themselves — that override wins over this fixture.
   Best-effort writers (usage events, run tracking) swallow the error by
   design, exactly as they would offline.
3. The OpenRouter key reads empty, so every LLM path takes the documented "no
   key configured" fallback instead of spending real credits — and real network
   latency — on whoever happens to run the suite.
4. ``SEO_ALLOW_NETWORK`` is forced OFF. Guard 1 stopped being enough the moment
   that switch existed: ``state._load_local_env`` exports every ``SEO_*`` key
   out of ``backend/.env`` into the environment, so a developer who turns
   outbound calls on for their own machine was silently turning them on for the
   suite too — the run went from 3 seconds to 3 minutes of real Google and
   Serper traffic, and still passed, because these paths all degrade quietly.
   Assigned, not ``setdefault``, so ``.env`` can never win.
"""
import os

import pytest

os.environ.setdefault("SEO_OFFLINE", "1")
os.environ["SEO_ALLOW_NETWORK"] = "0"

# Which agent each test belongs to, derived from its path. Keep the fragments
# lowercase — they are matched against a lowercased, forward-slashed path.
_AGENT_MARKERS = (
    ("agents/seo geo agent", "seo"),
)


@pytest.fixture(autouse=True)
def _no_prod_firestore(monkeypatch):
    try:
        from app.services import firestore_repo
    except ImportError:
        yield
        return

    def _blocked():
        raise RuntimeError(
            "Firestore access blocked in tests — monkeypatch firestore_repo._db "
            "(or the specific repo function) instead of talking to the live DB."
        )

    monkeypatch.setattr(firestore_repo, "_db", _blocked)
    yield


@pytest.fixture(autouse=True)
def _no_live_openrouter(monkeypatch):
    """The OpenRouter key reads empty, which is the whole guard.

    Every network entry point in ``app.services.openrouter`` resolves the key
    through ``runtime_config.require`` first, so a blank key turns all of them
    into the raise that callers already expect. Callers that ask permission
    first see False and take their curated branch.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    try:
        from app.config import settings
    except ImportError:
        yield
        return

    monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
    yield


def pytest_collection_modifyitems(items):
    """Tag every test with the agent it belongs to, derived from its path."""
    for item in items:
        path = str(item.fspath).replace("\\", "/").lower()
        for fragment, marker in _AGENT_MARKERS:
            if fragment in path:
                item.add_marker(getattr(pytest.mark, marker))
                break
        else:
            item.add_marker(pytest.mark.core)
