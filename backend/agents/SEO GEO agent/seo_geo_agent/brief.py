"""The employee's daily brief — five blocks, every line deterministic.

Current rank → what is not working → what is working → immediate fixes →
secondary fixes. Built entirely from the digest layer (``digests.py``), so it
is pure local reads: no network, no LLM, nothing to hallucinate. Every line
carries a ``link`` — the console section where the evidence lives — using the
same section ids the sidebar routes (``sections.ts``'s ``resolveSection``
maps them across the two workspaces).

The split between *immediate* and *secondary* is the tiering this agent keeps
arriving at from the data:

* **Immediate** — unblocking (indexation contradictions: a page Google cannot
  read earns nothing), live rank regressions, CTR gaps (no ranking change
  needed), and template faults (one fix clears every page at once).
* **Secondary** — compounding content work: striking-distance refreshes,
  cannibalization merges, density, decay.
"""
from __future__ import annotations

from datetime import date

from . import digests

MAX_LINES = 8


def _line(text: str, link: str) -> dict:
    return {"text": text, "link": link}


def build(brand: dict) -> dict:
    brand_id = brand["id"]
    rank = digests.rank_digest(brand)
    deep = digests.deep_digest(brand_id)
    traffic = digests.traffic_digest(brand_id)

    working: list[dict] = []
    not_working: list[dict] = []
    immediate: list[dict] = []
    secondary: list[dict] = []
    notes: list[str] = []

    # ------------------------------ current rank ------------------------------
    current_rank = None
    if rank:
        current_rank = {
            "at": rank.get("at"),
            "tracked": rank["tracked"], "top3": rank["top3"], "page1": rank["page1"],
            "striking": rank["striking_4_20"], "unranked": rank["unranked"],
            "moved_up": len(rank["movers_up"]), "moved_down": len(rank["movers_down"]),
            "dropouts": len(rank["dropouts_7d"]),
            "best": rank["best"][:3],
        }
    else:
        notes.append("Rank tracking: no sweep yet — run one from the Rank tracker")

    # ---------------------------- what is not working ----------------------------
    if rank:
        for q in rank["dropouts_7d"]:
            not_working.append(_line(f"'{q}' dropped out of the results this week — it ranked recently.",
                                     "rank-board"))
        for m in rank["movers_down"]:
            where = f" — {m['leader']} is above at #{m['leader_position']}" if m.get("leader") else ""
            not_working.append(_line(f"'{m['query']}' fell #{m['from']}→#{m['to']}{where}.", "rank-board"))
        for p in rank["pressure"][:1]:
            if p["above_us_on"] >= 3:
                not_working.append(_line(
                    f"{p['domain']} outranks us on {p['above_us_on']} tracked queries — "
                    "the biggest single competitive overlap.", "rank-board"))

    cwv = (deep or {}).get("core_web_vitals_field") or {}
    for form_factor, assessment in cwv.items():
        if assessment == "failing":
            not_working.append(_line(f"Core Web Vitals are failing on {form_factor} — "
                                     "real visitors get slow/unstable pages, a ranking signal.", "vitals"))
        elif assessment == "needs-improvement":
            not_working.append(_line(f"Core Web Vitals need improvement on {form_factor}.", "vitals"))
        elif assessment in ("passing", "good"):
            working.append(_line(f"Core Web Vitals pass on {form_factor} — real-visitor experience is healthy.",
                                 "vitals"))

    if deep:
        sitemap = deep.get("sitemap") or {}
        if (sitemap.get("score") or 100) < 60:
            not_working.append(_line(
                f"Sitemap health is {sitemap['score']}/100 with {sitemap.get('affected', 0)} affected URLs — "
                "contradictory signals can keep pages out of the index.", "sitemap"))
        landing = deep.get("landing") or {}
        if landing.get("high"):
            not_working.append(_line(
                f"{landing['high']} high-severity landing-page finding(s) — "
                "these directly block pages from ranking.", "landing"))
        cann = deep.get("cannibalization") or {}
        if cann.get("confirmed"):
            not_working.append(_line(
                f"{cann['confirmed']} cannibalization case(s) confirmed by Search Console — "
                "your own pages are splitting one query's clicks.", "cannibal"))
    else:
        notes.append("Deep audit: not run yet — the technical blocks are blind until it runs")

    summary = (traffic or {}).get("summary") or {}
    clicks, prev = summary.get("clicks_28d"), summary.get("clicks_prev_28d")
    if clicks is not None and prev:
        if clicks < prev * 0.9:
            not_working.append(_line(
                f"Organic clicks fell {prev:,} → {clicks:,} over 28 days.", "traffic"))
        elif clicks > prev * 1.1:
            working.append(_line(f"Organic clicks grew {prev:,} → {clicks:,} over 28 days.", "traffic"))

    # ------------------------------ what is working ------------------------------
    if rank:
        if rank["top3"]:
            examples = ", ".join(f"'{b['query']}' #{b['position']}" for b in rank["best"][:3])
            working.append(_line(f"{rank['top3']} tracked queries sit in the top 3 ({examples}).",
                                 "rank-board"))
        for m in rank["movers_up"][:2]:
            working.append(_line(f"'{m['query']}' climbed #{m['from']}→#{m['to']} — "
                                 "whatever changed there is working.", "rank-board"))
    if deep:
        density = deep.get("blog_keyword_density") or {}
        posts = density.get("posts") or 0
        if posts and (density.get("pass") or 0) / posts > 0.7:
            working.append(_line(f"{density['pass']} of {posts} blog articles hold their focus keyword "
                                 "through the whole text.", "density"))
        landing = deep.get("landing") or {}
        grades = landing.get("grades") or {}
        strong = (grades.get("A") or 0) + (grades.get("B") or 0)
        if landing.get("pages") and strong / landing["pages"] > 0.5:
            working.append(_line(f"{strong} of {landing['pages']} landing pages grade A or B on-page.",
                                 "landing"))

    # ------------------------------ immediate fixes ------------------------------
    if deep:
        for fault in (deep.get("template_faults_fix_once") or [])[:3]:
            immediate.append(_line(
                f"Template fault: “{fault['check']}” fails on {fault['pages']} pages — "
                "one fix in the template clears them all.", "landing"))
    for todo in (traffic or {}).get("top_fixes") or []:
        if todo.get("status") == "done":
            continue
        if todo.get("kind") == "ctr_gap":
            gain = todo.get("est_monthly_clicks")
            immediate.append(_line(
                f"{todo['action']} — snippet fix, no ranking change needed"
                + (f" (≈+{gain:,} clicks/mo)." if gain else "."), "traffic"))
        elif todo.get("kind") in ("rank_drop", "decay"):
            immediate.append(_line(f"{todo['action']}.", "traffic"))
    if rank:
        for m in rank["movers_down"][:2]:
            immediate.append(_line(
                f"Investigate '{m['query']}' ({m['from']}→#{m['to']})"
                + (f" — read what {m['leader']} does better." if m.get("leader") else "."),
                "rank-board"))

    # ------------------------------ secondary fixes ------------------------------
    for todo in (traffic or {}).get("top_fixes") or []:
        if todo.get("status") == "done":
            continue
        if todo.get("kind") in ("striking", "unranked"):
            gain = todo.get("est_monthly_clicks")
            secondary.append(_line(
                f"{todo['action']}" + (f" (≈+{gain:,} clicks/mo)." if gain else "."), "traffic"))
    if deep:
        cann = deep.get("cannibalization") or {}
        if cann.get("pairs"):
            secondary.append(_line(
                f"Resolve {cann['pairs']} page pair(s) competing for the same query — "
                "merge or differentiate.", "cannibal"))
        density = deep.get("blog_keyword_density") or {}
        if density.get("fail"):
            secondary.append(_line(
                f"{density['fail']} blog article(s) drift off their focus keyword — "
                "work them back in, naturally.", "density"))
    for topic in ((traffic or {}).get("top_blog_topics") or [])[:2]:
        if topic.get("priority") == "high":
            secondary.append(_line(f"Write for '{topic['keyword']}' — high-priority topic.", "keywords"))

    return {
        "brand_id": brand_id,
        "at": date.today().isoformat(),
        "current_rank": current_rank,
        "working": working[:MAX_LINES],
        "not_working": not_working[:MAX_LINES],
        "immediate": immediate[:MAX_LINES],
        "secondary": secondary[:MAX_LINES],
        "notes": notes,
    }
