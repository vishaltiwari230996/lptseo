"""Vitals module tests — CrUX origin retry and degradation notes."""
from __future__ import annotations

from unittest.mock import patch

from seo_geo_agent import vitals


def _brand(domain: str = "lawpreptutorial.com") -> dict:
    return {"id": "b1", "domain": domain}


def test_origin_retry_tries_www_variant_when_bare_domain_has_no_data(monkeypatch):
    """A bare-domain 404 must fall back to the www. form before giving up."""
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    calls: list[str] = []

    def fake_query(target: dict, form_factor: str, key: str, client):
        origin = target.get("origin")
        if origin:
            calls.append(origin)
        if origin == "https://www.lawpreptutorial.com":
            return {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": 2000}}}}
        return None  # bare domain: no CrUX record

    with patch.object(vitals, "_query", side_effect=fake_query):
        doc = vitals.build(_brand())

    assert calls == [
        "https://lawpreptutorial.com",
        "https://www.lawpreptutorial.com",
    ] or calls == [
        "https://lawpreptutorial.com",
        "https://www.lawpreptutorial.com",
        "https://lawpreptutorial.com",
        "https://www.lawpreptutorial.com",
    ]  # once per form factor (mobile, desktop) if both are queried
    assert doc["origin_used"] == "https://www.lawpreptutorial.com"
    assert "https://www.lawpreptutorial.com" in doc["origin_tried"]


def test_origin_retry_records_no_data_when_neither_form_has_records(monkeypatch):
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    with patch.object(vitals, "_query", return_value=None):
        doc = vitals.build(_brand())

    assert doc["origin_used"] is None
    assert any("no field data" in n.lower() for n in doc["notes"])


def test_request_failure_note_is_distinct_from_no_data_note(monkeypatch):
    """A real exception must not be worded identically to 'not enough traffic yet'."""
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    def boom(target, form_factor, key, client):
        raise TimeoutError("connect timed out")

    with patch.object(vitals, "_query", side_effect=boom):
        doc = vitals.build(_brand())

    failure_notes = [n for n in doc["notes"] if "request failed" in n.lower()]
    no_data_notes = [n for n in doc["notes"] if "no field data" in n.lower()]
    assert failure_notes and not no_data_notes
    assert "TimeoutError" in failure_notes[0]


def test_origin_used_is_scoped_per_form_factor_and_does_not_clobber(monkeypatch):
    """Finding 1: origin_used must track which origin each form factor used, not get
    overwritten by the last form factor to run. Prefer mobile result if it succeeded."""
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    def fake_query(target: dict, form_factor: str, key: str, client):
        origin = target.get("origin")
        # Mobile succeeds on bare domain
        if form_factor == "PHONE" and origin == "https://lawpreptutorial.com":
            return {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": 2000}}}}
        # Desktop only succeeds on www variant
        if form_factor == "DESKTOP" and origin == "https://www.lawpreptutorial.com":
            return {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": 2100}}}}
        return None

    with patch.object(vitals, "_query", side_effect=fake_query):
        doc = vitals.build(_brand())

    # Verify per-form-factor tracking
    assert doc["origin_used_by_tag"]["mobile"] == "https://lawpreptutorial.com"
    assert doc["origin_used_by_tag"]["desktop"] == "https://www.lawpreptutorial.com"
    # Final origin_used should prefer mobile (primary signal)
    assert doc["origin_used"] == "https://lawpreptutorial.com"
    # Both form factors should have metrics in the result
    assert "mobile" in doc["origin_vitals"]
    assert "desktop" in doc["origin_vitals"]


def test_mixed_outcome_one_candidate_no_data_then_error(monkeypatch):
    """Finding 2: When first origin candidate returns no data and second raises an
    exception, the note should acknowledge both outcomes, not ambiguously report only
    the error."""
    monkeypatch.setenv("SEO_CRUX_API_KEY", "test-key")
    monkeypatch.setenv("SEO_ALLOW_NETWORK", "1")

    call_sequence = []

    def fake_query(target: dict, form_factor: str, key: str, client):
        origin = target.get("origin")
        call_sequence.append((origin, form_factor))
        # First origin returns None (no data)
        if origin == "https://lawpreptutorial.com":
            return None
        # Second origin raises TimeoutError
        if origin == "https://www.lawpreptutorial.com":
            raise TimeoutError("connect timed out")
        return None

    with patch.object(vitals, "_query", side_effect=fake_query):
        doc = vitals.build(_brand())

    # Verify both origins were tried
    assert len([c for c in call_sequence if c[0] == "https://lawpreptutorial.com"]) >= 1
    assert len([c for c in call_sequence if c[0] == "https://www.lawpreptutorial.com"]) >= 1

    # Verify the note acknowledges mixed outcomes (no data AND error)
    all_notes = doc["notes"]
    # Should have 2 notes (one for mobile, one for desktop)
    assert len(all_notes) >= 2
    # Each note should mention the mixed outcome (no data on one candidate, error on another)
    for note in all_notes:
        # Mixed outcome note should say "No field data for the tested origins"
        assert "tested origins" in note.lower() and "failed" in note.lower()
        # Should mention TimeoutError
        assert "TimeoutError" in note
    # origin_used should be None since no candidate succeeded
    assert doc["origin_used"] is None
