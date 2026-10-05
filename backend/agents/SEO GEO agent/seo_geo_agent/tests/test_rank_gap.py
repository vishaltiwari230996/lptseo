"""Gap card — the diff between our ranking page and the one beating it."""
from __future__ import annotations

from datetime import datetime, timezone

from seo_geo_agent import jobs, rank_gap, rank_tracker


def _brand() -> dict:
    return {"id": "b1", "name": "Law Prep Tutorial", "domain": "lawpreptutorial.com"}


def _seed(position=9):
    # Matches rank_tracker.sweep()'s real contract: ``url`` is only populated
    # when we actually rank (position is not None); otherwise it stays "".
    url = "https://lawpreptutorial.com/clat" if position is not None else ""
    jobs.save_list(rank_tracker.LATEST_PREFIX.format("b1"), [
        {"query": "clat coaching", "position": position,
         "url": url, "error": None,
         "top": [{"position": 1, "domain": "rival.com",
                  "url": "https://rival.com/clat", "title": "Rival"}]},
    ], meta={"at": "2026-10-05T09:00:00+00:00", "ranked": 1, "errors": 0, "rivals": []})


class FakePage:
    """Mimics the real ``sources.PageFacts`` shape: ``word_count``, ``h2``,
    ``h3``, ``schema_types``, ``status``, and a ``questions`` property
    computed the same way the real dataclass computes it (``h2 + h3``
    entries ending in ``?``).

    ``status`` defaults to 200 (a genuinely fetched page). The real
    ``sources.fetch_page`` does NOT raise on a non-200 response, a too-large
    body, or an ``httpx.HTTPError`` (timeout, connect error, too many
    redirects) — it returns a normal ``PageFacts`` with ``status`` set to the
    code (or left at 0 for the unreachable/timeout case) and everything else
    at its empty default. Tests that want to exercise that failure mode pass
    ``status=`` here rather than making the double raise.
    """

    def __init__(self, word_count, h2=None, h3=None, schema_types=None, status=200):
        self.word_count = word_count
        self.h2 = h2 or []
        self.h3 = h3 or []
        self.schema_types = schema_types or []
        self.status = status

    @property
    def questions(self) -> list[str]:
        return [h for h in self.h2 + self.h3 if h.strip().endswith("?")]


def _fetch(pages):
    return lambda url: pages[url]


def test_explain_diffs_our_page_against_the_leader():
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(400, h2=["What is CLAT"]),
        "https://rival.com/clat": FakePage(
            2200, h2=["What is CLAT?", "Syllabus"], h3=["What are the Fees?"],
            schema_types=["FAQPage"],
        ),
    }
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "They cover fees and syllabus; we do not.")

    assert doc["their_domain"] == "rival.com"
    assert doc["their_position"] == 1 and doc["our_position"] == 9
    assert doc["metrics"]["words"] == {"ours": 400, "theirs": 2200}
    assert doc["metrics"]["headings"]["theirs"] == 3
    assert doc["metrics"]["schema"]["theirs"] == ["FAQPage"]
    assert doc["metrics"]["questions"] == {"ours": 0, "theirs": 2}
    assert "fees" in doc["narrative"]
    assert doc["cached"] is False


def test_explain_caches_for_a_day_and_does_not_call_the_llm_twice():
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(400),
        "https://rival.com/clat": FakePage(2200),
    }
    calls = []

    def llm(system, prompt):
        calls.append(prompt)
        return "narrative"

    now = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)
    rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages), llm=llm, now=now)
    again = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages), llm=llm, now=now)

    assert len(calls) == 1
    assert again["cached"] is True


def test_explain_says_so_when_we_do_not_rank_at_all():
    _seed(position=None)
    pages = {"https://rival.com/clat": FakePage(2200)}
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "narrative")

    assert doc["our_url"] == ""
    assert doc["our_position"] is None
    assert doc["metrics"]["words"]["ours"] is None


def test_explain_404s_cleanly_for_a_query_that_was_never_swept():
    _seed()
    assert rank_gap.explain(_brand(), "never checked", fetch=lambda u: None,
                            llm=lambda s, p: "x") is None


def test_explain_degrades_when_a_page_cannot_be_fetched():
    """Covers the ``fetch`` callable itself raising (e.g. offline mode's
    ``CredentialMissing``, or any other hard failure before a PageFacts is
    even produced) — distinct from the real ``fetch_page`` contract below,
    where an HTTP-level failure does NOT raise but comes back as a normal
    PageFacts with ``status`` set."""
    _seed()

    def fetch(url):
        raise RuntimeError("403 Forbidden")

    doc = rank_gap.explain(_brand(), "clat coaching", fetch=fetch, llm=lambda s, p: "n")
    assert doc["narrative"]
    assert any("403" in n for n in doc["notes"])


def test_explain_treats_a_non_200_status_as_unknown_not_empty():
    """The real ``sources.fetch_page`` does not raise on a 403 — it returns
    a PageFacts with ``status=403`` and every other field at its empty
    default. That must not be read as a genuinely thin page: our word count
    has to come back ``None`` (unknown), not ``0`` (measured and empty), and
    a note must name the URL and the status."""
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(0, status=403),
        "https://rival.com/clat": FakePage(2200, schema_types=["FAQPage"]),
    }
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "narrative")

    assert doc["metrics"]["words"]["ours"] is None
    assert doc["metrics"]["headings"]["ours"] is None
    assert doc["metrics"]["schema"]["ours"] is None
    assert any("403" in n and "lawpreptutorial.com/clat" in n for n in doc["notes"])
    # Their side was genuinely fetched and must stay populated.
    assert doc["metrics"]["words"]["theirs"] == 2200
    assert doc["metrics"]["schema"]["theirs"] == ["FAQPage"]


def test_explain_treats_status_zero_as_unknown_too():
    """``status=0`` is the fetcher's shape for "never got a response at all"
    — a timeout or connect error caught inside ``fetch_page``. Same
    unknown-not-empty treatment, with a note that does not falsely claim an
    HTTP status code."""
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(0, status=0),
        "https://rival.com/clat": FakePage(2200),
    }
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "narrative")

    assert doc["metrics"]["words"]["ours"] is None
    assert any("lawpreptutorial.com/clat" in n and "fetch failed" in n for n in doc["notes"])


def test_explain_still_returns_a_card_when_both_sides_are_unreadable():
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(0, status=403),
        "https://rival.com/clat": FakePage(0, status=503),
    }
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "narrative")

    assert doc["metrics"]["words"] == {"ours": None, "theirs": None}
    assert len(doc["notes"]) == 2
    assert doc["narrative"] == "narrative"


def test_explain_a_genuinely_empty_page_reports_zero_not_none():
    """The distinction that matters: a real ``status=200`` page with no
    words is a thin page we need to rewrite and must show ``0``. A page we
    could not read is unknown and must show ``None``. The two must never
    look the same."""
    _seed()
    pages = {
        "https://lawpreptutorial.com/clat": FakePage(0, status=200),
        "https://rival.com/clat": FakePage(2200),
    }
    doc = rank_gap.explain(_brand(), "clat coaching", fetch=_fetch(pages),
                           llm=lambda s, p: "narrative")

    assert doc["metrics"]["words"]["ours"] == 0
    assert doc["metrics"]["headings"]["ours"] == 0
    assert doc["metrics"]["schema"]["ours"] == []
    assert doc["notes"] == []
