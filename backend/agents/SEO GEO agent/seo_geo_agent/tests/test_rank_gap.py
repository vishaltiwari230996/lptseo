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
    ``h3``, ``schema_types``, and a ``questions`` property computed the same
    way the real dataclass computes it (``h2 + h3`` entries ending in ``?``)."""

    def __init__(self, word_count, h2=None, h3=None, schema_types=None):
        self.word_count = word_count
        self.h2 = h2 or []
        self.h3 = h3 or []
        self.schema_types = schema_types or []

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
    _seed()

    def fetch(url):
        raise RuntimeError("403 Forbidden")

    doc = rank_gap.explain(_brand(), "clat coaching", fetch=fetch, llm=lambda s, p: "n")
    assert doc["narrative"]
    assert any("403" in n for n in doc["notes"])
