"""Page speed — how long every page in the sitemap takes to appear on a phone.

"How long until the page is loaded on the user's screen" is measured the way
Google measures it: in a real browser, as **Largest Contentful Paint** — the
moment the main content (the hero image, the headline block) is painted. That
is the number reported first. Alongside it, per page: time to first byte, first
paint, the full load event, layout shift, main-thread blocking, bytes, request
count, *which element* was the LCP element, the heaviest resources, and how
much of the page is third-party.

**Conditions** — Lighthouse's mobile preset, applied through the DevTools
protocol:

* a mid-range Android viewport (412×823 at 1.75x) with a mobile user agent;
* "Slow 4G": 150ms round trip, 1.6 Mbps down, 750 Kbps up;
* the CPU slowed 4x, because the phone is slower than the machine running this;
* a fresh browser context per page — empty cache, no cookies — i.e. a first
  visit, which is what a searcher arriving from Google gets.

These are LAB numbers: one controlled load per page. They are stricter than
most real visits on a good connection, and that is the point — they are
comparable across pages and across runs, and they are the conditions Google
uses to model mobile users. Real-visitor (field) numbers come from the Chrome
UX Report in the Core Web Vitals panel, for the pages busy enough to have them.

Measuring a page this way takes 15–30 seconds, so the job runs in the
background, the most important pages first (home, then landing pages, then the
blog), and results are saved as they arrive — the panel shows what is done so
far, and a restart resumes rather than starting over.
"""
from __future__ import annotations

import asyncio
from datetime import date
from urllib.parse import unquote, urlparse

from . import jobs, state
from .crawl import _Resolver, norm_url

_RESULTS = "speed-{}"
_DOC = "speedsummary-{}"

CONCURRENCY = 4
NAV_TIMEOUT_MS = 90_000
#: Pause before re-measuring pages that failed: long enough for a DNS failure
#: the OS cached to expire.
RETRY_PAUSE_S = 45
#: After the load event, how long to keep listening for a later LCP candidate
#: or late layout shifts before reading the numbers.
SETTLE_MS = 2500
SAVE_EVERY = 8

MOBILE = {
    "viewport": {"width": 412, "height": 823},
    "device_scale_factor": 1.75,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": ("Mozilla/5.0 (Linux; Android 11; moto g power (2022)) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
}
SLOW_4G = {
    "offline": False,
    "latency": 150,
    "downloadThroughput": 1.6 * 1024 * 1024 / 8,
    "uploadThroughput": 750 * 1024 / 8,
}
CPU_SLOWDOWN = 4

#: Google's good / needs-improvement boundaries.
LCP_GOOD, LCP_POOR = 2500, 4000
CLS_GOOD, CLS_POOR = 0.10, 0.25
TBT_GOOD, TBT_POOR = 200, 600

#: Every LCP candidate is kept, not just the last, with the popup it sits in (if
#: any). On lawpreptutorial.com the page's own content painted at 2.1s and a
#: promo modal opened at 16s — and because the modal image was the largest
#: thing painted, IT was the LCP. Reporting only the final number blamed the
#: page for a popup. An overlay is a position:fixed ancestor that is modal-like
#: by name or role, or covers half the screen — a fixed header is not one.
_OBSERVE = """
(() => {
  window.__lcp = 0; window.__lcpEl = null; window.__cls = 0; window.__tbt = 0;
  window.__cands = []; window.__shifts = [];
  const describe = (n) => {
    if (!n || n.nodeType !== 1) return '';
    const c = typeof n.className === 'string' ? n.className.trim().split(/\\s+/).filter(Boolean).slice(0, 2) : [];
    return n.tagName.toLowerCase() + (n.id ? '#' + n.id : '') + (c.length ? '.' + c.join('.') : '');
  };
  const overlayOf = (el) => {
    for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
      if (getComputedStyle(n).position !== 'fixed') continue;
      const b = n.getBoundingClientRect();
      const named = /modal|popup|overlay|lightbox|interstitial|dialog/i.test(n.id + ' ' + n.className)
        || n.getAttribute('role') === 'dialog' || n.getAttribute('aria-modal') === 'true';
      if (named || b.width * b.height >= 0.5 * innerWidth * innerHeight) return describe(n);
    }
    return null;
  };
  const label = (e, el) => !el ? '' : e.url
    ? '<' + el.tagName.toLowerCase() + '> ' + decodeURIComponent(e.url.split('?')[0].split('/').pop()).slice(0, 80)
    : '<' + el.tagName.toLowerCase() + '> "' + (el.innerText || '').trim().slice(0, 60) + '"';
  try {
    new PerformanceObserver(list => {
      for (const e of list.getEntries()) {
        window.__lcp = e.startTime;
        const el = e.element;
        window.__lcpEl = el ? {
          tag: el.tagName.toLowerCase(),
          id: el.id || '',
          cls: (el.className && el.className.baseVal === undefined ? el.className : '').toString().slice(0, 80),
          src: (e.url || el.currentSrc || el.src || '').toString().slice(0, 300),
          text: (el.innerText || '').trim().slice(0, 90),
          size: e.size,
        } : null;
        window.__cands.push({t: Math.round(e.startTime), size: e.size, el: label(e, el),
                             overlay: el ? overlayOf(el) : null});
      }
    }).observe({type: 'largest-contentful-paint', buffered: true});
    new PerformanceObserver(list => {
      for (const e of list.getEntries()) {
        if (e.hadRecentInput) continue;
        window.__cls += e.value;
        if (e.value >= 0.005) window.__shifts.push({t: Math.round(e.startTime), v: +e.value.toFixed(3),
          nodes: (e.sources || []).map(s => describe(s.node)).filter(Boolean).slice(0, 3)});
      }
    }).observe({type: 'layout-shift', buffered: true});
    new PerformanceObserver(list => {
      for (const e of list.getEntries()) window.__tbt += Math.max(0, e.duration - 50);
    }).observe({type: 'longtask', buffered: true});
  } catch (e) {}
})();
"""

#: Timings come from the page; BYTES come from the DevTools network log
#: (``_account``). Resource Timing reports 0 bytes for any cross-origin file
#: whose server omits Timing-Allow-Origin — Google Tag Manager, Trustpilot, an
#: S3 bucket — so page weight read from inside the page was understated and
#: "third-party weight" listed real scripts at 0KB. The network log sees every
#: byte on the wire, the way Lighthouse counts them.
_READ = """() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paint = performance.getEntriesByName('first-contentful-paint')[0];
  const res = performance.getEntriesByType('resource');
  return {
    ttfb: nav.responseStart ? nav.responseStart - nav.startTime : null,
    fcp: paint ? paint.startTime : null,
    lcp: window.__lcp || null,
    lcpEl: window.__lcpEl,
    cls: window.__cls,
    tbt: window.__tbt,
    dcl: nav.domContentLoadedEventEnd ? nav.domContentLoadedEventEnd - nav.startTime : null,
    load: nav.loadEventEnd ? nav.loadEventEnd - nav.startTime : null,
    timings: res.map(r => ({url: r.name, ms: Math.round(r.duration), type: r.initiatorType,
                            size: r.transferSize || r.encodedBodySize || 0})),
    docBytes: nav.transferSize || 0,
    cands: (window.__cands || []).slice(-10),
    shifts: (window.__shifts || []).sort((a, b) => b.v - a.v).slice(0, 5),
    blocking: res.filter(r => r.renderBlockingStatus === 'blocking').length,
  };
}"""

#: Hosts that serve whoever pays for them. A file there is almost always the
#: site's own (its images on S3 or CloudFront), so it is reported as "CDN",
#: not blamed on a third party.
_CDN_SUFFIXES = (".cloudfront.net", ".amazonaws.com", ".azureedge.net", ".akamaized.net",
                 ".fastly.net", ".b-cdn.net", ".cdn77.org", ".imgix.net", ".r2.dev",
                 "res.cloudinary.com", ".gumlet.io", ".imagekit.io")


def _party(url: str, host: str) -> str:
    h = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if h == host or h.endswith("." + host):
        return "own"
    return "cdn" if h.endswith(_CDN_SUFFIXES) else "third"


def _account(net: dict[str, dict], timings: list[dict], doc_bytes: int, host: str) -> dict:
    """Requests and bytes, from the network log when it has them, else from
    Resource Timing (which can only under-count)."""
    ms = {t["url"]: t["ms"] for t in timings}
    items = [
        {"url": n["url"], "type": (n.get("type") or "other").lower(), "bytes": int(n.get("bytes") or 0),
         "ms": ms.get(n["url"])}
        for n in net.values() if n.get("url") and not n["url"].startswith(("data:", "blob:"))
    ]
    if not items:
        items = [{"url": t["url"], "type": t["type"], "bytes": t["size"], "ms": t["ms"]} for t in timings]
        items.append({"url": "(document)", "type": "document", "bytes": doc_bytes, "ms": None})
    by_type: dict[str, int] = {}
    hosts: dict[str, int] = {}
    third = cdn = 0
    third_bytes = cdn_bytes = 0
    for it in items:
        it["party"] = _party(it["url"], host) if it["url"] != "(document)" else "own"
        it["third"] = it["party"] == "third"
        by_type[it["type"]] = by_type.get(it["type"], 0) + it["bytes"]
        if it["party"] == "third":
            third += 1
            third_bytes += it["bytes"]
            h = (urlparse(it["url"]).hostname or "").lower().removeprefix("www.")
            hosts[h] = hosts.get(h, 0) + it["bytes"]
        elif it["party"] == "cdn":
            cdn += 1
            cdn_bytes += it["bytes"]
    heaviest = sorted(items, key=lambda x: -x["bytes"])[:8]
    return {
        "requests": len(items),
        "bytes": sum(it["bytes"] for it in items),
        "third_party_requests": third,
        "third_party_bytes": third_bytes,
        "cdn_requests": cdn,
        "cdn_bytes": cdn_bytes,
        "third_party_hosts": [{"host": h, "bytes": b} for h, b in sorted(hosts.items(), key=lambda kv: -kv[1])[:8]],
        "bytes_by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "heaviest": [{**h, "url": h["url"][:220]} for h in heaviest],
    }


def _rate(value: float | None, good: float, poor: float) -> str | None:
    if value is None:
        return None
    return "good" if value <= good else "needs-improvement" if value <= poor else "poor"


def _describe_lcp(el: dict | None) -> str:
    if not el:
        return ""
    tag = el.get("tag", "")
    if el.get("src"):
        name = unquote(el["src"].split("?")[0].rsplit("/", 1)[-1])
        return f"<{tag}> {name}"
    if el.get("text"):
        return f"<{tag}> \"{el['text'][:60]}\""
    return f"<{tag}{'#' + el['id'] if el.get('id') else ''}>"


async def _guard(ctx, resolver: _Resolver, blocked: list[str]) -> None:
    """Every request the browser makes passes the crawler's public-address check.

    The page decides what the browser fetches — its images, iframes, scripts
    and their fetch() calls, and any redirect — and Chromium resolves names
    itself. Without this, a page containing ``<img src="http://169.254.169.254/…">``
    would have the browser fetch cloud metadata and the network log would save
    its size and timing. Refused requests are aborted and named in the result.
    """

    async def check(route) -> None:
        url = route.request.url
        try:
            await asyncio.to_thread(resolver.check, url)
        except Exception:  # noqa: BLE001 — refused, or unresolvable: either way not fetched
            if len(blocked) < 20:
                blocked.append(url[:200])
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    await ctx.route("**/*", check)


#: Request failures that mean THIS machine's network failed — not anything the
#: page or its third parties did. A load with one of these is not a measurement
#: of the page (a tag manager that never arrived makes the page look faster),
#: so it is marked ``degraded``, re-measured, and left out of the statistics.
#: A third party that times out or refuses is the page's problem and is kept.
_OUR_NET_ERRORS = ("ERR_NAME_NOT_RESOLVED", "ERR_NAME_RESOLUTION_FAILED", "ERR_INTERNET_DISCONNECTED",
                   "ERR_NETWORK_CHANGED", "ERR_NETWORK_IO_SUSPENDED", "ERR_ADDRESS_UNREACHABLE",
                   "ERR_DNS_")

#: A run is reported as failed, not done, when more than this share of its
#: pages could not be measured from here. The first full run here "finished"
#: with 802 of 1,139 pages unmeasured — every blog post — because Windows had
#: cached one failed DNS lookup for the site, and that read as a completed job.
MAX_UNMEASURED = 0.03


def _ours(error_text: str) -> bool:
    return any(code in error_text for code in _OUR_NET_ERRORS)


def _pins(hosts: set[str], resolver: _Resolver) -> str:
    """``--host-resolver-rules`` pinning the site's own hosts to the addresses
    the resolver checked.

    Chromium resolves names itself, through the OS. One failed lookup for the
    site, cached by Windows, turned 802 navigations into instant
    ERR_NAME_NOT_RESOLVED. Pinned, the browser connects to the checked address
    (which also closes DNS rebinding for the site itself) and a resolver
    outage cannot touch navigation. TLS still verifies the certificate against
    the hostname. Third-party hosts are not pinned — their failures are
    detected per page instead (``_OUR_NET_ERRORS``)."""
    rules = []
    for h in sorted(hosts):
        ip = resolver.addresses(h, 443)[0]          # IPv4 first
        rules.append(f"MAP {h} {f'[{ip}]' if ':' in ip else ip}")
    return ",".join(rules)


async def _measure(browser, url: str, host: str, resolver: _Resolver | None = None) -> dict:
    ctx = await browser.new_context(**MOBILE)
    blocked: list[str] = []
    try:
        await _guard(ctx, resolver or _Resolver(), blocked)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        net: dict[str, dict] = {}
        sent: dict[str, str] = {}
        failed: list[dict] = []

        def responded(ev: dict) -> None:
            n = net.setdefault(ev["requestId"], {})
            n["url"] = ev["response"]["url"]
            n["type"] = ev.get("type")

        def finished(ev: dict) -> None:
            net.setdefault(ev["requestId"], {})["bytes"] = ev.get("encodedDataLength", 0)

        def requested(ev: dict) -> None:
            sent[ev["requestId"]] = ev["request"]["url"]

        def failed_ev(ev: dict) -> None:
            err = ev.get("errorText") or ""
            if ev.get("canceled") or err in ("net::ERR_ABORTED", "net::ERR_BLOCKED_BY_CLIENT"):
                return
            if len(failed) < 12:
                failed.append({"url": sent.get(ev["requestId"], "")[:200], "error": err})

        cdp.on("Network.requestWillBeSent", requested)
        cdp.on("Network.responseReceived", responded)
        cdp.on("Network.loadingFinished", finished)
        cdp.on("Network.loadingFailed", failed_ev)
        await cdp.send("Network.enable")
        await cdp.send("Network.emulateNetworkConditions", SLOW_4G)
        await cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU_SLOWDOWN})
        await page.add_init_script(_OBSERVE)
        resp = await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(SETTLE_MS)
        m = await page.evaluate(_READ)
        m.update(_account(net, m.pop("timings"), m.pop("docBytes"), host))
        return {**_row(url, resp.status if resp else None, m), "blocked_requests": blocked,
                "failed_requests": failed, "degraded": any(_ours(f["error"]) for f in failed)}
    except Exception as exc:  # noqa: BLE001 — one failed page is a result, not a crash
        return {"url": url, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        await ctx.close()


def _timeline(cands: list[dict], lcp: float | None) -> dict:
    """Split the LCP candidates into the page's own content and a popup.

    ``content_lcp_ms`` is when the page's largest own element painted — what
    the visitor came for. When the final candidate sits in an overlay, that and
    the reported LCP differ, and the difference is the popup's doing.
    """
    final = cands[-1] if cands else None
    overlay = (final or {}).get("overlay")
    own = [c for c in cands if not c.get("overlay")]
    content_ms = (own[-1]["t"] if own else None) if overlay else (round(lcp) if lcp else None)
    return {
        "lcp_overlay": overlay,
        "content_lcp_ms": content_ms,
        "content_lcp_element": (own[-1]["el"] if own else None) if overlay else (final or {}).get("el"),
        "content_lcp_rating": _rate(content_ms, LCP_GOOD, LCP_POOR) if content_ms else None,
    }


def _row(url: str, status: int | None, m: dict) -> dict:
    """One page's result, from what ``_READ`` returned."""
    cands = m.get("cands") or []
    return {
        "url": url,
        "status": status,
        "ttfb_ms": round(m["ttfb"]) if m["ttfb"] is not None else None,
        "fcp_ms": round(m["fcp"]) if m["fcp"] is not None else None,
        "lcp_ms": round(m["lcp"]) if m["lcp"] else None,
        "dcl_ms": round(m["dcl"]) if m["dcl"] is not None else None,
        "load_ms": round(m["load"]) if m["load"] is not None else None,
        "cls": round(m["cls"], 3),
        "tbt_ms": round(m["tbt"]),
        "requests": m["requests"],
        "bytes": m["bytes"],
        "third_party_requests": m["third_party_requests"],
        "third_party_bytes": m["third_party_bytes"],
        "third_party_hosts": m["third_party_hosts"],
        "cdn_requests": m["cdn_requests"],
        "cdn_bytes": m["cdn_bytes"],
        "render_blocking": m["blocking"],
        "bytes_by_type": m["bytes_by_type"],
        "heaviest": m["heaviest"],
        "lcp_element": _describe_lcp(m["lcpEl"]),
        "lcp_element_detail": m["lcpEl"],
        "lcp_rating": _rate(m["lcp"], LCP_GOOD, LCP_POOR) if m["lcp"] else None,
        "lcp_candidates": cands,
        **_timeline(cands, m["lcp"]),
        "shifts": m.get("shifts") or [],
        "cls_rating": _rate(m["cls"], CLS_GOOD, CLS_POOR),
        "tbt_rating": _rate(m["tbt"], TBT_GOOD, TBT_POOR),
        "error": None,
    }


def queue(records: list[dict], domain: str) -> list[dict]:
    """Pages worth measuring, most important first. Only live HTML on the
    brand's own host — the browser never navigates anywhere a sitemap merely
    mentions. Every request the browser then makes, subresources and
    redirects included, passes the crawler's public-address check (``_guard``)."""
    order = {"home": 0, "landing": 1, "blog_category": 2, "blog_post": 3, "utility": 4}
    host = domain.lower().removeprefix("www.")
    seen: set[str] = set()
    out = []
    for r in sorted(records, key=lambda r: (order.get(r.get("type"), 9), r["url"])):
        if r.get("status") != 200 or r.get("type") not in order:
            continue
        url = r.get("final_url") or r["url"]
        if (urlparse(url).hostname or "").lower().removeprefix("www.") != host:
            continue
        key = norm_url(url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": url, "type": r.get("type"), "subtype": r.get("subtype"), "key": key})
    return out


def run(brand: dict, records: list[dict], progress, *, limit: int | None = None,
        fresh: bool = False) -> dict:
    """Measure every queued page. Saves as it goes; resumes a same-day run."""
    from playwright.async_api import async_playwright

    domain = brand["domain"]
    host = domain.lower().removeprefix("www.")
    resolver = _Resolver()
    todo = queue(records, domain)
    if limit:
        todo = todo[:limit]

    prior, meta = jobs.load_list(_RESULTS.format(brand["id"]))
    done: dict[str, dict] = {}
    if prior and meta and meta.get("day") == date.today().isoformat() and not fresh:
        done = {p["key"]: p for p in prior if _usable(p)}
    remaining = [t for t in todo if t["key"] not in done]
    progress.phase(f"measuring {len(remaining)} pages on throttled mobile "
                   f"({len(done)} already measured today)", total=len(todo))
    progress.step(len(done))

    results: dict[str, dict] = dict(done)
    # Resolved and checked before the browser starts; if the site's own host
    # cannot be resolved at all, this raises and nothing is measured.
    pins = _pins({urlparse(t["url"]).hostname for t in todo} | {domain}, resolver)

    def persist() -> None:
        ordered = [results[t["key"]] for t in todo if t["key"] in results]
        jobs.save_list(_RESULTS.format(brand["id"]), ordered,
                       meta={"day": date.today().isoformat(), "queued": len(todo),
                             "complete": sum(1 for r in ordered if _usable(r)) >= len(todo)})

    async def main() -> None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=[f"--host-resolver-rules={pins}"])
            try:
                # Warm-up, not recorded: the first navigation in a fresh browser
                # pays for DNS, TLS and the server's own cold cache, and would
                # otherwise be blamed on whichever page happened to be first.
                try:
                    resolver.check(f"https://{domain}/")
                    warm = await browser.new_context(**MOBILE)
                    await _guard(warm, resolver, [])
                    p = await warm.new_page()
                    await p.goto(f"https://{domain}/", wait_until="domcontentloaded", timeout=60_000)
                    await warm.close()
                except Exception:  # noqa: BLE001
                    pass

                sem = asyncio.Semaphore(CONCURRENCY)
                counter = {"n": 0}

                async def one(item: dict, *, count: bool = True) -> None:
                    async with sem:
                        try:
                            # Checked once per host and remembered (the crawler's
                            # resolver), so a resolver hiccup mid-run cannot turn
                            # into hundreds of "could not resolve" failures.
                            resolver.check(item["url"])
                        except Exception as exc:  # noqa: BLE001
                            refused = "refusing" in str(exc)
                            res = {"url": item["url"],
                                   "error": f"refused: {exc}" if refused else f"{type(exc).__name__}: {exc}"}
                        else:
                            res = await _measure(browser, item["url"], host, resolver)
                        res.update(key=item["key"], type=item["type"], subtype=item["subtype"])
                        results[item["key"]] = res
                        if count:
                            progress.step()
                        counter["n"] += 1
                        if counter["n"] % SAVE_EVERY == 0:
                            persist()

                await asyncio.gather(*(one(t) for t in remaining))

                # One more try for pages that failed to load or loaded with our
                # network failing under them, two at a time after a pause long
                # enough for a cached DNS failure to expire: those are not
                # measurements of the page.
                again = [t for t in remaining
                         if not _usable(results.get(t["key"]) or {"error": "missing"})
                         and not str((results.get(t["key"]) or {}).get("error", "")).startswith("refused")]
                if again:
                    progress.note(f"retrying {len(again)} pages that failed to load")
                    await asyncio.sleep(RETRY_PAUSE_S)
                    sem = asyncio.Semaphore(2)
                    await asyncio.gather(*(one(t, count=False) for t in again))
            finally:
                await browser.close()

    asyncio.run(main())
    persist()
    doc = summarise(brand)
    verdict = _unmeasured([results.get(t["key"]) for t in todo])
    if verdict:
        raise SpeedUnreliable(verdict)
    return doc


class SpeedUnreliable(RuntimeError):
    """Too many pages could not be measured from here to call the run done."""


def _usable(r: dict) -> bool:
    """A result that is a measurement of the page: it loaded, and this
    machine's network did not fail underneath it."""
    return bool(r) and not r.get("error") and not r.get("degraded") and bool(r.get("lcp_ms"))


def _unmeasured(results: list[dict | None]) -> str | None:
    """The reason a run cannot be called done, or None. Measured pages are
    kept either way; a re-run resumes and measures only the rest."""
    lost = [r for r in results if not _usable(r or {})]
    if len(lost) <= max(3, MAX_UNMEASURED * len(results)):
        return None
    sample = next((r.get("error") or "network failed during the load" for r in lost if r), "not measured")
    ours = sum(1 for r in lost if r and (r.get("degraded") or _ours(r.get("error") or "")))
    why = ("this machine's network or DNS failed" if ours >= len(lost) / 2
           else "the pages did not load")
    return (f"{len(lost)} of {len(results)} pages could not be measured — {why} "
            f"({sample.splitlines()[0][:120]}). The {len(results) - len(lost)} measured pages are "
            "kept and shown; run it again to measure the rest.")


def summarise(brand: dict) -> dict:
    rows, meta = jobs.load_list(_RESULTS.format(brand["id"]))
    ok = [r for r in rows if _usable(r)]

    def pct(values: list[float], p: float) -> float | None:
        if not values:
            return None
        s = sorted(values)
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]

    by_type: dict[str, list[dict]] = {}
    for r in ok:
        by_type.setdefault(r.get("type", "other"), []).append(r)

    lcp_elements: dict[str, int] = {}
    hosts: dict[str, int] = {}
    overlays: dict[str, int] = {}
    movers: dict[str, dict] = {}
    for r in ok:
        if r.get("lcp_element"):
            lcp_elements[r["lcp_element"]] = lcp_elements.get(r["lcp_element"], 0) + 1
        for h in r.get("third_party_hosts") or []:
            hosts[h["host"]] = hosts.get(h["host"], 0) + h["bytes"]
        if r.get("lcp_overlay"):
            overlays[r["lcp_overlay"]] = overlays.get(r["lcp_overlay"], 0) + 1
        # Which elements move, summed over the site: a shared template element
        # (a carousel, a late banner) shows up here as one line with a big total.
        seen_here: set[str] = set()
        for s in r.get("shifts") or []:
            for node in s.get("nodes") or []:
                m = movers.setdefault(node, {"node": node, "cls": 0.0, "pages": 0})
                m["cls"] += s["v"]
                if node not in seen_here:
                    m["pages"] += 1
                    seen_here.add(node)
    popup = [r for r in ok if r.get("lcp_overlay")]
    content = [r["content_lcp_ms"] for r in ok if r.get("content_lcp_ms")]

    doc = {
        "at": date.today().isoformat(),
        "measured": len(ok),
        "failed": sum(1 for r in rows if r.get("error")),
        # Loaded, but this machine's network failed under them: re-measured on
        # the next run and left out of every number here.
        "degraded": sum(1 for r in rows if not r.get("error") and r.get("degraded")),
        "queued": (meta or {}).get("queued", len(rows)),
        "complete": bool((meta or {}).get("complete")),
        "conditions": "Lighthouse mobile preset: 412×823 @1.75x, Slow 4G (150ms RTT, 1.6/0.75 Mbps), "
                      "4x CPU slowdown, cold cache, one fresh context per page",
        "lcp": {
            "median_ms": pct([r["lcp_ms"] for r in ok], 0.5),
            "p75_ms": pct([r["lcp_ms"] for r in ok], 0.75),
            "good": sum(1 for r in ok if r["lcp_rating"] == "good"),
            "needs_improvement": sum(1 for r in ok if r["lcp_rating"] == "needs-improvement"),
            "poor": sum(1 for r in ok if r["lcp_rating"] == "poor"),
        },
        "cls": {
            "median": pct([r["cls"] for r in ok], 0.5),
            "poor": sum(1 for r in ok if r.get("cls_rating") == "poor"),
            "needs_improvement": sum(1 for r in ok if r.get("cls_rating") == "needs-improvement"),
        },
        "tbt_median_ms": pct([r["tbt_ms"] for r in ok], 0.5),
        "bytes_median": pct([r["bytes"] for r in ok], 0.5),
        "requests_median": pct([r["requests"] for r in ok], 0.5),
        "by_type": {
            t: {"pages": len(rs), "lcp_median_ms": pct([r["lcp_ms"] for r in rs], 0.5),
                "poor": sum(1 for r in rs if r["lcp_rating"] == "poor")}
            for t, rs in by_type.items()
        },
        # The page's own content, with any popup LCP set aside — see _timeline.
        "content_lcp": {
            "median_ms": pct(content, 0.5),
            "p75_ms": pct(content, 0.75),
            "good": sum(1 for v in content if v <= LCP_GOOD),
            "needs_improvement": sum(1 for v in content if LCP_GOOD < v <= LCP_POOR),
            "poor": sum(1 for v in content if v > LCP_POOR),
        },
        "popup_lcp": {
            "pages": len(popup),
            "overlays": sorted(({"overlay": k, "pages": v} for k, v in overlays.items()),
                               key=lambda x: -x["pages"])[:5],
            "lcp_median_ms": pct([r["lcp_ms"] for r in popup], 0.5),
            "content_median_ms": pct([r["content_lcp_ms"] for r in popup if r.get("content_lcp_ms")], 0.5),
        },
        "common_shift_sources": sorted(
            ({**m, "cls": round(m["cls"], 3)} for m in movers.values()),
            key=lambda x: -x["cls"])[:8],
        "common_lcp_elements": sorted(({"element": k, "pages": v} for k, v in lcp_elements.items()),
                                      key=lambda x: -x["pages"])[:10],
        "third_party": sorted(({"host": k, "bytes": v} for k, v in hosts.items()),
                              key=lambda x: -x["bytes"])[:12],
    }
    state.save(_DOC.format(brand["id"]), doc)
    return doc


def latest(brand_id: str) -> dict | None:
    return state.load(_DOC.format(brand_id))


def latest_rows(brand_id: str) -> list[dict]:
    return jobs.load_list(_RESULTS.format(brand_id))[0]


def by_url(brand_id: str) -> dict[str, dict]:
    """{norm_url: result} — what the landing audit reads to add performance checks."""
    return {r["key"]: r for r in latest_rows(brand_id) if _usable(r)}
