"""Keywords and content similarity, shared by every deep-audit check.

The landing-page audit, the cannibalization check and the blog keyword-density
check all have to answer "what is this page's keyword?" and "is this the same
word?". If each answered on its own, a page could pass the title check for a
keyword the density check had never heard of. So there is one answer, here.

**Matching is on stems, not spellings.** "CLAT coaching" in a title and "clat
coachings" in a slug are the same target, and "course" / "courses" is the
same word to a searcher and to Google. The stemmer is deliberately tiny and
deterministic — a plural stripper, not a linguistic stemmer — because a
precise audit has to be able to say exactly why two words were treated as one,
and "the Porter algorithm said so" is not an answer anyone can check by eye.

**Similarity is exact, not estimated.** Near-duplicate detection uses the true
Jaccard index over 5-word shingles rather than a MinHash estimate. At a few
thousand pages the exact figure is cheap, and a report that says two pages are
"87% identical" should mean 87%, not "somewhere near 87%".
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from .crawl import words

#: Function words: never part of what a page targets.
STOP = frozenset("""
a an the and or of to in on at for from by with without into onto over under about
as is are was were be been being this that these those it its your you our we us my
how what why when where which who whom whose vs versus than then there here
""".split())

#: Marketing modifiers. They sit in titles and H1s on nearly every commercial
#: page and say nothing about the query a page is built to win — "Best CLAT
#: Coaching" and "CLAT Coaching" target the same searcher.
MARKETING = frozenset("""
best top leading no number 1 #1 1st no1 affordable cheap expert experts guide
complete ultimate official free trusted famous popular latest updated new
""".split())

_YEAR = re.compile(r"^(19|20)\d{2}$")
_NUM = re.compile(r"^\d+$")


def stem(token: str) -> str:
    """Plural to singular, and nothing else.

    ``colleges -> college``, ``classes -> class``, ``universities -> university``,
    ``papers -> paper``. Words that merely end in s (``status``, ``analysis``,
    ``class``) are left alone.
    """
    t = token.lower()
    if len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("sses"):
        return t[:-2]
    if t.endswith(("ches", "shes", "xes", "zes")):
        return t[:-2]
    if t.endswith("s") and not t.endswith(("ss", "us", "is", "ys")):
        return t[:-1]
    return t


def pairs(text: str, *, drop: frozenset[str] = frozenset(), keep_years: bool = False,
          keep_numbers: bool = False) -> list[tuple[str, str]]:
    """(stem, original word) for every meaningful word, in order, without repeats.

    The stem is what matching compares; the original is what a person reads.
    Keeping both is why a keyword can match "Khas" against "khas" internally and
    still be shown as "hauz khas", not the stem "hauz kha".

    Numbers and years are dropped unless asked for. For *matching text* that is
    right — "top 10 law colleges" and "law colleges" are the same query. For a
    page's *identity* it is wrong: in ``/previous-year-papers/2025-pdf/`` and
    ``/2026-pdf/`` the year is the only thing that makes them different
    documents. Callers that build identities pass ``keep_numbers=True``.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for w in words((text or "").replace("_", " ").replace("-", " ")):
        low = w.lower()
        if low in STOP or low in MARKETING or low in drop:
            continue
        numeric = bool(_NUM.match(low))
        if numeric:
            if keep_numbers or (keep_years and _YEAR.match(low)):
                pass
            else:
                continue
        s = low if numeric else stem(low)
        if s and s not in seen:
            seen.add(s)
            out.append((s, low))
    return out


def tokens(text: str, *, drop: frozenset[str] = frozenset(), keep_years: bool = False,
           keep_numbers: bool = False) -> list[str]:
    """Meaningful tokens, stemmed, in order, without repeats. See :func:`pairs`."""
    return [s for s, _ in pairs(text, drop=drop, keep_years=keep_years, keep_numbers=keep_numbers)]


def brand_tokens(brand_name: str) -> frozenset[str]:
    """The brand phrase as tokens — removed from titles as a PHRASE, never token
    by token, because "Law Prep Tutorial" contains "law", and "law" is a real
    keyword on a law-coaching site."""
    return frozenset(w.lower() for w in words(brand_name))


def strip_brand(text: str, brand_name: str) -> str:
    """Remove the brand as a phrase, plus the separators around it ("| Brand",
    "- Brand"), leaving every other occurrence of its words intact."""
    if not brand_name:
        return text or ""
    pattern = re.compile(r"\s*[|\-–—:]?\s*" + re.escape(brand_name) + r"\s*[|\-–—:]?\s*", re.I)
    return pattern.sub(" ", text or "").strip()


def _slug_text(url: str) -> str:
    path = urlparse(url).path or "/"
    return " ".join(s for s in path.split("/") if s and s.lower() not in ("blog", "category", "tag"))


def slug_tokens(url: str) -> list[str]:
    return tokens(_slug_text(url), keep_numbers=True)


def target_keyword(record: dict, brand_name: str, gsc_top: str | None = None) -> dict:
    """The phrase a page is built to rank for, and where that answer came from.

    Order of preference:

    1. **Search Console** — the query the page actually earns the most
       impressions for. This is the only source that reflects what Google thinks
       the page is about, so it wins when it exists.
    2. **URL slug**, when it carries at least two meaningful words. On a
       structured site the slug is the most deliberate statement of intent
       (``/jaipur/clat-coaching``), and it is not diluted by marketing copy.
    3. **H1**, then the title, for pages whose slug is one word
       (``/hyderabad``), where the slug alone names a place but not a service.

    Token order follows the H1 where the tokens appear in it, so the phrase
    reads the way the page reads it ("clat coaching faridabad", not "faridabad
    clat coaching"). Matching elsewhere is on the token set, so order never
    changes a pass or a fail.
    """
    h1 = strip_brand(" ".join(record.get("h1") or []), brand_name)
    title = strip_brand(record.get("title") or "", brand_name)
    if gsc_top:
        ps = pairs(gsc_top, keep_years=True)
        return {"phrase": " ".join(o for _, o in ps), "tokens": [s for s, _ in ps],
                "source": "search-console"}

    slug_ps = pairs(_slug_text(record.get("final_url") or record.get("url", "")), keep_numbers=True)
    head_ps = pairs(h1) or pairs(title)
    originals = {s: o for s, o in slug_ps}
    for s_, o in head_ps:
        originals.setdefault(s_, o)
    slug = [s_ for s_, _ in slug_ps]
    heading = [s_ for s_, _ in head_ps]
    # A slug counts as a statement of intent when it has two meaningful WORDS —
    # a number alone does not make "/air-1/" a two-word target.
    slug_words = [t for t in slug if not _NUM.match(t)]
    if len(slug_words) >= 2:
        chosen, source = slug, "url"
    elif heading:
        # One-word slug: the slug's word plus the heading's service words.
        chosen = list(dict.fromkeys(heading + slug))[:6]
        source = "h1+url" if slug else ("h1" if h1 else "title")
    else:
        chosen, source = slug, "url"

    order = {t: i for i, t in enumerate(heading)}
    chosen = sorted(chosen, key=lambda t: (order.get(t, 999), chosen.index(t)))
    return {"phrase": " ".join(originals.get(t, t) for t in chosen), "tokens": chosen,
            "source": source}


def coverage(target: list[str], text: str) -> tuple[list[str], list[str]]:
    """(present, missing) target tokens in ``text``, compared on stems."""
    have = set(tokens(text, keep_years=True))
    present = [t for t in target if t in have]
    missing = [t for t in target if t not in have]
    return present, missing


# --------------------------------------------------------------------------- #
# Similarity
# --------------------------------------------------------------------------- #

SHINGLE = 5


def _h(s: str) -> int:
    """Stable 64-bit hash. Python's built-in ``hash()`` is salted per process,
    which would make the same two pages score differently on every run — and a
    similarity figure that changes between runs of an unchanged site is not a
    measurement."""
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def shingles(text: str) -> frozenset[int]:
    """Hashed 5-word shingles of the lowercased text.

    Five words is the standard window for near-duplicate detection: short
    enough that a templated page with the city name swapped still shares most
    of its shingles, long enough that two pages about the same subject in
    different words share almost none.
    """
    toks = [w.lower() for w in words(text)]
    if len(toks) < SHINGLE:
        return frozenset({_h(" ".join(toks))}) if toks else frozenset()
    return frozenset(_h(" ".join(toks[i:i + SHINGLE])) for i in range(len(toks) - SHINGLE + 1))


def jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)
