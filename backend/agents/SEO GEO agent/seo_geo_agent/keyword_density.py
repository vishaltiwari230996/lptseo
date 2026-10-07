"""Blog keyword density — one focus-keyword mention per 150 words.

The rule, as set for this site: in every blog article, the focus keyword should
appear at least once per 150 words.

It is measured two ways, because "once per 150 words" means two different
things and they fail differently:

* **On average** — the article has at least ``ceil(words / 150)`` mentions.
  Easy to satisfy by stuffing every mention into the introduction, which is
  why it cannot be the only test.
* **In every stretch** — the article is cut into consecutive 150-word windows
  and each window must contain a mention. This is the literal rule, and it is
  the one that catches a 2,000-word post that says the keyword six times in its
  first paragraph and never again. Every window that fails is reported with its
  position and its opening words, so a writer can go straight to it.

What is counted, precisely:

* **Words** — the article's main content only, extracted with trafilatura:
  navigation, header, footer, sidebars, related-post widgets and comments are
  excluded. Counting template words would inflate the total and make every
  article look like it needs more mentions than it does. Hyphenated compounds
  count as one word, as in any word counter.
* **A mention** — the focus keyword as a phrase, in order, case-insensitive,
  with plurals matched (``college`` / ``colleges``), hyphens equal to spaces,
  and up to two function words allowed between keyword words (so "law
  colleges *in* Karnataka" counts for "law colleges karnataka"). Close variants
  in a different word order ("Karnataka law colleges") are counted too, and
  reported separately. Synonyms are NOT counted — a synonym is a different
  keyword.
* **The focus keyword** — Search Console's top query for the post when
  connected; otherwise extracted from the post's title, H1 and URL by the
  language model, and checked against them (a keyword whose words do not
  appear in the title, H1 or URL is rejected as a hallucination and replaced by
  the URL-derived keyword). The source is recorded on every row.

One guardrail is added on top of the rule, and labelled as ours: more than 3%
density, or four mentions inside one 150-word window, reads as keyword stuffing
to readers and to Google's spam systems. The rule sets a floor; this marks a
ceiling.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from statistics import median

from . import jobs, keyphrase as kp, state
from .crawl import norm_url, words

_DOC = "density-{}"
_POSTS = "densityposts-{}"

WORDS_PER_MENTION = 150
#: A final partial window shorter than this is merged into the previous one
#: rather than being required to contain its own mention.
MIN_TAIL = WORDS_PER_MENTION // 2
MAX_GAP_FUNCTION_WORDS = 2
#: Density is occurrences / words * 100 — the convention Yoast and most SEO tools
#: report, so the number means what an SEO expects it to mean. (It was
#: previously multiplied by the keyword's length, which is a different metric
#: that flagged 431 of 789 posts as "stuffed".)
STUFFING_DENSITY = 3.0
#: Six mentions inside one 150-word stretch (4% locally) reads as repetition.
#: Four did not: four mentions of a head term like "CLAT" in one paragraph is
#: ordinary writing on an exam-prep site.
STUFFING_PER_WINDOW = 6

#: Bump when the focus-keyword instruction changes, so remembered keywords
#: chosen under the old instruction are re-chosen rather than reused.
KEYWORD_RULES = 2

LLM_BATCH = 30

#: A remembered focus keyword survives this long after its post was last seen.
MEMO_KEEP_DAYS = 60


def find_mentions(text: str, focus: list[str]) -> tuple[list[int], list[int], int]:
    """(exact_starts, variant_starts, word_total) — word indices where the focus
    keyword begins, over the same word list ``words()`` produces."""
    # Two different notions of "word", deliberately. For COUNTING, a hyphenated
    # compound is one word ("law-colleges"), as in any word counter — that is
    # what the 150 is measured in. For MATCHING, it has to be split, or the
    # keyword "law colleges" could never match "law-colleges". So matching runs
    # over hyphen-split parts, and each part remembers which counted word it came
    # from; a mention is reported at its word's position, never its part's.
    word_list = words(text)
    n = len(word_list)
    toks: list[str] = []
    parent: list[int] = []
    for wi, w in enumerate(word_list):
        w = re.sub(r"['’]s$", "", w.lower())  # possessive: "aspirant's" -> "aspirant"
        for part in w.split("-"):
            if part:
                toks.append(part)
                parent.append(wi)
    stems = [kp.stem(t) for t in toks]
    k = len(focus)
    if not k or not n:
        return [], [], n
    fset = set(focus)
    exact: list[int] = []
    variant: list[int] = []
    n_parts = len(toks)
    taken = [False] * n_parts

    # Exact, in order, allowing a couple of function words between.
    i = 0
    while i < n_parts:
        if stems[i] != focus[0]:
            i += 1
            continue
        j, matched, gaps = i + 1, 1, 0
        while matched < k and j < n_parts:
            if stems[j] == focus[matched]:
                matched += 1
                j += 1
            elif toks[j] in kp.STOP and gaps < MAX_GAP_FUNCTION_WORDS:
                gaps += 1
                j += 1
            else:
                break
        if matched == k:
            exact.append(parent[i])
            for x in range(i, j):
                taken[x] = True
            i = j
        else:
            i += 1

    # Close variants: every keyword word inside a short window, any order.
    if k > 1:
        span = k + MAX_GAP_FUNCTION_WORDS + 1
        i = 0
        while i < n_parts:
            if taken[i] or stems[i] not in fset:
                i += 1
                continue
            window = stems[i:i + span]
            if fset.issubset(window) and not any(taken[i:i + span]):
                variant.append(parent[i])
                for x in range(i, min(n_parts, i + span)):
                    taken[x] = True
                i += span
            else:
                i += 1
    return exact, variant, n


def windows(n_words: int) -> list[tuple[int, int]]:
    """Consecutive [start, end) windows of 150 words; a short tail joins the last."""
    if n_words <= 0:
        return []
    out = [(s, min(s + WORDS_PER_MENTION, n_words)) for s in range(0, n_words, WORDS_PER_MENTION)]
    if len(out) > 1 and out[-1][1] - out[-1][0] < MIN_TAIL:
        tail = out.pop()
        out[-1] = (out[-1][0], tail[1])
    return out


def analyse(record: dict, text: str, focus_phrase: str, source: str) -> dict:
    focus = kp.tokens(focus_phrase, keep_years=True)
    exact, variant, n = find_mentions(text, focus)
    starts = sorted(exact + variant)
    total = len(starts)
    toks = words(text)
    wins = windows(n)
    per_window = [sum(1 for s in starts if a <= s < b) for a, b in wins]
    gaps = [
        {
            "window": idx + 1,
            "words": f"{a + 1}–{b}",
            "starts_with": " ".join(toks[a:a + 14]) + (" …" if b - a > 14 else ""),
        }
        for idx, ((a, b), c) in enumerate(zip(wins, per_window)) if c == 0
    ]
    required = math.ceil(n / WORDS_PER_MENTION) if n else 0
    density = round(total / n * 100, 2) if n else 0.0
    covered = sum(1 for c in per_window if c > 0)

    headings = [h["text"] for h in record.get("headings") or [] if h["level"] in (2, 3)]
    slug = record.get("final_url") or record["url"]

    def has(t: str) -> bool:
        return bool(focus) and not kp.coverage(focus, t)[1]

    placement = {
        "title": has(record.get("title", "")),
        "h1": has(" ".join(record.get("h1") or [])),
        "url": has(slug.replace("/", " ")),
        "meta_description": has(record.get("meta_description", "")),
        "first_100_words": has(" ".join(toks[:100])),
        "a_subheading": any(has(h) for h in headings),
        "image_alt": any(has(a) for a in record.get("img_alts") or []),
        "last_100_words": has(" ".join(toks[-100:])),
    }

    pass_avg = total >= required and n > 0
    pass_win = n > 0 and covered == len(wins)
    stuffing = density > STUFFING_DENSITY or (max(per_window) if per_window else 0) >= STUFFING_PER_WINDOW
    verdict = ("pass" if pass_win else
               "pass-on-average" if pass_avg else
               "fail")
    return {
        "url": record["url"],
        "title": record.get("title", ""),
        # Shown as written, not as the stemmed tokens matching uses — "mother's
        # day question answer", not "day question answer mother".
        "focus_keyword": " ".join(focus_phrase.lower().split()),
        "keyword_source": source,
        "words": n,
        "mentions": total,
        "mentions_exact": len(exact),
        "mentions_variant": len(variant),
        "required": required,
        "shortfall": max(0, required - total),
        "words_per_mention": round(n / total) if total else None,
        "density_pct": density,
        "windows": len(wins),
        "windows_covered": covered,
        "coverage_pct": round(100 * covered / len(wins)) if wins else 0,
        "gaps": gaps,
        "per_window": per_window,
        "max_in_window": max(per_window) if per_window else 0,
        "stuffing": stuffing,
        "pass_average": pass_avg,
        "pass_every_window": pass_win,
        "verdict": verdict,
        "placement": placement,
        "placement_missing": [k for k, v in placement.items() if not v],
    }


# --------------------------------------------------------------------------- #
# Focus keywords
# --------------------------------------------------------------------------- #

_SYSTEM = (
    "You are a senior SEO. For each blog article you are given its URL path, title and H1. "
    "Return its focus keyword: the core TOPIC PHRASE the article must keep repeating to rank "
    "— a noun phrase of 2 to 4 words, lowercase, no brand, no year unless the year is the "
    "subject, no punctuation. Never start with a question word or an action verb: for "
    "'How to Prepare for UP Judiciary Exam' answer 'up judiciary exam', not 'prepare up "
    "judiciary exam'; for 'What is CLAT? Eligibility Explained' answer 'clat eligibility'. "
    "Use only words that appear in the title, H1 or URL. "
    'Reply with JSON only: {"items":[{"i":<int>,"keyword":"<keyword>"}]}'
)

#: Leading words that make a keyword a sentence rather than a topic. Stripped
#: defensively in case the model ignores the instruction above.
_LEADING = ("how to ", "what is ", "what are ", "why ", "when ", "guide to ", "prepare for ",
            "preparing for ", "prepare ", "crack ", "check ", "download ")


def _clean_keyword(kw: str) -> str:
    kw = " ".join(kw.lower().split())
    changed = True
    while changed:
        changed = False
        for lead in _LEADING:
            if kw.startswith(lead) and len(kw) > len(lead) + 2:
                kw = kw[len(lead):]
                changed = True
    return kw


def _grounded(keyword: str, record: dict) -> bool:
    """Reject a keyword whose words mostly do not appear in the title, H1 or URL.
    A focus keyword the article never states is a guess, and a density measured
    against a guess is meaningless."""
    toks = kp.tokens(keyword, keep_years=True)
    if not toks:
        return False
    source_text = " ".join([record.get("title", ""), " ".join(record.get("h1") or []),
                            (record.get("final_url") or record["url"]).replace("/", " ")])
    present, _ = kp.coverage(toks, source_text)
    return len(present) >= math.ceil(len(toks) * 0.75)


def _fingerprint(r: dict) -> str:
    """What a focus keyword was decided from. When this is unchanged, so is the
    keyword — re-asking the model would only let the answer drift."""
    import hashlib

    basis = "|".join([f"rules{KEYWORD_RULES}", r.get("title", ""), " ".join(r.get("h1") or []),
                      r.get("final_url") or r["url"]])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def focus_keywords(posts: list[dict], brand_name: str, gsc_top: dict[str, str],
                   *, on_batch=None, brand_id: str | None = None) -> dict[str, tuple[str, str]]:
    """{url: (keyword, source)} for every post.

    Keywords the model chose are remembered per post, keyed on the title, H1
    and URL they were chosen from, and reused on the next audit while those are
    unchanged. Without this the model — even at low temperature — names a
    slightly different keyword for a few posts on every run, and the density
    report for a site nobody touched moves between runs. An audit that gives a
    different answer for the same site is not measuring the site.
    """
    from .sources import CredentialMissing, llm_json

    memo_key = f"focuskw-{brand_id}" if brand_id else None
    memo: dict = (state.load(memo_key) or {}) if memo_key else {}
    out: dict[str, tuple[str, str]] = {}
    pending: list[dict] = []
    for r in posts:
        top = gsc_top.get(norm_url(r["url"]))
        remembered = memo.get(r["url"])
        if top:
            out[r["url"]] = (top, "search-console")
        elif remembered and remembered.get("fp") == _fingerprint(r):
            out[r["url"]] = (remembered["keyword"], remembered["source"])
        else:
            pending.append(r)
    if on_batch and len(posts) > len(pending):
        on_batch(len(posts) - len(pending))

    for b in range(0, len(pending), LLM_BATCH):
        batch = pending[b:b + LLM_BATCH]
        lines = []
        for i, r in enumerate(batch):
            path = (r.get("final_url") or r["url"]).split("//", 1)[-1].split("/", 1)[-1]
            h1 = (r.get("h1") or [""])[0]
            lines.append(f'{i}. path="/{path}" title="{r.get("title", "")}" h1="{h1}"')
        got: dict[int, str] = {}
        try:
            reply = llm_json(_SYSTEM, "\n".join(lines), bulk=True)  # 30 posts/batch: bulk tier
            for item in (reply or {}).get("items", []):
                if isinstance(item, dict) and isinstance(item.get("i"), int) and item.get("keyword"):
                    got[item["i"]] = _clean_keyword(str(item["keyword"]))
        except CredentialMissing:
            got = {}
        for i, r in enumerate(batch):
            kw = got.get(i, "")
            if kw and _grounded(kw, r):
                out[r["url"]] = (kw, "ai (checked against title/H1/URL)")
                # Only a model answer is worth remembering; a fallback is
                # deterministic already, and remembering it would stop a later
                # run with a working model from ever improving on it.
                memo[r["url"]] = {"keyword": kw, "source": out[r["url"]][1], "fp": _fingerprint(r)}
            else:
                fallback = kp.target_keyword(r, brand_name)
                out[r["url"]] = (fallback["phrase"], f"derived from {fallback['source']}")
        if on_batch:
            on_batch(len(batch))
    if memo_key:
        # Forget a keyword only when its post has been gone for MEMO_KEEP_DAYS —
        # not merely absent from this crawl. Keeping just the posts seen this
        # time meant one partial crawl erased every remembered choice, and the
        # model then re-chose them all, differently.
        today = date.today()
        for r in posts:
            if r["url"] in memo:
                memo[r["url"]]["seen"] = today.isoformat()
        cutoff = (today - timedelta(days=MEMO_KEEP_DAYS)).isoformat()
        state.save(memo_key, {u: v for u, v in memo.items() if v.get("seen", today.isoformat()) >= cutoff})
    return out


def build(brand: dict, records: list[dict], texts: dict[str, str], *,
          gsc_top: dict[str, str] | None = None, progress=None) -> dict:
    posts = [r for r in records if r.get("type") == "blog_post" and r.get("status") == 200
             and texts.get(norm_url(r["url"]))]
    keywords = focus_keywords(posts, brand.get("name", ""), gsc_top or {},
                              on_batch=(progress.step if progress else None), brand_id=brand["id"])
    results = []
    for r in posts:
        kw, src = keywords[r["url"]]
        results.append(analyse(r, texts[norm_url(r["url"])], kw, src))

    n = len(results) or 1
    wpm = [x["words_per_mention"] for x in results if x["words_per_mention"]]
    buckets = {"≤150 (meets rule)": 0, "151–300": 0, "301–600": 0, ">600": 0, "never mentioned": 0}
    for x in results:
        w = x["words_per_mention"]
        if w is None:
            buckets["never mentioned"] += 1
        elif w <= 150:
            buckets["≤150 (meets rule)"] += 1
        elif w <= 300:
            buckets["151–300"] += 1
        elif w <= 600:
            buckets["301–600"] += 1
        else:
            buckets[">600"] += 1
    placement_fail = {}
    for x in results:
        for k in x["placement_missing"]:
            placement_fail[k] = placement_fail.get(k, 0) + 1

    results.sort(key=lambda x: (x["pass_every_window"], x["coverage_pct"], -x["words"]))
    doc = {
        "at": date.today().isoformat(),
        "rule": f"at least one focus-keyword mention per {WORDS_PER_MENTION} words",
        "posts": len(results),
        "pass_every_window": sum(1 for x in results if x["pass_every_window"]),
        "pass_on_average_only": sum(1 for x in results if x["pass_average"] and not x["pass_every_window"]),
        "fail": sum(1 for x in results if not x["pass_average"]),
        "stuffing": sum(1 for x in results if x["stuffing"]),
        "median_words_per_mention": round(median(wpm)) if wpm else None,
        "median_coverage_pct": round(median([x["coverage_pct"] for x in results])) if results else 0,
        "total_missing_mentions": sum(x["shortfall"] for x in results),
        "buckets": buckets,
        "placement_missing": dict(sorted(placement_fail.items(), key=lambda kv: -kv[1])),
        "keyword_sources": {s: sum(1 for x in results if x["keyword_source"] == s)
                            for s in {x["keyword_source"] for x in results}},
        "method": {
            "words": "main content only (trafilatura) — nav, header, footer, sidebars and comments excluded",
            "mention": "focus keyword in order (plurals matched, hyphen = space, up to 2 function "
                       "words between), plus close variants in any order; synonyms not counted",
            "every_window": f"article cut into {WORDS_PER_MENTION}-word windows; each must contain a "
                            f"mention; a final window under {MIN_TAIL} words joins the previous one",
            "stuffing_guardrail": f"ours, not the rule: over {STUFFING_DENSITY}% density or "
                                  f"{STUFFING_PER_WINDOW}+ mentions in one window",
        },
    }
    state.save(_DOC.format(brand["id"]), doc)
    jobs.save_list(_POSTS.format(brand["id"]), results)
    return doc


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))


def latest_posts(brand_id: str) -> list[dict]:
    return jobs.load_list(_POSTS.format(brand_id))[0]
