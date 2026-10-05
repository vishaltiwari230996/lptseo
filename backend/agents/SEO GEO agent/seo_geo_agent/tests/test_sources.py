"""sources.py — real-Serper rank-tracking backend, kept separate from DataForSEO."""
from __future__ import annotations

from unittest.mock import Mock, patch

from seo_geo_agent import sources
from seo_geo_agent.sources import CredentialMissing


def test_brand_rank_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("SEO_SERPER_API_KEY", raising=False)
    assert sources.brand_rank_available() is False


def test_brand_rank_available_with_key(monkeypatch):
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")
    assert sources.brand_rank_available() is True


def test_brand_rank_search_raises_without_key(monkeypatch):
    monkeypatch.delenv("SEO_SERPER_API_KEY", raising=False)
    try:
        sources.brand_rank_search("clat coaching jaipur")
        assert False, "expected CredentialMissing"
    except CredentialMissing as exc:
        assert "SEO_SERPER_API_KEY" in str(exc)


def test_brand_rank_search_parses_real_serper_shape(monkeypatch):
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {
        "organic": [
            {"link": "https://lawpreptutorial.com/clat", "title": "CLAT Coaching", "position": 1},
            {"link": "https://competitor.com/clat", "title": "Competitor CLAT", "position": 2},
        ],
        "relatedSearches": [{"query": "best clat coaching"}],
        "peopleAlsoAsk": [{"question": "Which is the best CLAT coaching?"}],
        "aiOverview": {"text": "CLAT coaching overview..."},
    }
    fake_client = Mock()
    fake_client.post.return_value = fake_response

    result = sources.brand_rank_search("clat coaching", client=fake_client)

    assert result["organic"][0]["link"] == "https://lawpreptutorial.com/clat"
    assert result["organic"][1]["position"] == 2
    assert result["related"] == ["best clat coaching"]
    assert result["paa"] == ["Which is the best CLAT coaching?"]
    assert result["aio_present"] is True

    # confirm it hit the real Serper endpoint with the API-key header, not DataForSEO's Basic auth
    call_kwargs = fake_client.post.call_args.kwargs
    assert call_kwargs["headers"]["X-API-KEY"] == "test-key"
    assert "auth" not in call_kwargs


def test_brand_rank_search_sends_india_locale_and_depth(monkeypatch):
    """A US SERP is the wrong instrument for a Jodhpur CLAT brand."""
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")
    sent = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"organic": []}

    class FakeClient:
        def post(self, url, json=None, headers=None):
            sent.update(json)
            return FakeResponse()

    sources.brand_rank_search("clat coaching", client=FakeClient())

    assert sent["gl"] == "in"
    assert sent["hl"] == "en"
    # Measured against the live API: num=10, num=20 and num=100 all bill one
    # credit and all cap at 10 organic results on page 1 — `num` is ignored
    # by this Serper plan. SERP_RESULTS was dropped from 20 to 10 to stop
    # pretending otherwise; depth past 10 comes from `page` instead.
    assert sent["num"] == 10
    assert sent["q"] == "clat coaching"
    assert "page" not in sent  # page 1 is omitted, not sent as page=1


def test_brand_rank_search_locale_is_overridable(monkeypatch):
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")
    sent = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"organic": []}

    class FakeClient:
        def post(self, url, json=None, headers=None):
            sent.update(json)
            return FakeResponse()

    sources.brand_rank_search("clat", client=FakeClient(), gl="us", num=10)
    assert sent["gl"] == "us"
    assert sent["num"] == 10


def test_brand_rank_search_page_2_is_sent_and_offsets_nothing_itself(monkeypatch):
    """`page` defaults to 1 and is omitted from the body then (byte-identical
    with every existing caller); page 2 is sent explicitly. Any absolute-rank
    offsetting (+10) is the caller's job (`rank_tracker.sweep`), not this
    function's — it hands back whatever Serper says, as-is."""
    monkeypatch.setenv("SEO_SERPER_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")
    sent = {}

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {"organic": [{"link": "https://rival.com/x", "title": "t", "position": 1}]}

    class FakeClient:
        def post(self, url, json=None, headers=None):
            sent.update(json)
            return FakeResponse()

    result = sources.brand_rank_search("clat", client=FakeClient(), page=2)

    assert sent["page"] == 2
    assert result["organic"][0]["position"] == 1  # untouched — not offset here
