"""Rank tracker routes — auth, cron contract, and the panel payload."""
from __future__ import annotations

import pathlib as _pathlib
import sys as _sys

_BACKEND_ROOT = _pathlib.Path(__file__).resolve().parents[4]
if str(_BACKEND_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_BACKEND_ROOT))

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import get_current_user, require_creator


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "creator"}
    app.dependency_overrides[require_creator] = lambda: {"id": "u1", "role": "creator"}
    from seo_geo_agent import insights
    monkeypatch.setattr(insights, "list_brands", lambda: [
        {"id": "b1", "name": "Law Prep Tutorial", "domain": "lawpreptutorial.com",
         "seeds": [], "competitors": [], "enabled": True},
    ])
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_overview_route_returns_pool_budget_and_worklist(client):
    body = client.get("/api/seo-geo/rank-tracker/b1").json()
    assert set(body) >= {"rows", "worklist", "pool", "budget", "job", "meta"}
    assert body["budget"]["cap"] == 3000


def test_overview_404s_for_an_unknown_brand(client):
    assert client.get("/api/seo-geo/rank-tracker/nope").status_code == 404


def test_cron_is_503_until_the_key_is_configured(client, monkeypatch):
    monkeypatch.delenv("SEO_CRON_KEY", raising=False)
    assert client.post("/api/seo-geo/rank-tracker/cron").status_code == 503


def test_cron_rejects_a_wrong_key(client, monkeypatch):
    monkeypatch.setenv("SEO_CRON_KEY", "right")
    resp = client.post("/api/seo-geo/rank-tracker/cron", headers={"x-cron-key": "wrong"})
    assert resp.status_code == 403


def test_cron_runs_every_enabled_brand_and_reports_ok(client, monkeypatch):
    monkeypatch.setenv("SEO_CRON_KEY", "right")
    from seo_geo_agent import rank_tracker
    monkeypatch.setattr(rank_tracker, "build_pool", lambda brand, rows_fn=None: {"queries": []})
    monkeypatch.setattr(rank_tracker, "sweep",
                        lambda brand, progress=None, search=None, now=None:
                        {"checked": 3, "ranked": 3, "errors": 0, "blocked": None,
                         "at": "2026-10-05T09:00:00+00:00", "notes": []})

    resp = client.post("/api/seo-geo/rank-tracker/cron", headers={"x-cron-key": "right"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_cron_is_502_when_every_brand_fails(client, monkeypatch):
    monkeypatch.setenv("SEO_CRON_KEY", "right")
    from seo_geo_agent import rank_tracker

    def boom(*a, **kw):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(rank_tracker, "build_pool", boom)
    monkeypatch.setattr(rank_tracker, "sweep", boom)

    resp = client.post("/api/seo-geo/rank-tracker/cron", headers={"x-cron-key": "right"})
    assert resp.status_code == 502


def test_history_route_404s_for_an_unswept_query(client):
    assert client.get("/api/seo-geo/rank-tracker/b1/history?query=nothing").status_code == 404


def test_gap_route_404s_for_an_unswept_query(client):
    resp = client.post("/api/seo-geo/rank-tracker/b1/gap", json={"query": "nothing"})
    assert resp.status_code == 404


# ------------------------- auth boundary: credit-spending routes -------------------------
#
# The `client` fixture above overrides both get_current_user AND require_creator
# to the same creator identity, so nothing above exercises require_creator's own
# body. If /sweep or /pool/rebuild were ever relaxed from require_creator down to
# get_current_user, every test above would keep passing. This test wires
# require_creator back to its real implementation (app/security.py:
# `require_creator(user=Depends(get_current_user))`, which 403s unless
# `user["is_creator"]` is true) and overrides only get_current_user with a
# signed-in, non-creator identity shaped the way get_current_user actually
# returns it (id/email/is_admin/is_creator/is_geo_editor/session_id/timezone) —
# not guessed.

def test_non_creator_is_blocked_from_credit_spending_routes_but_can_still_read(client):
    app.dependency_overrides.pop(require_creator, None)  # run the real guard
    non_creator = {
        "id": "u2", "email": "noncreator@example.com",
        "is_admin": False, "is_creator": False, "is_geo_editor": False,
        "session_id": "s2", "timezone": "UTC",
    }
    app.dependency_overrides[get_current_user] = lambda: non_creator

    assert client.post("/api/seo-geo/rank-tracker/b1/sweep").status_code == 403
    assert client.post("/api/seo-geo/rank-tracker/b1/pool/rebuild").status_code == 403

    # Same identity, the two reads: the boundary is the two credit-spending
    # POSTs specifically, not "no non-creator request succeeds".
    assert client.get("/api/seo-geo/rank-tracker/b1").status_code == 200
    assert client.get("/api/seo-geo/rank-tracker/b1/history?query=nothing").status_code == 404


# ------------------------------- success-path payload shape -------------------------------
#
# Everything above the auth-boundary test exercises 404/503/403/200(cron)/502 —
# the unit logic behind each POST is covered elsewhere (rank_tracker's and
# rank_gap's own test files). What has no coverage yet is the route layer's own
# contract on a *successful* sweep-start / pool-rebuild / gap-card: that the
# response has the shape callers depend on.

def test_sweep_route_starts_a_job_when_online(client, monkeypatch):
    from seo_geo_agent import jobs as seo_jobs
    from seo_geo_agent import state as seo_state
    monkeypatch.setattr(seo_state, "use_network", lambda: True)
    stub_job = {"kind": "rank-sweep", "status": "running", "progress": 0}
    monkeypatch.setattr(seo_jobs, "start", lambda kind, brand_id, body: stub_job)

    resp = client.post("/api/seo-geo/rank-tracker/b1/sweep")
    assert resp.status_code == 200
    assert resp.json()["job"] == stub_job


def test_pool_rebuild_route_returns_the_full_rank_payload(client, monkeypatch):
    from seo_geo_agent import rank_tracker
    monkeypatch.setattr(rank_tracker, "build_pool", lambda brand, rows_fn=None: {
        "queries": [{"query": "q1", "active": True}],
        "sources_used": ["gsc"], "notes": [], "built_at": "2026-10-05",
    })

    resp = client.post("/api/seo-geo/rank-tracker/b1/pool/rebuild")
    assert resp.status_code == 200
    body = resp.json()
    # _rank_payload re-reads via seo_rank.latest_pool(), not the mocked
    # build_pool() return value directly, so this pins the route's response
    # shape rather than a size that depends on persistence this test does not
    # exercise.
    assert set(body) >= {"rows", "worklist", "pool", "budget", "job", "meta",
                         "competitors", "enabled"}


def test_gap_route_returns_the_gap_card_on_success(client, monkeypatch):
    from seo_geo_agent import rank_gap
    stub = {
        "query": "q1", "our_url": "", "our_position": None,
        "their_url": "https://rival.com/x", "their_domain": "rival.com",
        "their_position": 3, "metrics": {}, "narrative": "they have more words",
        "notes": [], "at": "2026-10-05T00:00:00+00:00",
    }
    monkeypatch.setattr(rank_gap, "explain", lambda brand, query: stub)

    resp = client.post("/api/seo-geo/rank-tracker/b1/gap", json={"query": "q1"})
    assert resp.status_code == 200
    assert resp.json()["gap"]["their_domain"] == "rival.com"


# ------------------------------- final-review fixes -------------------------------

def test_payload_carries_the_last_sweep_outcome(client, monkeypatch):
    """I4: a sweep that was disabled, had no key, or found another run in
    progress writes nothing and `jobs.start` still reports the job as done.
    The panel needs the reason, or it renders "all clear" over a dead
    tracker with "Run now" still enabled."""
    from seo_geo_agent import rank_tracker, state as seo_state
    seo_state.save(rank_tracker.SWEEP_DOC.format("b1"), {
        "checked": 0, "ranked": 0, "errors": 0, "blocked": "credentials",
        "at": "2026-10-05T09:00:00+00:00",
        "notes": ["SEO_SERPER_API_KEY not set — rank tracking needs live SERPs"]})

    body = client.get("/api/seo-geo/rank-tracker/b1").json()

    assert body["last_sweep"]["blocked"] == "credentials"
    assert "SEO_SERPER_API_KEY" in body["last_sweep"]["notes"][0]


def test_payload_last_sweep_is_null_before_any_sweep(client):
    """Unknown is not the same as "nothing was blocked" — the panel must be
    able to tell "never run" from "ran and was refused"."""
    assert client.get("/api/seo-geo/rank-tracker/b1").json()["last_sweep"] is None


def test_setting_competitors_normalises_pasted_urls(client, monkeypatch):
    """M2: `d.strip().lower()` stored "https://www.Rival.com/pricing", which
    never equals the "rival.com" a SERP reports — so the rival's rank series
    stayed empty and the tracked-rival scoring boost never fired, silently."""
    from seo_geo_agent import insights
    saved: dict = {}
    monkeypatch.setattr(insights, "upsert_brand", lambda b: saved.update(b))

    resp = client.put("/api/seo-geo/competitors/b1",
                      json={"domains": ["https://www.Rival.com/pricing?x=1",
                                        "WWW.Other.com", "rival.com"]})

    assert resp.status_code == 200
    # Normalised, and deduped once normalisation makes the duplicate visible.
    assert resp.json()["tracked"] == ["rival.com", "other.com"]
    assert saved["competitors"] == ["rival.com", "other.com"]


def test_setting_competitors_rejects_a_value_that_is_not_a_domain(client, monkeypatch):
    """Silently dropping it is the same class of invisible failure as
    silently storing it wrong."""
    from seo_geo_agent import insights
    monkeypatch.setattr(insights, "upsert_brand", lambda b: None)

    resp = client.put("/api/seo-geo/competitors/b1", json={"domains": ["not a domain"]})

    assert resp.status_code == 400
    assert "site domain" in resp.json()["detail"]


def test_setting_competitors_caps_at_the_tracked_rival_limit(client, monkeypatch):
    """M7: the sweep records history for MAX_RIVALS competitors; the stored
    list must not exceed what the sweep will ever track."""
    from seo_geo_agent import insights, rank_tracker
    monkeypatch.setattr(insights, "upsert_brand", lambda b: None)

    resp = client.put("/api/seo-geo/competitors/b1",
                      json={"domains": [f"c{n}.com" for n in range(12)]})

    assert len(resp.json()["tracked"]) == rank_tracker.MAX_RIVALS
