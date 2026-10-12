"""GSC connection hardening: transient retries, revoke-on-disconnect,
health stamping, and the daily series that feeds the traffic charts."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import httplib2
import pytest
from googleapiclient.errors import HttpError

from seo_geo_agent import gsc_oauth, insights, sources, state
from seo_geo_agent.sources import CredentialMissing

BRAND = {"id": "b", "name": "Acme", "domain": "x.com", "seeds": [], "competitors": []}


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": str(status)}), b"boom")


class _FlakyRequest:
    """Fails with the given statuses, then succeeds."""

    def __init__(self, failures: list[int], payload: dict):
        self.failures = list(failures)
        self.payload = payload
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.failures:
            raise _http_error(self.failures.pop(0))
        return self.payload


def test_retry_recovers_from_transient_statuses(monkeypatch):
    monkeypatch.setattr(sources, "_RETRY_DELAYS", (0, 0))  # no sleeping in tests
    req = _FlakyRequest([429, 503], {"rows": []})
    assert sources._execute_with_retry(req) == {"rows": []}
    assert req.calls == 3


def test_retry_gives_up_after_the_budget(monkeypatch):
    monkeypatch.setattr(sources, "_RETRY_DELAYS", (0, 0))
    req = _FlakyRequest([429, 429, 429], {"rows": []})
    with pytest.raises(HttpError):
        sources._execute_with_retry(req)
    assert req.calls == 3  # two retries, not infinite


def test_permission_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(sources, "_RETRY_DELAYS", (0, 0))
    req = _FlakyRequest([403], {"rows": []})
    with pytest.raises(HttpError):
        sources._execute_with_retry(req)
    assert req.calls == 1  # retrying "not allowed" is just slower "not allowed"


def test_gsc_fetch_daily_rows_sorted_and_typed():
    class Svc:
        def searchanalytics(self):
            return self

        def query(self, siteUrl, body):
            assert body["dimensions"] == ["date"]
            return SimpleNamespace(execute=lambda: {"rows": [
                {"keys": ["2026-10-02"], "clicks": 5, "impressions": 100, "ctr": 0.05, "position": 8.2},
                {"keys": ["2026-10-01"], "clicks": 3, "impressions": 80, "ctr": 0.0375, "position": 9.0},
            ]})

    rows = sources.gsc_fetch_daily("sc-domain:x.com", date(2026, 10, 1), date(2026, 10, 2), service=Svc())
    assert [r["date"] for r in rows] == ["2026-10-01", "2026-10-02"]  # sorted
    assert rows[0] == {"date": "2026-10-01", "clicks": 3, "impressions": 80, "ctr": 0.0375, "position": 9.0}


def test_disconnect_revokes_at_google(monkeypatch):
    state.save("gsc-auth-b", {"refresh_token": "rt-123", "property": "sc-domain:x.com"})
    sent = {}
    monkeypatch.setattr(gsc_oauth.state, "use_network", lambda: True)
    monkeypatch.setattr(gsc_oauth.httpx, "post",
                        lambda url, data, timeout: sent.update(url=url, **data) or SimpleNamespace(status_code=200))
    assert gsc_oauth.disconnect("b") is True
    assert sent["url"] == gsc_oauth.REVOKE_ENDPOINT and sent["token"] == "rt-123"
    assert not gsc_oauth.connection("b")  # tombstone or absent — both read as disconnected


def test_disconnect_still_deletes_when_revoke_fails(monkeypatch):
    state.save("gsc-auth-b", {"refresh_token": "rt-123", "property": "p"})
    monkeypatch.setattr(gsc_oauth.state, "use_network", lambda: True)

    def boom(url, data, timeout):
        raise gsc_oauth.httpx.ConnectError("down")

    monkeypatch.setattr(gsc_oauth.httpx, "post", boom)
    assert gsc_oauth.disconnect("b") is False
    assert not gsc_oauth.connection("b")  # our copy is gone regardless


def test_run_brand_stamps_and_run_persists_series():
    """Offline, with a stored customer grant: the run degrades, and the
    failure is stamped onto the connection so the dashboard can say
    'reconnect needed' instead of silently falling back forever."""
    state.save("gsc-auth-b", {"refresh_token": "rt", "property": "sc-domain:x.com"})
    try:
        run = insights.run_brand(BRAND, trigger="test")
        conn = gsc_oauth.connection("b")
        assert conn.get("last_error")
        assert run["series"] == []  # honest empty, key always present for the charts
    finally:
        state.delete("gsc-auth-b")
        state.delete("run-b")
        state.delete("todos-b")


def test_delete_brand_takes_the_grant_with_it(monkeypatch):
    state.save("gsc-auth-doomed", {"refresh_token": "rt", "property": "p"})
    insights.delete_brand("doomed")
    assert gsc_oauth.connection("doomed") is None



# ------------------- the resurrection race (commit review) -------------------

def test_mark_error_never_recreates_a_disconnected_grant():
    """A background run failing AFTER a disconnect must not write the stale
    (revoked) credential back into existence."""
    state.delete("gsc-auth-b")
    gsc_oauth.mark_error("b", "token revoked")
    assert not gsc_oauth.connection("b")  # still gone — not resurrected


def test_mark_error_stamps_the_current_doc_not_a_stale_copy():
    """A reconnect that lands before the failing run's stamp keeps ITS token —
    the stamp merges onto whatever is stored now, never a copy read earlier."""
    state.save("gsc-auth-b", {"refresh_token": "rt-NEW", "property": "p2"})
    try:
        gsc_oauth.mark_error("b", "old grant failed")
        conn = gsc_oauth.connection("b")
        assert conn["refresh_token"] == "rt-NEW"   # the fresh grant survived
        assert conn["last_error"] == "old grant failed"
        gsc_oauth.clear_error("b")
        conn = gsc_oauth.connection("b")
        assert "last_error" not in conn and conn["refresh_token"] == "rt-NEW"
    finally:
        state.delete("gsc-auth-b")


def test_clear_error_writes_nothing_when_there_is_nothing():
    state.delete("gsc-auth-b")
    gsc_oauth.clear_error("b")
    assert not gsc_oauth.connection("b")
