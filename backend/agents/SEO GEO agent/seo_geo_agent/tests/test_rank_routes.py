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
