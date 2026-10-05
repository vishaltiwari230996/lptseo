"""Core Web Vitals from the Chrome UX Report — what real visitors actually got.

This is *field* data, not a lab score: CrUX reports the 75th percentile of what
Chrome users on real devices and real connections experienced over the trailing
28 days. That distinction is the whole reason to prefer it. A Lighthouse run on
a datacentre connection says what the site could do; CrUX says what it did.

The 75th percentile is Google's own threshold — a page "passes" a metric when
75% of visits were at or under the good boundary — so the categories here are
the same ones Search Console reports against.

Needs a Google API key (free, generous quota). Without one this module reports
itself unavailable rather than guessing, exactly like every other source in this
agent.
"""
from __future__ import annotations

import os
from datetime import date

import httpx

from . import state
from .sources import CredentialMissing

_DOC = "vitals-{}"

CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"

#: Google's own good/needs-improvement boundaries, in the units CrUX returns
#: (milliseconds, except CLS which is unitless and scaled by 100).
THRESHOLDS = {
    "largest_contentful_paint": (2500, 4000, "ms", "LCP", "Loading — when the main content appeared"),
    "interaction_to_next_paint": (200, 500, "ms", "INP", "Responsiveness — lag after a tap or click"),
    "cumulative_layout_shift": (10, 25, "cls", "CLS", "Stability — how much the page moved while loading"),
    "first_contentful_paint": (1800, 3000, "ms", "FCP", "When anything first appeared"),
    "experimental_time_to_first_byte": (800, 1800, "ms", "TTFB", "Server response time"),
}

#: The three that decide the Core Web Vitals assessment. FCP and TTFB are
#: diagnostic — useful for explaining a bad LCP, not part of the verdict.
CORE = ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift")


def _api_key() -> str:
    """The CrUX key. ``SEO_``-prefixed so ``state._load_local_env`` exports it
    from ``backend/.env`` like the agent's other settings."""
    return os.environ.get("SEO_CRUX_API_KEY", "").strip()


def available() -> bool:
    return bool(_api_key()) and state.use_network()


def _category(metric: str, p75: float) -> str:
    good, poor, *_ = THRESHOLDS[metric]
    if p75 <= good:
        return "good"
    return "needs-improvement" if p75 <= poor else "poor"


def _query(target: dict, form_factor: str, key: str, client: httpx.Client) -> dict | None:
    """One CrUX record, or None when Google has no data for this target.

    A 404 here is normal and is not an error: CrUX only publishes a URL or
    origin once it has enough traffic to anonymise, so a smaller site or a
    quiet page legitimately has no record.
    """
    body = dict(target)
    if form_factor:
        body["formFactor"] = form_factor
    resp = client.post(f"{CRUX_ENDPOINT}?key={key}", json=body, timeout=25)
    if resp.status_code == 404:
        return None
    if resp.status_code == 429:
        raise CredentialMissing("CrUX quota exceeded for this API key")
    if resp.status_code in (401, 403):
        raise CredentialMissing(
            "CrUX rejected the API key — check SEO_CRUX_API_KEY and that the "
            "Chrome UX Report API is enabled on that Google Cloud project"
        )
    resp.raise_for_status()
    return resp.json().get("record") or None


def _read(record: dict) -> dict:
    """CrUX's metric shape -> ours: p75, category, and the good/poor split."""
    out: dict[str, dict] = {}
    for name, raw in (record.get("metrics") or {}).items():
        if name not in THRESHOLDS:
            continue
        p75 = raw.get("percentiles", {}).get("p75")
        if p75 is None:
            continue
        # CLS arrives as a decimal string ("0.08"); scale it so one integer
        # comparison works for every metric.
        value = float(p75) * 100 if name == "cumulative_layout_shift" else float(p75)
        good, poor, unit, short, blurb = THRESHOLDS[name]
        bins = raw.get("histogram") or []
        out[name] = {
            "metric": short,
            "label": blurb,
            "p75": round(value, 2),
            "unit": unit,
            "category": _category(name, value),
            "good_pct": round(100 * float(bins[0].get("density", 0))) if len(bins) > 0 else None,
            "poor_pct": round(100 * float(bins[2].get("density", 0))) if len(bins) > 2 else None,
            "good_threshold": good,
            "poor_threshold": poor,
        }
    return out


def _assessment(metrics: dict) -> str:
    """Google's rule: all three Core metrics must be good, and all three must be
    present. A missing metric is not a pass."""
    present = [m for m in CORE if m in metrics]
    if len(present) < len(CORE):
        return "insufficient-data"
    cats = [metrics[m]["category"] for m in CORE]
    if all(c == "good" for c in cats):
        return "passing"
    return "failing" if any(c == "poor" for c in cats) else "needs-improvement"


def _origin_candidates(domain: str) -> list[str]:
    """Bare domain first, then the other www/bare form — CrUX needs an exact
    origin match and this app does not know which form the site's real
    traffic is recorded under."""
    bare = domain[4:] if domain.startswith("www.") else domain
    www = f"www.{bare}"
    ordered = [bare, www] if domain == bare else [domain, bare]
    seen: list[str] = []
    for d in ordered:
        candidate = f"https://{d}"
        if candidate not in seen:
            seen.append(candidate)
    return seen


def build(brand: dict, pages: list[str] | None = None) -> dict:
    """Origin-level vitals on both form factors, plus up to five key pages."""
    key = _api_key()
    if not state.use_network():
        raise CredentialMissing("offline mode")
    if not key:
        raise CredentialMissing(
            "SEO_CRUX_API_KEY is not set — get a free key at "
            "console.cloud.google.com (enable 'Chrome UX Report API'), then add it to .env"
        )

    origins = _origin_candidates(brand["domain"])
    notes: list[str] = []
    out_origin: dict[str, dict] = {}
    origin_tried: list[str] = []
    origin_used_by_tag: dict[str, str | None] = {}

    with httpx.Client() as client:
        for form_factor, tag in (("PHONE", "mobile"), ("DESKTOP", "desktop")):
            last_exc: Exception | None = None
            form_factor_used: str | None = None
            record = None
            had_no_data: bool = False
            for candidate in origins:
                if candidate not in origin_tried:
                    origin_tried.append(candidate)
                try:
                    record = _query({"origin": candidate}, form_factor, key, client)
                except CredentialMissing:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    continue
                if record:
                    form_factor_used = candidate
                    break
                else:
                    # This candidate returned None (legitimate no-data), keep trying
                    had_no_data = True

            origin_used_by_tag[tag] = form_factor_used

            # Determine note based on outcomes:
            # - All candidates: no data → "no field data"
            # - All candidates: errors → "request failed"
            # - Mixed (some no-data, some errors) → "request failed" with note about mixed outcome
            if form_factor_used is None and last_exc is None:
                notes.append(
                    f"{tag}: Chrome has no field data for this origin yet — it needs "
                    "enough traffic to report anonymously."
                )
                continue
            elif form_factor_used is None and last_exc is not None:
                if had_no_data:
                    notes.append(
                        f"{tag}: No field data for the tested origins, and the final query failed "
                        f"({type(last_exc).__name__}: {last_exc})"
                    )
                else:
                    notes.append(f"{tag}: CrUX request failed ({type(last_exc).__name__}: {last_exc})")
                continue

            if record is not None:
                metrics = _read(record)
                out_origin[tag] = {
                    "metrics": metrics,
                    "assessment": _assessment(metrics),
                    "period": (record.get("collectionPeriod") or {}).get("lastDate"),
                }

        page_rows: list[dict] = []
        for url in (pages or [])[:5]:
            try:
                record = _query({"url": url}, "PHONE", key, client)
            except CredentialMissing:
                raise
            except Exception:  # noqa: BLE001 — one bad page must not sink the report
                continue
            if record is None:
                page_rows.append({"url": url, "has_data": False})
                continue
            metrics = _read(record)
            page_rows.append({
                "url": url,
                "has_data": True,
                "metrics": metrics,
                "assessment": _assessment(metrics),
            })

    # Decide final origin_used: prefer mobile if it succeeded (mobile is our primary signal),
    # fall back to desktop, then None if neither succeeded. This avoids the ambiguity of
    # "whichever form factor ran last."
    final_origin_used: str | None = (
        origin_used_by_tag.get("mobile")
        or origin_used_by_tag.get("desktop")
        or None
    )
    # Use final origin for the top-level "origin" field, or first candidate if nothing worked
    used_origin = final_origin_used or (origins[0] if origins else None)
    doc = {
        "at": date.today().isoformat(),
        "origin": used_origin,
        "origin_vitals": out_origin,
        "pages": page_rows,
        "notes": notes,
        "origin_tried": origin_tried,
        "origin_used": final_origin_used,
        "origin_used_by_tag": origin_used_by_tag,
    }
    state.save(_DOC.format(brand["id"]), doc)
    return doc


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))
