"""The deep crawl: every sitemap, every URL, every page taken apart.

Everything else in the deep audit — sitemap diagnosis, landing-page audit,
cannibalization, blog keyword density, page speed — reads from what this
module produces. It runs once per audit, and each diagnostic works from the
same snapshot, so they cannot disagree about what the site looked like.

Three stages:

1. **Discovery.** Every sitemap declared in robots.txt, plus ``/sitemap.xml``,
   followed through sitemap indexes to ANY depth. The first version of the
   sitemap audit read one level of index and reported 310 URLs for a site that
   has 1,125 — the blog lived in an index nested inside the main index. Each
   sitemap is recorded individually (status, kind, size, which index lists it,
   whether robots.txt names it), because most sitemap faults are properties of
   one file, not of the set.

2. **Fetch.** Every URL, concurrently, with redirects followed by hand so each
   hop is recorded and each hop passes the SSRF address check in
   ``sources._assert_public_address`` — the same guarantee the rest of this
   agent's fetchers give. Time to first byte and total fetch time are taken per
   request.

3. **Dissect.** A real HTML parser (lxml) extracts ~40 facts per page, and
   trafilatura isolates the main content from navigation, headers, footers and
   sidebars. That separation is not optional for this audit: counting nav and
   footer words would inflate every word count and dilute every keyword ratio,
   and the 150-words-per-keyword rule would be measured against text the reader
   never reads.
"""
from __future__ import annotations

import gzip
import hashlib
import ipaddress
import re
import socket
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import httpcore
import httpx

from .sources import (
    FETCH_UA,
    MAX_REDIRECT_HOPS,
    _REDIRECT_CODES,
    _assert_public_address,
    _non_public,
)

#: Parallel fetches. Polite for a production site — a single browser visit to
#: one of these pages already issues 150+ requests.
CONCURRENCY = 8
TIMEOUT = 20.0

#: Pause before each retry of a request that got no HTTP answer, or a 429/503.
RETRY_DELAYS = (1.5, 4.0)

#: A crawl is published only if at least this share of the site's own URLs got
#: an HTTP answer. The rest are gaps in OUR view of the site, not faults in it,
#: and publishing them would report this machine's outage as the site's dead
#: pages. That happened once: a resolver failure mid-crawl turned 1,059 of 1,140
#: live pages into "status 0" and the audit overwrote a good report with them.
MIN_REACHED = 0.97

#: Give up early when half of the first EARLY_SAMPLE answers are network
#: failures: an unreachable site should fail in seconds, not after retrying
#: every one of 1,100 URLs.
EARLY_SAMPLE = 40
EARLY_ABORT = 0.5


class CrawlUnreliable(RuntimeError):
    """Too much of the site was unreachable from here to publish this crawl."""


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

class _Resolver:
    """Each host's addresses: looked up once, checked once, then connected to.

    Two jobs.

    **SSRF.** The address that passes the public-address check is the address
    the connection is made to. Checking a name and then letting the HTTP
    library resolve it again leaves a gap: a name that answers "public" to the
    check and "169.254.169.254" to the connect (DNS rebinding) walks through.

    **Resilience.** A crawl lost 1,059 of 1,140 pages to "could not resolve"
    while the site was up: one lookup failed mid-crawl, the OS resolver cached
    the failure, and every later lookup failed in 0 ms. A good answer is kept
    for the crawl, a failed lookup is retried, and while the cache is fresh a
    resolver outage costs nothing. Only good answers are kept — a refused
    host is checked again every time.
    """

    TTL = 600.0
    LOOKUP_RETRIES = (0.5, 2.0, 5.0)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._known: dict[tuple[str, int], tuple[float, list[str]]] = {}

    def addresses(self, host: str, port: int) -> list[str]:
        """Public addresses for ``host``, IPv4 first. Raises ``httpx.ConnectError``
        if it cannot be resolved or resolves anywhere non-public."""
        key = (host.lower(), port)
        with self._lock:
            hit = self._known.get(key)
        if hit and time.monotonic() - hit[0] < self.TTL:
            return hit[1]
        infos, last = None, None
        for delay in (0.0, *self.LOOKUP_RETRIES):
            if delay:
                time.sleep(delay)
            try:
                infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
                break
            except OSError as exc:
                last = exc
        if infos is None:
            raise httpx.ConnectError(f"could not resolve {host}") from last
        addrs: list[str] = []
        for info in infos:
            raw = str(info[4][0]).split("%", 1)[0]
            try:
                addr = ipaddress.ip_address(raw)
            except ValueError as exc:  # pragma: no cover - getaddrinfo shouldn't
                raise httpx.ConnectError(f"could not resolve {host}") from exc
            if _non_public(addr):
                raise httpx.ConnectError(f"{host} resolves to a non-public address ({addr}) — refusing")
            if raw not in addrs:
                addrs.append(raw)
        addrs.sort(key=lambda a: ":" in a)  # IPv4 first; many networks have no IPv6 route
        with self._lock:
            self._known[key] = (time.monotonic(), addrs)
        return addrs

    def check(self, url: str) -> None:
        """``sources._assert_public_address``, answered from this resolver."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise httpx.UnsupportedProtocol(f"{url[:200]!r} is not an http(s) address")
        if not parsed.hostname:
            raise httpx.ConnectError(f"{url[:200]!r} has no host")
        self.addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))


class _PinnedBackend(httpcore.SyncBackend):
    """httpcore's socket layer, connecting to the resolver's checked addresses
    instead of resolving the name again. TLS still verifies the certificate
    against the hostname: httpcore takes SNI from the URL, not from here."""

    def __init__(self, resolver: _Resolver) -> None:
        super().__init__()
        self._resolver = resolver

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last: Exception | None = None
        for ip in self._resolver.addresses(host, port)[:4]:
            try:
                return super().connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                           socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last or httpcore.ConnectError(f"no address for {host}")

#: Stop following sitemap indexes past this depth, and stop reading sitemaps
#: past this count. Both are far above any real site; they exist so a sitemap
#: that indexes itself cannot spin the crawler forever.
MAX_SITEMAP_DEPTH = 5
MAX_SITEMAPS = 400
#: URLs read from sitemaps, in total. Far above this site (1,140) and most
#: others; it bounds how much crawling a sitemap can make this job do.
MAX_URLS = 20_000

#: Main text is kept in memory for the density and near-duplicate passes, then
#: dropped. This bounds one pathological page.
MAX_TEXT_CHARS = 200_000

_UTILITY = re.compile(
    r"/(contact|contact-us|privacy|privacy-policy|terms|terms-and-conditions|refund|"
    r"refund-policy|cancellation|disclaimer|login|signin|sign-in|register|signup|cart|"
    r"checkout|account|my-account|careers?|faq|sitemap|search|thank-you|thankyou)(/|$)",
    re.I,
)

_WORD = re.compile(r"[A-Za-z0-9ऀ-ॿ]+(?:['’\-][A-Za-z0-9ऀ-ॿ]+)*")


def words(text: str) -> list[str]:
    """Tokenise the way a word counter does: hyphenated compounds are one word.

    Devanagari is included because Indian-market pages routinely mix scripts,
    and dropping those words would under-count the content.
    """
    return _WORD.findall(text or "")


def norm_url(url: str) -> str:
    """Identity for comparing URLs: lower host without www, no fragment, no
    trailing slash on the path. Scheme is dropped deliberately — an http and an
    https URL for the same path are the same page for every purpose here, and
    the mismatch is reported separately as its own finding."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (u.netloc or "").lower().removeprefix("www.")
    path = u.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = f"?{u.query}" if u.query else ""
    return f"{host}{path}{query}"


def _same_site(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host == domain.lower().removeprefix("www.")


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

@dataclass
class Fetched:
    url: str
    final_url: str
    status: int
    chain: list[dict] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    content: bytes = b""
    ttfb_ms: float = 0.0
    total_ms: float = 0.0
    error: str | None = None
    #: The request got no HTTP answer at all — resolution, connection, timeout,
    #: a dropped socket. Distinct from anything the site answered: a 404 is the
    #: site's fault, this may well be ours.
    network_error: bool = False
    attempts: int = 1


#: Exceptions meaning "no HTTP answer came back". The SSRF refusals are
#: ConnectErrors too, and are excluded by message: a refused address is a
#: decision, not a failure, and retrying it would change nothing.
_NO_ANSWER = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


def _no_answer(exc: Exception) -> bool:
    msg = str(exc)
    return isinstance(exc, _NO_ANSWER) and "refusing" not in msg and "has no host" not in msg


def _retry_after(headers: dict) -> float:
    try:
        return min(10.0, max(0.0, float(headers.get("retry-after", 0))))
    except ValueError:
        return 0.0


def fetch(cli: httpx.Client, url: str) -> Fetched:
    """One URL, redirects followed by hand, every hop recorded and address-checked.

    Never raises: an unreachable page is a result (status 0, ``error`` set),
    because to an audit it is a finding like any other. A request that got no
    answer, or a 429/503, is retried twice with a pause first — Googlebot
    retries too, and a single dropped packet is not a finding.
    """
    resolver = getattr(cli, "_seo_resolver", None)
    check = resolver.check if resolver else _assert_public_address
    got = _fetch_once(cli, url, check)
    for n, delay in enumerate(RETRY_DELAYS, start=2):
        if not (got.network_error or got.status in (429, 503)):
            break
        # Only the site's own host is retried. A redirect into someone else's
        # server that fails is the site's finding, and retrying it would let a
        # hostile sitemap point this crawler's retries at a third party.
        if not _same_site(got.final_url, urlparse(url).hostname or ""):
            break
        time.sleep(max(delay, _retry_after(got.headers)))
        got = _fetch_once(cli, url, check)
        got.attempts = n
    return got


def _fetch_once(cli: httpx.Client, url: str, check) -> Fetched:
    chain: list[dict] = []
    target = url
    started = time.perf_counter()
    try:
        for _ in range(MAX_REDIRECT_HOPS + 1):
            check(target)
            t0 = time.perf_counter()
            req = cli.build_request("GET", target)
            resp = cli.send(req, stream=True)
            ttfb = (time.perf_counter() - t0) * 1000
            try:
                if resp.status_code in _REDIRECT_CODES and resp.headers.get("location"):
                    chain.append({"url": target, "status": resp.status_code})
                    target = str(resp.url.join(resp.headers["location"]))
                    continue
                body = resp.read()
            finally:
                resp.close()
            return Fetched(
                url=url,
                final_url=target,
                status=resp.status_code,
                chain=chain,
                headers={k.lower(): v for k, v in resp.headers.items()},
                content=body,
                ttfb_ms=round(ttfb, 1),
                total_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        return Fetched(url=url, final_url=target, status=0, chain=chain,
                       error=f"more than {MAX_REDIRECT_HOPS} redirects")
    except Exception as exc:  # noqa: BLE001 — see fetch's docstring
        return Fetched(url=url, final_url=target, status=0, chain=chain,
                       total_ms=round((time.perf_counter() - started) * 1000, 1),
                       error=f"{type(exc).__name__}: {str(exc)[:160]}",
                       network_error=_no_answer(exc))


def downgrades(chain: list[dict], final_url: str) -> bool:
    """True when a redirect chain that started on https passes through http.

    ``https://site/blog -> http://site/blog/ -> https://site/blog/`` ends in the
    right place, so a check that only looks at the destination calls it fine.
    It is not: the middle hop is served in the clear, which is exactly the
    request an attacker on a café network rewrites, and it is a wasted crawl.
    """
    hops = [h["url"] for h in chain] + [final_url]
    return bool(hops) and hops[0].startswith("https://") and any(u.startswith("http://") for u in hops[1:])


def _client() -> httpx.Client:
    resolver = _Resolver()
    transport = httpx.HTTPTransport(
        limits=httpx.Limits(max_connections=CONCURRENCY * 2, max_keepalive_connections=CONCURRENCY),
    )
    # httpx has no public hook for the socket layer, so the pool's backend is
    # replaced after construction. `test_client_connects_to_the_checked_address`
    # fails if an httpx upgrade moves it; until then every connection this
    # client makes goes to an address the resolver checked.
    pool = getattr(transport, "_pool", None)
    if pool is not None and hasattr(pool, "_network_backend"):
        pool._network_backend = _PinnedBackend(resolver)
    cli = httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=False,  # hops are followed by `fetch`, which checks each one
        headers={"User-Agent": FETCH_UA, "Accept-Encoding": "gzip, deflate, br"},
        transport=transport,
    )
    cli._seo_resolver = resolver
    return cli


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

_URL_BLOCK = re.compile(r"<url\b.*?</url>", re.I | re.S)
_SM_BLOCK = re.compile(r"<sitemap\b.*?</sitemap>", re.I | re.S)
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
_LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.I | re.S)
_W3C_DATE = re.compile(
    r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?)?)?$"
)


def _xml_text(content: bytes) -> str:
    if content[:2] == b"\x1f\x8b":  # a .xml.gz served without Content-Encoding
        try:
            content = gzip.decompress(content)
        except OSError:
            return ""
    return content.decode("utf-8", errors="replace")


def discover(domain: str, cli: httpx.Client | None = None) -> dict:
    """Every sitemap reachable from robots.txt or /sitemap.xml, and every URL in them."""
    own = cli is None
    cli = cli or _client()
    try:
        robots = fetch(cli, f"https://{domain}/robots.txt")
        if robots.network_error:
            raise CrawlUnreliable(
                f"{domain} could not be reached from here ({robots.error}). That is a network "
                "problem on this machine or the site's DNS, not an SEO finding, so nothing was "
                "published; the last complete audit is still shown.")
        robots_text = robots.content.decode("utf-8", errors="replace") if robots.status == 200 else ""
        declared = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_text)

        queue: list[tuple[str, int, str | None]] = [(u, 0, None) for u in declared]
        default = f"https://{domain}/sitemap.xml"
        if not any(norm_url(u) == norm_url(default) for u in declared):
            queue.append((default, 0, None))

        sitemaps: dict[str, dict] = {}
        urls: dict[str, dict] = {}
        truncated = 0
        declared_norm = {norm_url(u) for u in declared}

        while queue and len(sitemaps) < MAX_SITEMAPS:
            sm_url, depth, parent = queue.pop(0)
            key = norm_url(sm_url)
            if key in sitemaps:
                if parent and parent not in sitemaps[key]["parents"]:
                    sitemaps[key]["parents"].append(parent)
                continue
            got = fetch(cli, sm_url)
            rec = {
                "url": sm_url,
                "status": got.status,
                "error": got.error,
                "network_error": got.network_error,
                "final_url": got.final_url,
                "redirected": bool(got.chain),
                "declared_in_robots": key in declared_norm,
                "parents": [parent] if parent else [],
                "depth": depth,
                "kind": "error",
                "bytes": len(got.content),
                "url_count": 0,
                "child_count": 0,
                "lastmod_count": 0,
                "lastmod_invalid": 0,
                "lastmod_future": 0,
                "content_type": got.headers.get("content-type", ""),
            }
            sitemaps[key] = rec
            if got.status != 200:
                continue
            text = _xml_text(got.content)
            low = text[:2000].lower()
            if "<sitemapindex" in low:
                rec["kind"] = "index"
                children = []
                for block in _SM_BLOCK.findall(text):
                    loc = _LOC.search(block)
                    if loc:
                        children.append(loc.group(1).strip())
                rec["child_count"] = len(children)
                if depth + 1 <= MAX_SITEMAP_DEPTH:
                    queue.extend((c, depth + 1, sm_url) for c in children)
            elif "<urlset" in low:
                rec["kind"] = "urlset"
                today = time.strftime("%Y-%m-%d")
                for block in _URL_BLOCK.findall(text):
                    loc = _LOC.search(block)
                    if not loc:
                        continue
                    u = loc.group(1).strip().replace("&amp;", "&")
                    if len(urls) >= MAX_URLS and norm_url(u) not in urls:
                        truncated += 1
                        continue
                    mod = _LASTMOD.search(block)
                    mod_raw = mod.group(1).strip() if mod else None
                    rec["url_count"] += 1
                    if mod_raw:
                        rec["lastmod_count"] += 1
                        if not _W3C_DATE.match(mod_raw):
                            rec["lastmod_invalid"] += 1
                        elif mod_raw[:10] > today:
                            rec["lastmod_future"] += 1
                    entry = urls.setdefault(norm_url(u), {
                        "url": u, "sitemaps": [], "lastmod": mod_raw,
                    })
                    if sm_url not in entry["sitemaps"]:
                        entry["sitemaps"].append(sm_url)
            else:
                rec["kind"] = "unparseable"

        # A sitemap on the site's own host that got no answer is a hole in the
        # URL list — every page it lists would silently drop out of the audit.
        # (One on another host that is unreachable is a real finding and stays.)
        lost = [s for s in sitemaps.values() if s["network_error"] and _same_site(s["url"], domain)]
        if lost:
            raise CrawlUnreliable(
                f"{len(lost)} of the site's sitemaps could not be reached from here "
                f"({lost[0]['url']}: {lost[0]['error']}), so the URL list is incomplete. Nothing "
                "was published; the last complete audit is still shown.")

        parser = urllib.robotparser.RobotFileParser()
        parser.parse(robots_text.splitlines())
        return {
            "domain": domain,
            "robots": {
                "status": robots.status,
                "declared": declared,
                "text": robots_text[:20_000],
            },
            "sitemaps": list(sitemaps.values()),
            "urls": urls,
            #: URLs past MAX_URLS, listed but not crawled.
            "truncated": truncated,
            "_robots_parser": parser,
        }
    finally:
        if own:
            cli.close()


# --------------------------------------------------------------------------- #
# Dissection
# --------------------------------------------------------------------------- #

def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _page_type(url: str, sitemaps: list[str]) -> tuple[str, str]:
    """(type, subtype). Sitemap provenance first — a site's own sitemap names
    are the most reliable statement of what a URL is — then URL shape."""
    path = urlparse(url).path or "/"
    segs = [s for s in path.split("/") if s]
    joined = " ".join(sitemaps).lower()
    if path in ("", "/"):
        return "home", "home"
    if segs and segs[0].lower() == "blog":
        if "category" in joined or "tag" in joined or len(segs) == 1:
            return "blog_category", "archive"
        if "post" in joined or len(segs) >= 3 or (len(segs) == 2 and "category" not in joined):
            return "blog_post", "article"
        return "blog_category", "archive"
    if _UTILITY.search(path):
        return "utility", "utility"
    # Landing pages, sub-typed because the right structured data and the right
    # recommendations differ by kind.
    last = segs[-1].lower() if segs else ""
    if "topper" in path.lower() or "result" in path.lower():
        return "landing", "proof"
    if "resource" in path.lower() or "resourse" in path.lower() or "download" in path.lower():
        return "landing", "resource"
    if "center" in joined or "centre" in joined:
        return "landing", "centre"
    if len(segs) >= 2 and ("coaching" in last or "classes" in last or "course" in last):
        return "landing", "location"
    if "coaching" in last or "course" in last or "classes" in last or "test-series" in last:
        return "landing", "service"
    return "landing", "page"


def _json_ld(doc) -> tuple[list[str], int, list[dict]]:
    """(types, parse_errors, raw objects). Types are collected through @graph and
    nested arrays, because a site that emits one @graph block with six entities
    would otherwise look like it has one type."""
    import json

    types: list[str] = []
    errors = 0
    objects: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                types.append(t)
            elif isinstance(t, list):
                types.extend(str(x) for x in t)
            if "@type" in node:
                objects.append(node)
            for k in ("@graph", "mainEntity", "itemListElement", "hasPart"):
                if k in node:
                    walk(node[k])

    for el in doc.xpath('//script[@type="application/ld+json"]'):
        raw = (el.text or "").strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except ValueError:
            errors += 1
    return sorted(set(types)), errors, objects[:12]


def dissect(fetched: Fetched, domain: str, sitemaps: list[str], lastmod: str | None) -> tuple[dict, str]:
    """(record, main_text). The record is persisted; the text is held in memory
    for the passes that need it and then dropped."""
    import lxml.html

    base = {
        "url": fetched.url,
        "final_url": fetched.final_url,
        "status": fetched.status,
        "error": fetched.error,
        "network_error": fetched.network_error,
        "attempts": fetched.attempts,
        "chain": fetched.chain,
        "ttfb_ms": fetched.ttfb_ms,
        "fetch_ms": fetched.total_ms,
        "html_bytes": len(fetched.content),
        "content_type": fetched.headers.get("content-type", ""),
        "x_robots_tag": fetched.headers.get("x-robots-tag", ""),
        "sitemaps": sitemaps,
        "lastmod": lastmod,
    }
    ptype, subtype = _page_type(fetched.url, sitemaps)
    base["type"], base["subtype"] = ptype, subtype

    if fetched.status != 200 or "html" not in base["content_type"].lower() or not fetched.content:
        return base, ""

    try:
        text = fetched.content.decode(
            _charset(base["content_type"]) or "utf-8", errors="replace")
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
        doc = lxml.html.fromstring(text)
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"unparseable HTML: {type(exc).__name__}"
        return base, ""

    page_url = fetched.final_url
    head_title = _clean(" ".join(doc.xpath("//head/title//text()")) or " ".join(doc.xpath("//title//text()")))

    def meta(name: str, attr: str = "name") -> str:
        vals = doc.xpath(f'//meta[translate(@{attr},"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="{name}"]/@content')
        return _clean(vals[0]) if vals else ""

    canon_vals = doc.xpath('//link[translate(@rel,"CANONICL","canonicl")="canonical"]/@href')
    canonical = urljoin(page_url, canon_vals[0].strip()) if canon_vals else ""
    robots_meta = (meta("robots") + "," + meta("googlebot")).lower()
    x_robots = base["x_robots_tag"].lower()

    headings = []
    for el in doc.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
        t = _clean(el.text_content())
        headings.append({"level": int(el.tag[1]), "text": t[:200]})
    h1s = [h["text"] for h in headings if h["level"] == 1]
    skips = 0
    prev = 0
    for h in headings:
        if prev and h["level"] > prev + 1:
            skips += 1
        prev = h["level"]

    # Images — alt, and explicit dimensions (their absence is a layout-shift risk).
    imgs = doc.xpath("//img")
    img_missing_alt = [i.get("src") or i.get("data-src") or "" for i in imgs if i.get("alt") is None]
    img_empty_alt = sum(1 for i in imgs if i.get("alt") is not None and not i.get("alt").strip())
    img_no_dims = sum(1 for i in imgs if not (i.get("width") and i.get("height")))
    img_lazy = sum(1 for i in imgs if (i.get("loading") or "").lower() == "lazy")

    # Links.
    internal, external, nofollow_internal = [], 0, 0
    generic_anchors, empty_anchors = 0, 0
    anchors_sample: list[str] = []
    for a in doc.xpath("//a[@href]"):
        href = a.get("href").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "whatsapp:")):
            continue
        absu = urljoin(page_url, href)
        anchor = _clean(a.text_content()) or _clean(a.get("aria-label")) or _clean(
            " ".join(a.xpath(".//img/@alt")))
        if _same_site(absu, domain):
            internal.append(norm_url(absu))
            if "nofollow" in (a.get("rel") or "").lower():
                nofollow_internal += 1
            if not anchor:
                empty_anchors += 1
            elif anchor.lower() in {"click here", "read more", "here", "learn more", "more",
                                    "know more", "view more", "details", "link"}:
                generic_anchors += 1
            if anchor and len(anchors_sample) < 40:
                anchors_sample.append(anchor[:80])
        elif absu.startswith("http"):
            external += 1

    # Mixed content: http:// subresources on an https page. The URLs are kept
    # (a few) because WHERE they point is the finding: on this site they were
    # 568 article images on http://13.127.188.157 — a raw server IP that the
    # browser blocks, so the images never appear for anyone.
    mixed = 0
    mixed_urls: list[str] = []
    if page_url.startswith("https://"):
        for attr in ("src", "href"):
            for v in doc.xpath(f"//img/@{attr}|//script/@{attr}|//link[@rel='stylesheet']/@{attr}|//iframe/@{attr}|//source/@{attr}"):
                if v.strip().startswith("http://"):
                    mixed += 1
                    if len(mixed_urls) < 5:
                        mixed_urls.append(v.strip()[:200])

    # Render-blocking in <head>: stylesheets, and scripts that are neither async nor defer.
    head_css = len(doc.xpath("//head/link[translate(@rel,'STYLESHET','styleshet')='stylesheet']"))
    head_js_blocking = len(doc.xpath(
        "//head/script[@src and not(@async) and not(@defer) and not(@type='module')]"))

    types, ld_errors, ld_objects = _json_ld(doc)

    body_text = _clean(" ".join(doc.xpath("//body//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::noscript)]")))
    main_text = _main_text(text) or ""
    main_words = words(main_text)

    lang = (doc.xpath("//html/@lang") or [""])[0].strip()
    hreflang = [
        {"lang": l.get("hreflang"), "href": l.get("href")}
        for l in doc.xpath("//link[@rel='alternate'][@hreflang]")
    ][:20]

    record = {
        **base,
        "lang": lang,
        "title": head_title[:300],
        "title_len": len(head_title),
        "meta_description": meta("description")[:500],
        "meta_desc_len": len(meta("description")),
        "canonical": canonical,
        "canonical_kind": (
            "missing" if not canonical
            else "self" if norm_url(canonical) == norm_url(page_url)
            else "other"
        ),
        "noindex": "noindex" in robots_meta or "noindex" in x_robots,
        "nofollow": "nofollow" in robots_meta or "nofollow" in x_robots,
        "meta_robots": robots_meta.strip(","),
        "viewport": bool(meta("viewport")),
        "charset": bool(doc.xpath("//meta[@charset]") or "charset" in base["content_type"].lower()),
        "h1": [h[:200] for h in h1s],
        "headings": headings[:120],
        "heading_skips": skips,
        "h2_count": sum(1 for h in headings if h["level"] == 2),
        "word_count": len(main_words),
        "word_count_body": len(words(body_text)),
        "first_100": " ".join(main_words[:100]),
        "last_100": " ".join(main_words[-100:]),
        "images": len(imgs),
        "img_missing_alt": len(img_missing_alt),
        "img_missing_alt_sample": img_missing_alt[:8],
        "img_empty_alt": img_empty_alt,
        "img_no_dims": img_no_dims,
        "img_lazy": img_lazy,
        "img_alts": [(_clean(i.get("alt")) or "")[:120] for i in imgs if i.get("alt")][:60],
        "internal_links": sorted(set(internal)),
        "internal_link_count": len(internal),
        "external_link_count": external,
        "nofollow_internal": nofollow_internal,
        "generic_anchors": generic_anchors,
        "empty_anchors": empty_anchors,
        "anchors_sample": anchors_sample,
        "schema_types": types,
        "schema_errors": ld_errors,
        "schema_objects": ld_objects,
        "og": {
            "title": meta("og:title", "property"),
            "description": meta("og:description", "property"),
            "image": meta("og:image", "property"),
            "url": meta("og:url", "property"),
            "type": meta("og:type", "property"),
        },
        "twitter_card": meta("twitter:card"),
        "hreflang": hreflang,
        "mixed_content": mixed,
        "mixed_content_urls": mixed_urls,
        "head_css": head_css,
        "head_js_blocking": head_js_blocking,
        "scripts": len(doc.xpath("//script")),
        "iframes": len(doc.xpath("//iframe")),
        "content_hash": hashlib.sha1(" ".join(main_words).lower().encode()).hexdigest()[:16],
    }
    return record, main_text[:MAX_TEXT_CHARS]


def _charset(content_type: str) -> str | None:
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    return m.group(1) if m else None


#: Elements that are never a page's content, whatever an extractor thinks.
_CHROME_TAGS = ("header", "nav", "footer", "aside", "form", "dialog", "script", "style",
                "noscript", "template", "iframe", "svg", "button", "select", "input", "textarea")

#: Class/id fragments that mark overlays and site chrome. Matched against the
#: class and id attributes, never against <html>, <body> or <main> — a body with
#: class "modal-open" is still the page.
_CHROME_ATTR = re.compile(
    r"(^|[\s_-])(modal|popup|pop-up|login|signin|sign-in|signup|sign-up|register|cookie|"
    r"consent|overlay|offcanvas|off-canvas|drawer|newsletter|whatsapp|chatbot|chat-widget|"
    r"toast|lightbox|mega-?menu|dropdown-menu|breadcrumbs?|share-buttons?|social-share)($|[\s_-])",
    re.I,
)
_CHROME_ROLES = ("dialog", "alertdialog", "navigation", "banner", "contentinfo", "search")


def _strip_chrome(html: str) -> str:
    """The page with navigation, overlays and forms removed, before extraction.

    trafilatura already drops nav, header and footer on a well-formed article.
    What it cannot know is that a ``<div id="login-modal">`` sitting in
    ``<body>`` is a popup — and on a page whose real content is sparse (a list
    of links, a few headings), the modal is the biggest block of text on the
    page, so the extractor returns IT as the "main content". That is not
    hypothetical: it made two different resource pages on this site score as
    100% identical, because both "main contents" were the same login form.

    Collapsed accordions (``aria-hidden="true"``) are deliberately kept: an FAQ
    panel that is closed until tapped is indexable content, not chrome.
    """
    try:
        import lxml.html
        from lxml import etree

        doc = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001
        return html
    kill = []
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        if tag in ("html", "body", "main", "head"):
            continue
        if tag in _CHROME_TAGS:
            kill.append(el)
            continue
        if (el.get("role") or "").lower() in _CHROME_ROLES or (el.get("aria-modal") or "").lower() == "true":
            kill.append(el)
            continue
        attrs = f"{el.get('class') or ''} {el.get('id') or ''}"
        if attrs.strip() and _CHROME_ATTR.search(attrs):
            kill.append(el)
    for el in kill:
        parent = el.getparent()
        if parent is not None:
            # Keep the tail text — it belongs to the parent, not to the removed node.
            if el.tail:
                prev = el.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + el.tail
                else:
                    parent.text = (parent.text or "") + el.tail
            parent.remove(el)
    try:
        return etree.tostring(doc, encoding="unicode", method="html")
    except Exception:  # noqa: BLE001
        return html


def _main_text(html: str) -> str:
    """The page's own content: chrome and overlays stripped first, then
    trafilatura to separate the body from what is left of the template.

    ``favor_recall`` rather than precision: on a templated commercial page the
    real content is often split across several blocks, and trafilatura in
    precision mode drops all but the largest — which would under-count a
    landing page's words and hide half of a blog post from the density check.

    ``deduplicate`` stays OFF. trafilatura's deduplication is a cache shared by
    every call in the process: text it has seen a few times is dropped from
    every later page. Across a crawl of 88 near-identical centre pages the
    first few kept their 277 words and later ones came back with 0 — the
    extraction depended on crawl order, "thin content" fired on pages that
    were not thin, and empty pages then escaped the duplicate check. Each page
    is extracted on its own here; what is shared across pages is measured
    deliberately, site-wide, by ``_mark_unique``.
    """
    cleaned = _strip_chrome(html)
    try:
        import trafilatura

        return trafilatura.extract(
            cleaned,
            include_comments=False,
            include_tables=True,
            include_links=False,
            favor_recall=True,
            deduplicate=False,
        ) or ""
    except Exception:  # noqa: BLE001 — extraction failure means "no main text", not a crash
        return ""


#: A 5-word run of main content found on this many landing pages or more is
#: template copy, not the page's own content.
SHARED_ON_PAGES = 3
_SHINGLE = 5
_UNIQUE_TYPES = ("landing", "home")


def _mark_unique(records: list[dict], texts: dict[str, str]) -> None:
    """Set ``unique_word_count`` on every landing page: the main-content words
    that are this page's own.

    A word is unique when at least one 5-word run containing it appears on
    fewer than ``SHARED_ON_PAGES`` landing pages. Runs rather than whole
    paragraphs, because template copy on a location site is usually
    name-swapped ("Visit our Bhopal centre…" / "Visit our Dehradun centre…"):
    a paragraph-level match calls every swapped paragraph unique, while here
    only the few words around the swapped name are.
    """
    pages: dict[str, list[str]] = {}
    for r in records:
        if r.get("type") in _UNIQUE_TYPES and r.get("status") == 200:
            key = norm_url(r["url"])
            pages[key] = [w.lower() for w in words(texts.get(key, ""))]
    df: dict[int, int] = {}
    runs: dict[str, list[int]] = {}
    for key, ws in pages.items():
        # hash() is salted per process, which is fine here: these are counted
        # and discarded within one call, never stored or compared across runs.
        hs = [hash(" ".join(ws[i:i + _SHINGLE])) for i in range(len(ws) - _SHINGLE + 1)]
        runs[key] = hs
        for h in set(hs):
            df[h] = df.get(h, 0) + 1
    for r in records:
        key = norm_url(r["url"])
        if key not in pages:
            continue
        ws, hs = pages[key], runs[key]
        if len(ws) < _SHINGLE:
            r["unique_word_count"] = len(ws)
            continue
        own = [df[h] < SHARED_ON_PAGES for h in hs]
        r["unique_word_count"] = sum(
            1 for j in range(len(ws))
            if any(own[i] for i in range(max(0, j - _SHINGLE + 1), min(j, len(hs) - 1) + 1))
        )


# --------------------------------------------------------------------------- #
# The crawl
# --------------------------------------------------------------------------- #

def crawl(domain: str, url_map: dict[str, dict], *, on_page=None,
          cli: httpx.Client | None = None) -> tuple[list[dict], dict[str, str]]:
    """Fetch and dissect every URL in ``url_map``. Returns (records, {norm_url: main_text}).

    Off-site URLs listed in a sitemap are recorded but not fetched — a sitemap
    may only list its own host's pages, and reaching out to another domain on
    the strength of a sitemap entry is not this crawler's business.

    Raises :class:`CrawlUnreliable` instead of returning a crawl in which too
    many pages got no answer — see ``MIN_REACHED``.
    """
    own = cli is None
    cli = cli or _client()
    texts: dict[str, str] = {}

    def one(key: str, entry: dict) -> tuple[dict, str]:
        url = entry["url"]
        if not _same_site(url, domain):
            return ({
                "url": url, "final_url": url, "status": 0, "error": "off-site URL in sitemap",
                "sitemaps": entry["sitemaps"], "lastmod": entry.get("lastmod"),
                "type": "offsite", "subtype": "offsite", "chain": [],
            }, "")
        return dissect(fetch(cli, url), domain, entry["sitemaps"], entry.get("lastmod"))

    by_key: dict[str, dict] = {}
    try:
        pool = ThreadPoolExecutor(max_workers=CONCURRENCY)
        try:
            futures = {pool.submit(one, k, v): k for k, v in url_map.items()}
            answered = unreached = 0
            for fut in as_completed(futures):
                record, text = fut.result()
                by_key[futures[fut]] = record
                if text:
                    texts[norm_url(record["url"])] = text
                if on_page:
                    on_page(record)
                answered += 1
                unreached += bool(record.get("network_error"))
                if answered == EARLY_SAMPLE and unreached >= EARLY_ABORT * EARLY_SAMPLE:
                    raise CrawlUnreliable(
                        f"{unreached} of the first {answered} pages got no answer from here "
                        f"({record.get('error') or 'network error'}). That is a network problem on this "
                        "machine or at the site's host, not an SEO finding, so the crawl stopped and "
                        "nothing was published; the last complete audit is still shown.")
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        # A second, gentler pass over the pages that got no answer: two at a
        # time, after a pause. Transient drops recover here; what is still
        # unreachable after this is either a real fault or an outage, and the
        # guard below decides which.
        retry = [k for k, r in by_key.items() if r.get("network_error")]
        if retry:
            time.sleep(5)
            with ThreadPoolExecutor(max_workers=2) as slow:
                for k, (record, text) in zip(retry, slow.map(lambda k: one(k, url_map[k]), retry)):
                    by_key[k] = record
                    if text:
                        texts[norm_url(record["url"])] = text
    finally:
        if own:
            cli.close()

    records = list(by_key.values())
    mine = [r for r in records if r.get("type") != "offsite"]
    lost = [r for r in mine if r.get("network_error")]
    if mine and len(lost) > max(3, (1 - MIN_REACHED) * len(mine)):
        raise CrawlUnreliable(
            f"{len(lost)} of {len(mine)} pages got no answer from here "
            f"({lost[0].get('error') or 'network error'}). Too many to be the site's own fault, so "
            "nothing was published; the last complete audit is still shown. Run it again once the "
            "connection is stable — if it keeps happening, the server is dropping parallel requests.")

    # Inbound internal links, computed from the whole crawl. This is what lets
    # the sitemap audit tell a page nobody links to from one that is merely
    # missing from the sitemap.
    inbound: dict[str, int] = {}
    for r in records:
        src = norm_url(r["url"])
        for t in r.get("internal_links") or []:
            if t != src:
                inbound[t] = inbound.get(t, 0) + 1
    for r in records:
        r["inlinks"] = inbound.get(norm_url(r["url"]), 0)
        final = norm_url(r.get("final_url") or r["url"])
        if final != norm_url(r["url"]):
            r["inlinks"] = max(r["inlinks"], inbound.get(final, 0))

    _mark_unique(records, texts)
    records.sort(key=lambda r: r["url"])
    return records, texts
