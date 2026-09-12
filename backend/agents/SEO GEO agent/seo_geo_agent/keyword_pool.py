"""The keyword pool — every keyword this brand is known to touch, in one table.

The agent already knows about keywords in four separate places: the queries
Search Console reports, the seeds configured on the brand, the clusters the
keyword lab builds, and the topics the blog plan proposes. Each view answers its
own question well and none of them answers "what is the full list, and where do
we stand on each one" — which is the question anyone planning a quarter actually
asks.

So this joins them. It invents no data: every row says which source it came
from, and a keyword with no Search Console history is shown with empty metrics
rather than a guess. What it adds on top is one derived number, ``opportunity``,
explained at :func:`_opportunity`.
"""
from __future__ import annotations

from datetime import date

from . import keywords as kw_mod
from . import state
from .insights import ctr_at

_DOC = "kwpool-{}"

#: Rows below this many impressions are real but too small to plan around, and
#: they crowd out the ones worth reading. Kept as data (`thin`) rather than
#: dropped, so the count is still honest.
THIN_IMPRESSIONS = 30


def _opportunity(impressions: int, position: float, clicks: int) -> int:
    """Estimated monthly clicks available from a realistic ranking improvement.

    Not "clicks if we were #1" — that number is fantasy for most keywords and
    makes every list top-heavy with terms nobody will ever win. This models
    moving up a few places from where the page actually sits: to position 3 for
    something already on page one, to position 8 for something on page two, and
    nothing at all past position 30, where the gap is a content problem rather
    than an optimisation one.

    The result is the *additional* clicks over what the keyword earns now, so a
    term already ranking well scores low even with big impressions — which is
    the point: it is already doing its job.
    """
    if impressions <= 0 or position <= 0 or position > 30:
        return 0
    target = 3.0 if position <= 10 else 8.0
    if position <= target:
        return 0
    gain = (ctr_at(target) - ctr_at(position)) * impressions
    return max(0, round(gain - max(0, clicks - impressions * ctr_at(position))))


def _blank_metrics() -> dict:
    return {"clicks": 0, "impressions": 0, "position": 0.0, "ctr": 0.0}


def build(brand: dict, rows: list, *, topics: list[dict] | None = None,
          notes: list[str] | None = None) -> dict:
    """One row per keyword, joined across every place the agent knows one from.

    ``rows`` are ``QueryStat`` records from Search Console for the window the
    caller chose, and ``topics`` the blog plan from the latest run. Clusters are
    read from whatever the keyword lab last persisted. Topics are passed in
    rather than fetched because they live on the run document, not in a store
    of their own — there is no ``topics.latest()`` to call.
    """
    brand_id = brand["id"]
    pool: dict[str, dict] = {}

    def touch(keyword: str, source: str) -> dict:
        key = keyword.strip().lower()
        if not key:
            return {}
        row = pool.setdefault(key, {
            "keyword": key,
            "sources": [],
            "cluster": None,
            "intent": kw_mod.intent_of(key),
            "page": "",
            **_blank_metrics(),
        })
        if source not in row["sources"]:
            row["sources"].append(source)
        return row

    # 1. Search Console — the only source with real numbers attached.
    for stat in rows or []:
        row = touch(stat.query, "search-console")
        if not row:
            continue
        row["clicks"] = stat.clicks
        row["impressions"] = stat.impressions
        row["position"] = round(stat.position, 1)
        row["ctr"] = round(stat.ctr, 4)
        row["page"] = stat.page or row["page"]

    # 2. The brand's configured seeds.
    for seed in brand.get("seeds") or []:
        touch(seed, "seed")

    # 3. Keyword-lab clusters, which also give a keyword its cluster name.
    lab = kw_mod.latest(brand_id) or {}
    for cluster in lab.get("clusters") or []:
        name = cluster.get("cluster") or cluster.get("name") or ""
        for member in cluster.get("keywords") or []:
            row = touch(member, "keyword-lab")
            if row and name:
                row["cluster"] = name

    # 4. Blog-plan topics — keywords we have decided to write for.
    for topic in topics or []:
        row = touch(topic.get("keyword") or "", "blog-plan")
        if row and topic.get("cluster"):
            row["cluster"] = row["cluster"] or topic["cluster"]

    for row in pool.values():
        row["opportunity"] = _opportunity(row["impressions"], row["position"], row["clicks"])
        row["thin"] = row["impressions"] < THIN_IMPRESSIONS
        row["ranked"] = row["position"] > 0
        row["band"] = (
            "unranked" if not row["ranked"]
            else "top3" if row["position"] <= 3
            else "page1" if row["position"] <= 10
            else "page2" if row["position"] <= 20
            else "beyond"
        )

    entries = sorted(
        pool.values(),
        key=lambda r: (-r["opportunity"], -r["impressions"], r["keyword"]),
    )
    bands = {b: 0 for b in ("top3", "page1", "page2", "beyond", "unranked")}
    for row in entries:
        bands[row["band"]] += 1

    doc = {
        "at": date.today().isoformat(),
        "keywords": entries,
        "totals": {
            "keywords": len(entries),
            "ranked": sum(1 for r in entries if r["ranked"]),
            "thin": sum(1 for r in entries if r["thin"]),
            "clicks": sum(r["clicks"] for r in entries),
            "impressions": sum(r["impressions"] for r in entries),
            "opportunity": sum(r["opportunity"] for r in entries),
        },
        "bands": bands,
        "clusters": sorted({r["cluster"] for r in entries if r["cluster"]}),
        "notes": list(notes or []),
    }
    state.save(_DOC.format(brand_id), doc)
    return doc


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))
