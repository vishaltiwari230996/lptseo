"""The deep audit's diagnostics, pinned offline.

Each test states one precise behaviour a real audit depends on. Several pin a
defect that shipped once and must not come back: the sitemap audit that
missed 815 URLs because it did not recurse into a nested index, the density
matcher that could not see a hyphenated keyword, the year that `keep_years`
still dropped. No network — HTTP is served by an in-memory transport and DNS
is answered by a stub, so the SSRF check still runs on every request.
"""
from __future__ import annotations

import socket

import httpx
import pytest

from seo_geo_agent import (
    cannibalization, crawl, jobs, keyphrase as kp, keyword_density as kd, landing_audit,
    sitemap_health,
)
from seo_geo_agent import sources


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def public_dns(monkeypatch):
    """Every hostname resolves to a public address, so the SSRF check passes for
    the fake site while still refusing IP literals in the private ranges."""
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        try:
            socket.inet_aton(host)
            return real(host, port, *a, **k)
        except OSError:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]

    monkeypatch.setattr(sources.socket, "getaddrinfo", fake)


def _site(pages: dict[str, tuple[int, str, dict]]) -> httpx.Client:
    """An httpx client over an in-memory site: {path: (status, body, headers)}."""

    def handler(request: httpx.Request) -> httpx.Response:
        status, body, headers = pages.get(request.url.path, (404, "not found", {}))
        return httpx.Response(status, text=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _rec(url, **kw):
    base = {
        "url": url, "final_url": url, "status": 200, "chain": [], "type": "landing",
        "subtype": "location", "title": "", "h1": [], "meta_description": "",
        "canonical_kind": "self", "noindex": False, "inlinks": 3, "word_count": 400,
        "word_count_body": 900, "headings": [], "images": 0, "img_no_dims": 0,
        "img_missing_alt": 0, "schema_types": ["LocalBusiness", "BreadcrumbList"],
        "schema_errors": 0, "viewport": True, "lang": "en", "og": {"title": "t",
        "description": "d", "image": "i"}, "first_100": "", "title_len": 0, "meta_desc_len": 0,
        "h2_count": 2, "internal_link_count": 40,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# Keywords
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("word,stem", [
    ("colleges", "college"), ("classes", "class"), ("universities", "university"),
    ("courses", "course"), ("branches", "branch"),
    ("status", "status"), ("analysis", "analysis"), ("class", "class"),
])
def test_stem_is_a_plural_stripper_and_nothing_more(word, stem):
    assert kp.stem(word) == stem


def test_keep_years_really_keeps_years():
    # Pins a defect: the bare-number filter used to drop years even when asked
    # to keep them, silently turning "clat 2026" into "clat".
    assert kp.tokens("CLAT 2026", keep_years=True) == ["clat", "2026"]
    assert kp.tokens("CLAT 2026") == ["clat"]


def test_brand_is_removed_as_a_phrase_not_word_by_word():
    # "law" is part of the brand AND a real keyword on a law site.
    assert kp.strip_brand("Law Entrance Coaching | Law Prep Tutorial", "Law Prep Tutorial") \
        == "Law Entrance Coaching"


def test_target_keyword_prefers_search_console_then_slug_then_h1():
    r = _rec("https://s.com/faridabad/clat-coaching", h1=["Best CLAT Coaching in Faridabad"])
    assert kp.target_keyword(r, "Brand")["phrase"] == "clat coaching faridabad"
    assert kp.target_keyword(r, "Brand")["source"] == "url"
    one_word = _rec("https://s.com/hyderabad", h1=["CLAT Coaching in Hyderabad"])
    assert set(kp.target_keyword(one_word, "Brand")["tokens"]) == {"clat", "coaching", "hyderabad"}
    assert kp.target_keyword(r, "Brand", "clat classes faridabad")["source"] == "search-console"


# --------------------------------------------------------------------------- #
# Density — the 150-word rule
# --------------------------------------------------------------------------- #

FOCUS = kp.tokens("law colleges karnataka", keep_years=True)


@pytest.mark.parametrize("text,exact,variant", [
    ("the best law colleges in Karnataka", 1, 0),          # function word between
    ("a law college in Karnataka", 1, 0),                  # singular
    ("top law-colleges in karnataka", 1, 0),               # hyphen = space (pinned defect)
    ("Karnataka law colleges are tough", 0, 1),            # reordered = close variant
    ("law colleges karnataka then law colleges karnataka", 2, 0),
    ("medical colleges in Karnataka", 0, 0),               # a different keyword
])
def test_mentions(text, exact, variant):
    e, v, _ = kd.find_mentions(text, FOCUS)
    assert (len(e), len(v)) == (exact, variant)


def test_hyphenated_compound_is_one_word_for_counting():
    _, _, n = kd.find_mentions("law-colleges in karnataka", FOCUS)
    assert n == 3


def test_windows_merge_a_short_tail_into_the_last_window():
    assert kd.windows(620) == [(0, 150), (150, 300), (300, 450), (450, 620)]
    assert kd.windows(700) == [(0, 150), (150, 300), (300, 450), (450, 600), (600, 700)]


def _article(mention_positions: list[int], total: int) -> str:
    toks = ["filler"] * total
    for p in mention_positions:
        toks[p:p + 3] = ["law", "colleges", "karnataka"]
    return " ".join(toks[:total])


def test_every_window_covered_is_a_pass():
    text = _article([10, 160, 310, 460], 600)
    res = kd.analyse(_rec("https://s.com/blog/x/"), text, "law colleges karnataka", "test")
    assert res["verdict"] == "pass" and res["gaps"] == []


def test_front_loaded_mentions_pass_on_average_but_fail_every_window():
    # Four mentions, all in the opening — enough on average for 600 words,
    # but windows 2–4 never mention the keyword. The rule the average misses.
    text = _article([0, 20, 40, 60], 600)
    res = kd.analyse(_rec("https://s.com/blog/x/"), text, "law colleges karnataka", "test")
    assert res["pass_average"] and not res["pass_every_window"]
    assert res["verdict"] == "pass-on-average"
    assert [g["window"] for g in res["gaps"]] == [2, 3, 4]
    assert res["gaps"][0]["words"] == "151–300"


def test_too_few_mentions_fail_and_report_the_shortfall():
    res = kd.analyse(_rec("https://s.com/blog/x/"), _article([5], 600),
                     "law colleges karnataka", "test")
    assert res["verdict"] == "fail" and res["required"] == 4 and res["shortfall"] == 3


def test_stuffing_guardrail():
    text = _article([0, 4, 8, 12, 16], 150)
    res = kd.analyse(_rec("https://s.com/blog/x/"), text, "law colleges karnataka", "test")
    assert res["stuffing"]


def test_a_hallucinated_focus_keyword_is_rejected():
    r = _rec("https://s.com/blog/clat/colleges/", title="Top CLAT Colleges")
    assert kd._grounded("clat colleges", r)
    assert not kd._grounded("medical entrance coaching", r)


# --------------------------------------------------------------------------- #
# Cannibalization
# --------------------------------------------------------------------------- #

def _brand():
    return {"id": "b", "name": "Brand", "domain": "s.com"}


def test_different_cities_do_not_cannibalize():
    recs = [_rec("https://s.com/jaipur/clat-coaching"), _rec("https://s.com/delhi/clat-coaching")]
    doc = cannibalization.build(_brand(), recs, {})
    assert doc["pairs"] == 0


def test_neighbourhood_page_copying_the_city_page_is_high():
    body = "clat coaching " + " ".join(f"word{i}" for i in range(300))
    recs = [_rec("https://s.com/jaipur/clat-coaching"),
            _rec("https://s.com/vaishali-nagar-jaipur/clat-coaching")]
    texts = {crawl.norm_url(r["url"]): body for r in recs}
    doc = cannibalization.build(_brand(), recs, texts)
    assert doc["by_kind"]["copied-spoke"] == 1 and doc["by_severity"]["high"] == 1


def test_distinct_hub_and_spoke_is_architecture_not_a_problem():
    recs = [_rec("https://s.com/jaipur/clat-coaching"),
            _rec("https://s.com/vaishali-nagar-jaipur/clat-coaching")]
    texts = {crawl.norm_url(recs[0]["url"]): " ".join(f"a{i}" for i in range(300)),
             crawl.norm_url(recs[1]["url"]): " ".join(f"b{i}" for i in range(300))}
    doc = cannibalization.build(_brand(), recs, texts)
    assert doc["by_kind"]["hub-spoke"] == 1 and doc["by_severity"]["high"] == 0
    assert doc["clusters"] == []  # low severity is summarised, not listed as a problem


def test_blog_post_against_landing_page_keeps_the_landing_page():
    recs = [_rec("https://s.com/clat-coaching", inlinks=1),
            _rec("https://s.com/blog/clat/coaching/", type="blog_post", inlinks=50)]
    doc = cannibalization.build(_brand(), recs, {})
    pair = cannibalization.latest_pairs("b")[0]
    assert pair["keep"] == "https://s.com/clat-coaching"   # commercial wins over inlinks
    assert doc["pairs"] == 1


# --------------------------------------------------------------------------- #
# Crawl — discovery and the SSRF guarantee
# --------------------------------------------------------------------------- #

def test_discovery_recurses_into_a_nested_index_and_flags_dead_declarations(public_dns):
    # The shape that fooled the first sitemap audit: robots.txt declares a dead
    # file, and the blog lives in an index nested inside the main index.
    site = {
        "/robots.txt": (200, "Sitemap: https://s.com/sitemap-0.xml\nSitemap: https://s.com/sitemap.xml", {}),
        "/sitemap.xml": (200, "<sitemapindex><sitemap><loc>https://s.com/blog/sitemap.xml</loc></sitemap>"
                              "<sitemap><loc>https://s.com/sitemap/sitemap-0.xml</loc></sitemap></sitemapindex>", {}),
        "/blog/sitemap.xml": (200, "<sitemapindex><sitemap><loc>https://s.com/blog/posts.xml</loc></sitemap></sitemapindex>", {}),
        "/blog/posts.xml": (200, "<urlset>" + "".join(
            f"<url><loc>https://s.com/blog/p{i}/</loc><lastmod>2026-01-01</lastmod></url>" for i in range(5)
        ) + "</urlset>", {}),
        "/sitemap/sitemap-0.xml": (200, "<urlset><url><loc>https://s.com/a</loc></url></urlset>", {}),
    }
    with _site(site) as cli:
        disc = crawl.discover("s.com", cli)
    assert len(disc["urls"]) == 6
    doc = sitemap_health.build(_brand(), disc, [])
    dead = next(i for i in doc["issues"] if i["code"] == "sitemap-declared-dead")
    assert dead["detail"][0]["real_file"] == "/sitemap/sitemap-0.xml"   # "wrong path", not "stale"


def test_crawler_refuses_a_redirect_into_the_private_range(public_dns):
    site = {"/go": (302, "", {"location": "http://169.254.169.254/latest/"})}
    with _site(site) as cli:
        got = crawl.fetch(cli, "https://s.com/go")
    assert got.status == 0 and got.error
    assert not got.network_error and got.attempts == 1   # a refusal is a decision: never retried


# --------------------------------------------------------------------------- #
# Crawl — our network failing is not the site failing
#
# A resolver failure mid-crawl once turned 1,059 of 1,140 live pages into
# "status 0", and the audit published that as the site's fault, overwriting a
# good report. These pin each layer that now stops it.
# --------------------------------------------------------------------------- #

@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(crawl, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(crawl.time, "sleep", lambda s: None)
    monkeypatch.setattr(crawl._Resolver, "LOOKUP_RETRIES", (0, 0))


def _flaky(fail: set[str], *, times: int = 99) -> httpx.Client:
    """A site whose paths in ``fail`` get no answer for the first ``times`` tries."""
    tries: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        tries[p] = tries.get(p, 0) + 1
        if p in fail and tries[p] <= times:
            raise httpx.ConnectError("could not resolve s.com")
        return httpx.Response(200, text=f"<html><title>{p}</title><body><h1>{p}</h1></body></html>",
                              headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_a_dropped_request_is_retried_not_reported(public_dns, no_wait):
    with _flaky({"/a"}, times=1) as cli:
        got = crawl.fetch(cli, "https://s.com/a")
    assert got.status == 200 and got.attempts == 2 and not got.network_error


def test_a_request_that_never_gets_an_answer_is_marked_as_ours(public_dns, no_wait):
    with _flaky({"/a"}) as cli:
        got = crawl.fetch(cli, "https://s.com/a")
    assert got.status == 0 and got.network_error and got.attempts == 3


def _urls(n: int) -> dict[str, dict]:
    return {f"s.com/p{i}": {"url": f"https://s.com/p{i}", "sitemaps": ["https://s.com/sitemap.xml"]}
            for i in range(n)}


def test_an_off_site_redirect_that_fails_is_not_retried(public_dns, no_wait):
    # Retrying would let a hostile sitemap aim this crawler's retries at a third party.
    calls = {"n": 0}

    def handler(request):
        if request.url.host == "victim.example":
            calls["n"] += 1
            raise httpx.ConnectError("refused by peer")
        return httpx.Response(301, headers={"location": "https://victim.example/expensive"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as cli:
        got = crawl.fetch(cli, "https://s.com/a")
    assert got.status == 0 and got.attempts == 1 and calls["n"] == 1


def test_sitemap_urls_past_the_cap_are_counted_not_crawled(public_dns, monkeypatch):
    monkeypatch.setattr(crawl, "MAX_URLS", 3)
    site = {"/robots.txt": (404, "", {}),
            "/sitemap.xml": (200, "<urlset>" + "".join(f"<url><loc>https://s.com/p{i}</loc></url>"
                                                      for i in range(5)) + "</urlset>", {})}
    with _site(site) as cli:
        disc = crawl.discover("s.com", cli)
    assert len(disc["urls"]) == 3 and disc["truncated"] == 2


def test_the_browser_refuses_subresources_on_private_addresses():
    # A page is free to reference anything; the browser that loads it for page
    # speed must not fetch cloud metadata or the internal network for it.
    pytest.importorskip("playwright")
    import asyncio

    from playwright.async_api import async_playwright
    from seo_geo_agent import page_speed as ps

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                ctx = await browser.new_context()
                blocked: list[str] = []
                await ps._guard(ctx, crawl._Resolver(), blocked)
                page = await ctx.new_page()
                await page.set_content('<img src="http://169.254.169.254/latest/meta-data/">'
                                       '<iframe src="http://127.0.0.1:6379/"></iframe>')
                await page.wait_for_timeout(800)
                return blocked
            finally:
                await browser.close()

    try:
        blocked = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 — no browser installed here
        pytest.skip(f"chromium unavailable: {exc}")
    assert any("169.254.169.254" in u for u in blocked) and any("127.0.0.1" in u for u in blocked)


def test_a_crawl_that_could_not_reach_the_site_is_refused_early(public_dns, no_wait):
    with _flaky({f"/p{i}" for i in range(300)}) as cli:
        with pytest.raises(crawl.CrawlUnreliable, match="first 40 pages"):
            crawl.crawl("s.com", _urls(300), cli=cli)


def test_a_crawl_with_too_many_unreached_pages_is_not_published(public_dns, no_wait):
    # 10 of 100 never answer: past the early sample, over the 3% the guard allows.
    with _flaky({f"/p{i}" for i in range(90, 100)}) as cli:
        with pytest.raises(crawl.CrawlUnreliable, match="10 of 100"):
            crawl.crawl("s.com", _urls(100), cli=cli)


def test_a_few_unreachable_pages_are_published_as_findings(public_dns, no_wait):
    with _flaky({"/p7", "/p8"}) as cli:
        records, _ = crawl.crawl("s.com", _urls(100), cli=cli)
    lost = [r for r in records if r["status"] != 200]
    assert len(records) == 100 and {r["url"] for r in lost} == {"https://s.com/p7", "https://s.com/p8"}
    assert all(r["network_error"] for r in lost)


def test_the_slow_second_pass_recovers_transient_drops(public_dns, no_wait):
    # Every page fails its first three tries — i.e. the whole first pass — and
    # answers on the fourth, which is the recovery pass.
    with _flaky({f"/p{i}" for i in range(60, 100)}, times=3) as cli:
        records, _ = crawl.crawl("s.com", _urls(100), cli=cli)
    assert all(r["status"] == 200 for r in records)


def test_an_unreachable_own_sitemap_stops_discovery(public_dns, no_wait):  # noqa: F811
    site = {"/robots.txt": (200, "Sitemap: https://s.com/sitemap.xml", {})}

    def handler(request):
        if request.url.path == "/sitemap.xml":
            raise httpx.ReadTimeout("timed out")
        status, body, headers = site.get(request.url.path, (404, "", {}))
        return httpx.Response(status, text=body, headers=headers)

    with httpx.Client(transport=httpx.MockTransport(handler)) as cli:
        with pytest.raises(crawl.CrawlUnreliable, match="URL list is incomplete"):
            crawl.discover("s.com", cli)


def test_the_deep_audit_keeps_the_last_good_report_when_the_crawl_is_unreliable(monkeypatch):
    from seo_geo_agent import deep_audit, state

    good = {"at": "2026-09-10", "landing": {"avg_score": 69}}
    state.save("deepaudit-b", good)
    monkeypatch.setattr(crawl, "discover", lambda d, c=None: {"urls": {"s.com/": {}}, "sitemaps": [1]})

    def dead(*a, **k):
        raise crawl.CrawlUnreliable("1059 of 1140 pages got no answer from here")

    monkeypatch.setattr(crawl, "crawl", dead)
    progress = jobs.Progress("deep", "b")
    with pytest.raises(crawl.CrawlUnreliable):
        deep_audit.run(_brand(), progress)
    assert deep_audit.latest("b") == good
    assert jobs.load_list("crawl-b")[0] == []


def test_resolver_keeps_a_good_answer_through_a_resolver_outage(monkeypatch, no_wait):
    calls = {"n": 0}

    def flaky(host, port, *a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise socket.gaierror("temporary failure in name resolution")
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2600:9000::1", port, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("99.86.30.76", port))]

    monkeypatch.setattr(crawl.socket, "getaddrinfo", flaky)
    r = crawl._Resolver()
    assert r.addresses("www.s.com", 443) == ["99.86.30.76", "2600:9000::1"]   # IPv4 first
    for _ in range(50):
        assert r.addresses("www.s.com", 443)[0] == "99.86.30.76"
    assert calls["n"] == 1


def test_resolver_refuses_private_addresses_and_does_not_remember_the_refusal(monkeypatch):
    answers = iter(["10.0.0.5", "93.184.216.34"])
    monkeypatch.setattr(crawl.socket, "getaddrinfo",
                        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), p))])
    r = crawl._Resolver()
    with pytest.raises(httpx.ConnectError, match="refusing"):
        r.check("https://s.com/")
    r.check("https://s.com/")   # asked again, not answered from a remembered refusal


def test_client_connects_to_the_checked_address(monkeypatch):
    import httpcore

    cli = crawl._client()
    try:
        backend = cli._transport._pool._network_backend
        assert isinstance(backend, crawl._PinnedBackend)
    finally:
        cli.close()
    seen = []

    def connect(self, host, port, **kw):
        seen.append(host)
        raise httpcore.ConnectError("stop here")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    monkeypatch.setattr(crawl.socket, "getaddrinfo",
                        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", p))])
    with pytest.raises(httpcore.ConnectError):
        backend.connect_tcp("www.s.com", 443)
    assert seen == ["93.184.216.34"]   # the IP that was checked, never the name again


def test_dissect_extracts_the_facts_the_audits_need():
    html = (
        "<html lang=en><head><title>CLAT Coaching in Jaipur | Brand</title>"
        '<meta name="description" content="Join us.">'
        '<meta name="robots" content="noindex,follow">'
        '<link rel="canonical" href="https://s.com/other">'
        '<script src="/a.js"></script>'
        '<script type="application/ld+json">{"@type":"LocalBusiness"}</script>'
        '<script type="application/ld+json">{broken</script></head>'
        "<body><h1>CLAT Coaching</h1><h3>Skipped</h3>"
        '<img src="a.png"><img src="b.png" alt="x" width="1" height="1">'
        '<a href="/x">click here</a></body></html>'
    )
    f = crawl.Fetched(url="https://s.com/jaipur/clat-coaching", final_url="https://s.com/jaipur/clat-coaching",
                      status=200, headers={"content-type": "text/html; charset=utf-8"},
                      content=html.encode())
    r, _ = crawl.dissect(f, "s.com", [], None)
    assert r["noindex"] and r["canonical_kind"] == "other"
    assert r["schema_types"] == ["LocalBusiness"] and r["schema_errors"] == 1
    assert r["img_missing_alt"] == 1 and r["img_no_dims"] == 1
    assert r["heading_skips"] == 1 and r["head_js_blocking"] == 1 and r["generic_anchors"] == 1


# --------------------------------------------------------------------------- #
# Sitemap diagnosis
# --------------------------------------------------------------------------- #

def test_http_resources_in_blog_posts_are_reported_with_where_they_point():
    # Found by the page-speed run, missed by every report: 111 blog posts with
    # 568 article images on http://13.127.188.157 that the browser blocks.
    ip_img = "http://13.127.188.157/lawprep-blog/public/storage/media-master/cbse-class-11.webp"
    disc = {"sitemaps": [], "urls": {
        "s.com/blog/cbse/class-11": {"url": "https://s.com/blog/cbse/class-11/", "sitemaps": ["https://s.com/sm.xml"]},
        "s.com/jaipur": {"url": "https://s.com/jaipur", "sitemaps": ["https://s.com/sm.xml"]},
    }}
    recs = [_rec("https://s.com/blog/cbse/class-11/", type="blog_post", mixed_content=6,
                 mixed_content_urls=[ip_img] * 5, title="CBSE Class 11"),
            _rec("https://s.com/jaipur", title="Jaipur")]
    doc = sitemap_health.build(_brand(), disc, recs)
    issue = next(i for i in doc["issues"] if i["code"] == "url-mixed-content")
    assert issue["severity"] == "high" and issue["count"] == 1
    assert "13.127.188.157, a raw server IP" in issue["why"]
    assert issue["detail"][0]["url"] == "/blog/cbse/class-11/" and issue["detail"][0]["examples"][0] == ip_img


def test_sitemap_contradictions_are_each_named_per_url():
    disc = {
        "sitemaps": [{"url": "https://s.com/sm.xml", "status": 200, "kind": "urlset",
                      "declared_in_robots": True, "parents": [], "depth": 0, "bytes": 1,
                      "url_count": 4, "child_count": 0, "lastmod_count": 0,
                      "lastmod_invalid": 0, "lastmod_future": 0, "redirected": False}],
        "robots": {"status": 200, "declared": ["https://s.com/sm.xml"]},
        "urls": {crawl.norm_url(u): {"url": u, "sitemaps": ["https://s.com/sm.xml"], "lastmod": None}
                 for u in ("https://s.com/a", "https://s.com/b", "https://s.com/c", "https://s.com/d")},
    }
    recs = [
        _rec("https://s.com/a", noindex=True),
        _rec("https://s.com/b", canonical_kind="other", canonical="https://s.com/a"),
        _rec("https://s.com/c", status=404),
        _rec("https://s.com/d", chain=[{"url": "https://s.com/d", "status": 301}],
             final_url="https://s.com/e"),
    ]
    doc = sitemap_health.build(_brand(), disc, recs)
    codes = {i["code"]: i for i in doc["issues"]}
    assert codes["url-noindex"]["detail"][0]["url"] == "/a"
    assert codes["url-canonical-elsewhere"]["detail"][0]["canonical"] == "/a"
    assert codes["url-non200"]["detail"][0]["url"] == "/c"
    assert codes["url-redirect"]["detail"][0]["final"] == "/e"
    assert "lastmod-missing" in codes


def test_slash_variants_that_both_serve_200_are_a_duplicate():
    probes = {"slash_checks": [{"section": "blog", "listed": "https://s.com/blog/p/",
                                "variant": "https://s.com/blog/p", "status": 200,
                                "redirects_to_listed": False, "canonical_to_listed": False}]}
    doc = sitemap_health.build(_brand(), {"sitemaps": [], "robots": {"status": 200}, "urls": {}}, [], probes)
    assert any(i["code"] == "slash-duplicates" for i in doc["issues"])


# --------------------------------------------------------------------------- #
# Landing audit
# --------------------------------------------------------------------------- #

def _audit(r, **kw):
    args = dict(brand_name="Brand", text="", dupes={}, title_groups={}, meta_groups={},
                h1_groups={}, speed=None, gsc_top=None, robots_blocked=False)
    args.update(kw)
    return {f["check"]: f for f in landing_audit.audit_page(r, **args)["findings"]}


def test_landing_checks_fire_with_their_evidence():
    r = _rec("https://s.com/jaipur/clat-coaching", title="Welcome", title_len=7,
             h1=["Welcome"], images=10, img_no_dims=10, schema_errors=1, word_count=90)
    found = _audit(r)
    assert "jaipur" in found["title-keyword"]["evidence"]
    assert found["img-dimensions"]["severity"] == "medium"
    assert found["schema-broken"]["severity"] == "high"
    assert found["thin-content"]["severity"] == "high"


def test_duplicate_titles_name_the_other_pages():
    r = _rec("https://s.com/a/clat-coaching", title="CLAT Coaching", title_len=13)
    groups = {"clat coaching": ["https://s.com/a/clat-coaching", "https://s.com/b/clat-coaching"]}
    assert "https://s.com/b/clat-coaching" in _audit(r, title_groups=groups)["title-duplicate"]["evidence"]


def test_a_check_failing_on_most_landing_pages_is_a_template_issue():
    recs = [_rec(f"https://s.com/c{i}/clat-coaching", images=5, img_no_dims=5) for i in range(10)]
    doc = landing_audit.build(_brand(), recs, {})
    dims = next(m for m in doc["issues"] if m["check"] == "img-dimensions")
    assert dims["template"] and dims["count"] == 10


def test_near_duplicates_are_exact_jaccard():
    body = " ".join(f"w{i}" for i in range(200))
    pages = [_rec("https://s.com/a"), _rec("https://s.com/b")]
    d = landing_audit.near_duplicates(pages, {crawl.norm_url(p["url"]): body for p in pages})
    assert d["https://s.com/a"][0] == ("https://s.com/b", 1.0)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def test_chunked_list_round_trips_and_a_shrinking_save_drops_the_old_tail():
    # Padded so a handful of items already cross MAX_CHUNK_BYTES and force more
    # than one chunk — chunking is by measured size now, not a fixed count.
    pad = "x" * 300_000
    items = [{"i": i, "pad": pad} for i in range(7)]
    jobs.save_list("t", items)
    got, meta = jobs.load_list("t")
    assert got == items and meta["chunks"] > 1
    jobs.save_list("t", items[:2])
    got, meta = jobs.load_list("t")
    assert got == items[:2] and meta["chunks"] == 1


# --------------------------------------------------------------------------- #
# Numbered series — pins the defect that produced 946 false "same-target" pairs
# --------------------------------------------------------------------------- #

def test_a_numbered_series_of_distinct_documents_is_not_cannibalization():
    # /papers/2025-pdf/ and /papers/2026-pdf/ are different documents with their
    # own search demand. Dropping the year used to give them identical
    # signatures, which on a real site produced 946 false high-severity pairs.
    recs = [_rec("https://s.com/blog/clat/papers/2025-pdf/", type="blog_post"),
            _rec("https://s.com/blog/clat/papers/2026-pdf/", type="blog_post")]
    texts = {crawl.norm_url(recs[0]["url"]): " ".join(f"a{i}" for i in range(300)),
             crawl.norm_url(recs[1]["url"]): " ".join(f"b{i}" for i in range(300))}
    assert cannibalization.build(_brand(), recs, texts)["pairs"] == 0


def test_a_numbered_series_that_copies_itself_is_flagged():
    recs = [_rec("https://s.com/blog/clat/notification/2025/", type="blog_post"),
            _rec("https://s.com/blog/clat/notification/2026/", type="blog_post")]
    body = " ".join(f"w{i}" for i in range(300))
    texts = {crawl.norm_url(r["url"]): body for r in recs}
    doc = cannibalization.build(_brand(), recs, texts)
    assert doc["by_kind"]["numbered-series"] == 1 and doc["by_severity"]["high"] == 1


def test_keywords_are_shown_in_the_page_words_not_as_stems():
    r = _rec("https://s.com/hauz-khas-delhi", h1=["CLAT Coaching in Hauz Khas"])
    assert "khas" in kp.target_keyword(r, "Brand")["phrase"].split()


def test_extraction_does_not_depend_on_what_was_extracted_before():
    # Pins a defect: trafilatura's deduplicate=True keeps a process-wide cache,
    # so across a crawl of near-identical centre pages the first few kept their
    # 277 words and the fifth came back with 0.
    html = ("<html><body><main><h1>Centre</h1>"
            + "".join(f"<p>Premium learning environment with expert faculty number {i} for law aspirants "
                      f"who want the best results.</p>" for i in range(12))
            + "</main></body></html>")
    counts = [len(crawl.words(crawl._main_text(html))) for _ in range(6)]
    assert counts[0] > 100 and len(set(counts)) == 1, counts


def test_unique_words_discount_template_copy_shared_across_landing_pages():
    template = " ".join(f"shared template sentence number {i} about our premium coaching." for i in range(20))
    own = {c: " ".join(f"{c}fact{i}" for i in range(40)) for c in ("bhopal", "indore", "agra", "patna")}
    recs = [_rec(f"https://s.com/{c}") for c in own]
    texts = {crawl.norm_url(f"https://s.com/{c}"): f"{template} Visit our {c} centre today. {own[c]}"
             for c in own}
    crawl._mark_unique(recs, texts)
    for r in recs:
        total = len(crawl.words(texts[crawl.norm_url(r["url"])]))
        # the 40 page-specific words plus the few around the swapped name — not the 160 shared
        assert 40 <= r["unique_word_count"] <= 60 < total, (r["url"], r["unique_word_count"], total)


def test_a_name_swapped_paragraph_is_not_unique_content():
    # "Visit our Bhopal centre…" vs "Visit our Indore centre…": only the words
    # within reach of the swapped name count as the page's own.
    para = "our centre has modern classrooms expert faculty and a library open every day of the week"
    recs = [_rec(f"https://s.com/{c}") for c in ("bhopal", "indore", "agra")]
    texts = {crawl.norm_url(r["url"]): f"{para} in {r['url'].rsplit('/', 1)[1]} {para}" for r in recs}
    crawl._mark_unique(recs, texts)
    assert all(r["unique_word_count"] <= 9 for r in recs)   # the name and four words either side


def test_thin_content_is_judged_on_unique_words_and_says_so():
    r = _rec("https://s.com/bhopal", word_count=277, unique_word_count=36, word_count_body=1028)
    res = landing_audit.audit_page(r, brand_name="Brand", text="", dupes={}, title_groups={},
                                   meta_groups={}, h1_groups={}, speed=None, gsc_top=None,
                                   robots_blocked=False)
    f = next(f for f in res["findings"] if f["check"] == "thin-content")
    assert f["severity"] == "high" and f["title"] == "Only 36 words unique to this page"
    assert "277 words of main content, only 36 unique" in f["evidence"]


def test_a_login_modal_is_never_mistaken_for_the_main_content():
    # Pins a defect: on thin pages the login popup was the biggest text block,
    # so two different pages "shared" it and scored as 100% duplicates.
    modal = ('<div id="login-modal" class="login-form modal"><div class="modal-dialog">'
             + "Enter your mobile number to begin your journey. MORE WAYS TO LOGIN. " * 30
             + "</div></div>")
    content = "<main><h1>Gujarat Judiciary</h1><p>" + "Daily current affairs for the exam. " * 20 + "</p></main>"
    html = f"<html><body>{modal}{content}</body></html>"
    text = crawl._main_text(html)
    assert "MORE WAYS TO LOGIN" not in text and "current affairs" in text


def test_collapsed_faq_panels_are_kept_as_content():
    html = ("<html><body><main><h2>FAQ</h2><div aria-hidden='true'>"
            + "The eligibility for the exam is a law degree. " * 15
            + "</div></main></body></html>")
    assert "eligibility" in crawl._main_text(html)


def test_a_redirect_chain_through_http_is_caught_even_though_it_ends_on_https():
    # The real /blog chain: https -> http -> https. The destination is right,
    # so a destination-only check passes it; the middle hop is sent in the clear.
    chain = [{"url": "https://s.com/blog", "status": 301}, {"url": "http://s.com/blog/", "status": 301}]
    assert crawl.downgrades(chain, "https://s.com/blog/")
    assert not crawl.downgrades([{"url": "https://s.com/a", "status": 301}], "https://s.com/b")


def test_pages_too_thin_to_compare_are_not_reported_as_zero_percent_similar():
    recs = [_rec("https://s.com/jaipur/clat-coaching"), _rec("https://s.com/vaishali-nagar-jaipur/clat-coaching")]
    doc = cannibalization.build(_brand(), recs, {crawl.norm_url(recs[0]["url"]): "too short"})
    assert cannibalization.latest_pairs("b")[0]["content_similarity"] is None
    assert doc["by_severity"]["high"] == 0


def test_focus_keywords_are_reused_until_the_post_changes(monkeypatch):
    # Pins reproducibility: the model is asked once per post, and asked again
    # only when the title/H1/URL it answered from actually changes.
    calls = []

    def fake_llm(system, prompt, **kw):
        calls.append(prompt)
        return {"items": [{"i": 0, "keyword": "clat colleges"}]}

    monkeypatch.setattr(sources, "llm_json", fake_llm)
    post = _rec("https://s.com/blog/clat/colleges/", type="blog_post", title="Top CLAT Colleges")
    kd.focus_keywords([post], "Brand", {}, brand_id="b")
    kd.focus_keywords([post], "Brand", {}, brand_id="b")
    assert len(calls) == 1
    kd.focus_keywords([dict(post, title="CLAT Colleges Ranked")], "Brand", {}, brand_id="b")
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# Page speed — a popup is not the page
#
# Measured on lawpreptutorial.com/delhi/du-llb-coaching: the page's content
# painted at 2.06s, then a promo modal opened at 16.06s and, being the largest
# thing painted, became the LCP. One number blamed the page for the popup.
# --------------------------------------------------------------------------- #

_DU_LLB = [
    {"t": 1896, "size": 2880, "el": "<img> logo.svg", "overlay": None},
    {"t": 2056, "size": 28875, "el": '<p> "Prepare for Delhi University’s 3-Year LLB entrance"', "overlay": None},
    {"t": 16056, "size": 86400, "el": "<img> 3260404110_CLAT_EXPRESS_588x393.jpg (1).jpeg",
     "overlay": "div#popup_banner.modal.fade"},
]


def test_a_popup_lcp_is_separated_from_the_content_lcp():
    from seo_geo_agent import page_speed as ps

    t = ps._timeline(_DU_LLB, 16056.0)
    assert t["lcp_overlay"] == "div#popup_banner.modal.fade"
    assert t["content_lcp_ms"] == 2056 and t["content_lcp_rating"] == "good"
    assert t["content_lcp_element"].startswith("<p>")


def test_without_a_popup_content_lcp_is_the_lcp():
    from seo_geo_agent import page_speed as ps

    t = ps._timeline(_DU_LLB[:2], 2056.0)
    assert t["lcp_overlay"] is None and t["content_lcp_ms"] == 2056


def test_landing_audit_blames_the_popup_and_rates_the_content_on_its_own():
    speed = {"lcp_ms": 16056, "lcp_element": "<img> CLAT_EXPRESS.jpeg", "content_lcp_ms": 2056,
             "content_lcp_element": "<p> \"Prepare…\"", "lcp_overlay": "div#popup_banner.modal",
             "cls": 0.567, "bytes": 800_000,
             "shifts": [{"t": 2217, "v": 0.225, "nodes": ["div.col-md-6", "div.col-lg-7"]}]}
    r = _rec("https://s.com/delhi/du-llb-coaching", title="DU LLB Coaching in Delhi | Brand")
    res = landing_audit.audit_page(r, brand_name="Brand", text="", dupes={}, title_groups={},
                                   meta_groups={}, h1_groups={}, speed=speed, gsc_top=None,
                                   robots_blocked=False)
    by = {f["check"]: f for f in res["findings"]}
    assert by["lcp-popup"]["severity"] == "high" and "2.1s" in by["lcp-popup"]["fix"]
    assert "lcp" not in by                                   # 2.1s content is good
    assert "div.col-md-6, div.col-lg-7 moved 0.225 at 2.2s" in by["cls"]["evidence"]


def test_bytes_come_from_the_network_log_not_resource_timing():
    # Resource Timing says 0 for a cross-origin file without Timing-Allow-Origin
    # (GTM, Trustpilot, an S3 bucket). The network log has the real size.
    from seo_geo_agent import page_speed as ps

    net = {
        "1": {"url": "https://www.s.com/", "type": "Document", "bytes": 40_000},
        "2": {"url": "https://www.googletagmanager.com/gtm.js?id=X", "type": "Script", "bytes": 95_000},
        "3": {"url": "https://bucket.s3.ap-south-1.amazonaws.com/banner.jpg", "type": "Image", "bytes": 120_000},
        "4": {"url": "https://www.s.com/app.css", "type": "Stylesheet", "bytes": 30_000},
        "5": {"url": "data:image/png;base64,AAAA", "type": "Image", "bytes": 0},
    }
    timings = [{"url": "https://www.googletagmanager.com/gtm.js?id=X", "ms": 900, "type": "script", "size": 0}]
    got = ps._account(net, timings, 0, "s.com")
    assert got["bytes"] == 285_000 and got["requests"] == 4          # data: URIs are not requests
    assert got["third_party_bytes"] == 95_000 and got["third_party_hosts"][0]["host"] == "googletagmanager.com"
    assert got["cdn_bytes"] == 120_000                                 # the site's own bucket, not a third party
    assert got["heaviest"][0]["party"] == "cdn" and got["heaviest"][1]["ms"] == 900


def test_chromium_is_pinned_to_the_checked_address_of_the_sites_own_hosts(monkeypatch):
    # One cached DNS failure turned 802 navigations into instant
    # ERR_NAME_NOT_RESOLVED; pinned, the browser never asks the OS.
    from seo_geo_agent import page_speed as ps

    answers = {"www.s.com": ["2600:9000::1", "99.86.30.76"], "s.com": ["65.0.210.39"]}
    r = crawl._Resolver()
    monkeypatch.setattr(r, "addresses", lambda h, p: sorted(answers[h], key=lambda a: ":" in a))
    assert ps._pins({"www.s.com", "s.com"}, r) == "MAP s.com 65.0.210.39,MAP www.s.com 99.86.30.76"


def test_a_run_mostly_unmeasured_is_not_reported_as_done():
    from seo_geo_agent import page_speed as ps

    ok = {"lcp_ms": 2000, "error": None}
    dns = {"error": "Error: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.s.com/blog/x/"}
    verdict = ps._unmeasured([ok] * 337 + [dns] * 802)
    assert verdict and verdict.startswith("802 of 1139 pages could not be measured")
    assert "network or DNS failed" in verdict and "337 measured pages are kept" in verdict
    assert ps._unmeasured([ok] * 1130 + [dns] * 9) is None          # under 3%: findings, not failure


def test_a_load_our_network_broke_is_not_a_measurement():
    from seo_geo_agent import page_speed as ps

    assert ps._ours("net::ERR_NAME_NOT_RESOLVED") and not ps._ours("net::ERR_CONNECTION_REFUSED")
    assert not ps._usable({"lcp_ms": 1500, "error": None, "degraded": True})
    assert ps._usable({"lcp_ms": 1500, "error": None, "degraded": False})


def test_observer_tells_a_late_modal_from_a_fixed_header():
    # The overlay rule, run in a real browser: a position:fixed header is part
    # of the page; a fixed modal that opens later is a popup.
    pytest.importorskip("playwright")
    import asyncio

    from playwright.async_api import async_playwright
    from seo_geo_agent import page_speed as ps

    html = """<html><head><script>%s</script></head><body style="margin:0">
      <header style="position:fixed;top:0;left:0;right:0;height:48px;background:#fff">
        <b style="font-size:20px">Brand</b></header>
      <main style="padding-top:64px"><p style="font-size:17px;margin:0 12px">%s</p></main>
      <script>
        const c = document.createElement('canvas'); c.width = 380; c.height = 560;
        const g = c.getContext('2d'), d = g.createImageData(380, 560);
        for (let i = 0; i < d.data.length; i++) d.data[i] = (Math.random() * 255) | 0;
        g.putImageData(d, 0, 0);
        const src = c.toDataURL('image/png');
        setTimeout(() => document.body.insertAdjacentHTML('beforeend',
          '<div id="popup_banner" class="modal" style="position:fixed;inset:0;background:rgba(0,0,0,.5)">'
          + '<img src="' + src + '" width="380" height="560"></div>'), 600);
      </script></body></html>""" % (ps._OBSERVE, "Real content that the visitor came for. " * 30)

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 412, "height": 823})
                await page.set_content(html)
                await page.wait_for_timeout(2000)
                return await page.evaluate("[window.__cands, window.__lcp]")
            finally:
                await browser.close()

    try:
        cands, lcp = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 — no browser installed here
        pytest.skip(f"chromium unavailable: {exc}")
    t = ps._timeline(cands, lcp)
    assert t["lcp_overlay"] and t["lcp_overlay"].startswith("div#popup_banner")
    assert t["content_lcp_element"].startswith("<p>")
    assert all(c["overlay"] is None for c in cands[:-1])   # the fixed header is not a popup


def test_a_run_that_sees_no_posts_does_not_erase_remembered_keywords(monkeypatch):
    # Pins a defect: the memo kept only the posts seen in the current run, so
    # one broken crawl (0 posts) wiped all 789 and the model re-chose them.
    calls = []
    monkeypatch.setattr(sources, "llm_json",
                        lambda s, p, **kw: calls.append(p) or {"items": [{"i": 0, "keyword": "clat colleges"}]})
    post = _rec("https://s.com/blog/clat/colleges/", type="blog_post", title="Top CLAT Colleges")
    kd.focus_keywords([post], "Brand", {}, brand_id="b")
    kd.focus_keywords([], "Brand", {}, brand_id="b")
    kd.focus_keywords([post], "Brand", {}, brand_id="b")
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Concurrency — both pinned from a live run through the API
# --------------------------------------------------------------------------- #

def test_starting_a_running_job_again_returns_it_instead_of_deadlocking():
    import threading

    release = threading.Event()
    first = jobs.start("t", "b", lambda progress: release.wait(5))
    result: dict = {}

    def second():
        result["doc"] = jobs.start("t", "b", lambda progress: None)

    t = threading.Thread(target=second, daemon=True)
    t.start()
    t.join(3)
    # Before the fix the second start held the lock and waited on itself forever.
    assert not t.is_alive(), "second start() deadlocked"
    assert result["doc"]["status"] == "running"
    assert jobs.status("t", "b")["status"] == "running"   # a poll does not hang either
    release.set()
    assert first["status"] == "running"


def test_reads_never_see_a_half_written_document():
    import threading

    from seo_geo_agent import state

    stop = threading.Event()
    errors: list[Exception] = []
    big = {"log": ["x" * 200] * 400}

    def writer():
        n = 0
        while not stop.is_set():
            state.save("race", {**big, "n": n})
            n += 1

    def reader():
        while not stop.is_set():
            try:
                doc = state.load("race")
                assert doc is None or "n" in doc
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader),
               threading.Thread(target=reader)]
    for t in threads:
        t.start()
    threading.Event().wait(1.0)
    stop.set()
    for t in threads:
        t.join(3)
    assert errors == []


# --------------------------------------------------------------------------- #
# Calibration — each pinned from a finding that did not survive review
# --------------------------------------------------------------------------- #

def test_a_single_topper_profile_does_not_compete_with_the_toppers_page():
    # /blog/topper/air-15-clat-2025/ is one student's profile. Nobody searching
    # "clat toppers" wants it, so it is not a rival of /our-toppers/clat.
    recs = [_rec("https://s.com/our-toppers/clat", subtype="proof"),
            _rec("https://s.com/blog/topper/air-15-clat-2025/", type="blog_post")]
    doc = cannibalization.build(_brand(), recs, {})
    assert doc["by_kind"]["blog-vs-landing"] == 0


def test_a_blog_post_one_word_away_from_a_landing_page_does_compete():
    recs = [_rec("https://s.com/clat-coaching"),
            _rec("https://s.com/blog/clat-coaching-tips/", type="blog_post")]
    doc = cannibalization.build(_brand(), recs, {})
    assert doc["by_kind"]["blog-vs-landing"] == 1


@pytest.mark.parametrize("raw,clean", [
    ("How to Prepare for UP Judiciary Exam", "up judiciary exam"),
    ("prepare up judiciary exam", "up judiciary exam"),
    ("what is clat", "clat"),
    ("clat preparation", "clat preparation"),     # a real topic, left alone
])
def test_focus_keywords_are_topics_not_sentences(raw, clean):
    assert kd._clean_keyword(raw) == clean


def test_density_is_occurrences_over_words():
    # 4 mentions of a 3-word keyword in 600 words = 0.67%, not 2%.
    res = kd.analyse(_rec("https://s.com/blog/x/"), _article([10, 160, 310, 460], 600),
                     "law colleges karnataka", "test")
    assert res["density_pct"] == 0.67 and not res["stuffing"]


def test_the_start_response_describes_the_new_run_not_the_last_one():
    import threading
    import time as _t

    jobs.start("r", "b", lambda progress: None)
    for _ in range(100):
        if jobs.status("r", "b")["status"] == "done":
            break
        _t.sleep(0.02)
    old = jobs.status("r", "b")
    assert old["status"] == "done"
    _t.sleep(1.1)  # started_at has one-second resolution
    hold = threading.Event()
    doc = jobs.start("r", "b", lambda progress: hold.wait(5))
    # Before the fix this returned the previous run's finished document.
    assert doc["status"] == "running" and doc["started_at"] != old["started_at"]
    hold.set()
