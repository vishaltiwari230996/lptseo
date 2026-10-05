"""The rank tracker against a REAL Firestore, not the local JSON fallback.

Every other test in this suite runs under ``SEO_OFFLINE=1``, where ``state``
writes JSON files that accept any shape Python can serialise. Firestore does
not: it rejects an array whose elements are themselves arrays outright
(``InvalidArgument 400 Property array contains an invalid nested entity``).
The history document was written as ``raw: [[hour, position], …]``, so in
production every sweep charged its full ~200-search Serper bill, saved
``rank-latest``, and then died before one history document landed — while 70
offline tests passed. That gap is what this file closes.

Run it by starting an emulator and exporting its address::

    gcloud emulators firestore start --host-port=localhost:8080
    # or, with no gcloud component installed:
    java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-*.jar \
        --host=localhost --port=8080

    export FIRESTORE_EMULATOR_HOST=localhost:8080
    python -m pytest -m seo -k firestore -q

Without ``FIRESTORE_EMULATOR_HOST`` the whole module skips, so the ordinary
offline run is unaffected. It must never fall back to offline mode silently —
a green run that proved nothing is exactly the failure being fixed here.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest

from seo_geo_agent import rank_tracker as rt
from seo_geo_agent import state

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs a Firestore emulator; set FIRESTORE_EMULATOR_HOST to run",
)

#: Documents this module writes, as `{}`-templates taking the brand id. Used
#: both to assert everything landed and to clean up afterwards.
_DOC_TEMPLATES = (
    rt.POOL_DOC, rt.BUDGET_DOC, rt.HARVEST_DOC, rt.ROLLUP_DOC, rt.SWEEP_DOC,
    rt.LATEST_PREFIX + "-meta", rt.LATEST_PREFIX + "-0",
    rt.HISTORY_PREFIX + "-meta", rt.HISTORY_PREFIX + "-0",
    rt.SWEEP_LOCK_DOC, rt.HISTORY_LOCK_DOC,
)


@pytest.fixture()
def cloud(monkeypatch):
    """Point `state` at the emulator and hand back a throwaway brand id.

    The repo-root ``_no_prod_firestore`` fixture replaces ``firestore_repo._db``
    with a raise; this is the escape hatch that fixture documents. Pointing a
    real client at ``FIRESTORE_EMULATOR_HOST`` reaches the emulator and
    nothing else — ``google-cloud-firestore`` honours that variable by
    skipping auth and dialling the named host.
    """
    from google.cloud import firestore

    from app.services import firestore_repo

    client = firestore.Client(project="rank-tracker-emulator-test", database="(default)")
    monkeypatch.setattr(firestore_repo, "_db", lambda: client)
    monkeypatch.setenv("SEO_OFFLINE", "0")
    assert state.use_cloud(), "this module must not silently run offline"

    brand_id = f"emu{uuid.uuid4().hex[:10]}"
    yield brand_id

    for template in _DOC_TEMPLATES:
        try:
            state.delete(template.format(brand_id))
        except Exception:  # noqa: BLE001 — cleanup must not mask a real failure
            pass


def _brand(brand_id: str, **over) -> dict:
    brand = {"id": brand_id, "name": "Law Prep Tutorial",
             "domain": "lawpreptutorial.com", "competitors": ["rival.com"]}
    brand.update(over)
    return brand


def _serp(query: str) -> dict:
    return {"organic": [{"position": 1, "link": "https://rival.com/a", "title": "r"},
                        {"position": 4, "link": "https://lawpreptutorial.com/x", "title": "u"}],
            "related": [f"{query} fees"], "paa": [], "aio_present": False}


def _seed_pool(brand_id: str, *queries: str) -> None:
    state.save(rt.POOL_DOC.format(brand_id), {
        "queries": [{"query": q, "source": "custom", "impressions": 0,
                     "added_at": "2026-10-01T00:00:00", "active": True} for q in queries],
        "built_at": "2026-10-05T00:00:00", "sources_used": ["custom"], "notes": [],
    })


def test_a_full_sweep_lands_every_document_in_real_firestore(cloud):
    """The C1 regression. Before the map encoding this raised InvalidArgument
    400 inside append_history, after rank-latest and the budget charge had
    already been written — paid for, and nothing to show for it."""
    brand_id = cloud
    _seed_pool(brand_id, "clat coaching", "clat syllabus")

    out = rt.sweep(_brand(brand_id), search=_serp,
                   now=datetime(2026, 10, 5, 9, tzinfo=timezone.utc))

    assert out["blocked"] is None
    assert out["checked"] == 2 and out["ranked"] == 2 and out["errors"] == 0

    for template in (rt.LATEST_PREFIX + "-meta", rt.LATEST_PREFIX + "-0",
                     rt.HISTORY_PREFIX + "-meta", rt.HISTORY_PREFIX + "-0",
                     rt.BUDGET_DOC, rt.ROLLUP_DOC, rt.SWEEP_DOC):
        assert state.load(template.format(brand_id)) is not None, \
            f"{template.format(brand_id)} was not written"

    # And it reads back as history, which is the whole point of writing it.
    row = rt.history_for(brand_id, "clat coaching")
    assert row is not None
    assert row["raw"] == [{"h": rt._epoch_hours(datetime(2026, 10, 5, 9, tzinfo=timezone.utc)),
                           "p": 4}]
    assert row["rivals"]["rival.com"]["raw"][0]["p"] == 1


def test_rollup_writes_daily_points_to_real_firestore(cloud):
    """`daily` was `[[date, {...}], …]` — the same nested-array rejection, on
    the branch that only runs once a week, which is why no amount of manual
    poking at a fresh deploy would have found it either."""
    brand_id = cloud
    rt.append_history(brand_id, [{"query": "q", "position": 7, "top": [], "error": None}],
                      [], now=datetime(2026, 10, 1, 9, tzinfo=timezone.utc))

    written = rt.rollup(brand_id, today=date(2026, 10, 20))

    assert written == 1
    row = rt.history_for(brand_id, "q")
    assert row["raw"] == []
    assert row["daily"] == [{"d": "2026-10-01", "best": 7, "worst": 7, "last": 7,
                             "at": rt._epoch_hours(datetime(2026, 10, 1, 9, tzinfo=timezone.utc))}]


def test_the_history_lease_is_released_so_a_second_sweep_can_run(cloud):
    """Two sweeps in a row must both write. A lease that leaked would make the
    second one hang until its TTL and then interleave anyway."""
    brand_id = cloud
    _seed_pool(brand_id, "clat coaching")

    first = rt.sweep(_brand(brand_id), search=_serp,
                     now=datetime(2026, 10, 5, 9, tzinfo=timezone.utc))
    second = rt.sweep(_brand(brand_id), search=_serp,
                      now=datetime(2026, 10, 5, 11, tzinfo=timezone.utc))

    assert first["blocked"] is None and second["blocked"] is None
    assert [p["p"] for p in rt.history_for(brand_id, "clat coaching")["raw"]] == [4, 4]
    assert state.load(rt.SWEEP_LOCK_DOC.format(brand_id)) in (None, {})
