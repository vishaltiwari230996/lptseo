"""SEO agent (a2) API — brands, insights, traffic-estimated to-dos, blog topics.

Mounted under ``/api/seo-geo``. Auth: any signed-in user reads and runs; only a
Creator edits the brand registry; the cron entry is gated by ``x-cron-key``
(matched against ``SEO_CRON_KEY``, endpoint is inert until that env var is set).
"""
from __future__ import annotations

import hmac
import html
import logging
import os
import secrets

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.security import get_current_user, require_creator
from app.services.run_tracking import CHANGE, CRON, JOB, Activity, ActivityTrail
from seo_geo_agent import advisor as seo_advisor
from seo_geo_agent import gsc_oauth as seo_oauth
from seo_geo_agent import site_brain as seo_site
from seo_geo_agent import audit as seo_audit
from seo_geo_agent import briefs as seo_briefs
from seo_geo_agent import competitors as seo_competitors
from seo_geo_agent import insights, keyword_pool as seo_kwpool, keywords as seo_keywords, sources
from seo_geo_agent import pages as seo_pages
from seo_geo_agent import priorities as seo_priorities
from seo_geo_agent import rank_gap as seo_rank_gap
from seo_geo_agent import rank_tracker as seo_rank
from seo_geo_agent import sitemap_health as seo_sitemap
from seo_geo_agent import cannibalization as seo_cannibal
from seo_geo_agent import deep_audit as seo_deep
from seo_geo_agent import jobs as seo_jobs
from seo_geo_agent import keyword_density as seo_density
from seo_geo_agent import landing_audit as seo_landing
from seo_geo_agent import page_speed as seo_speed
from seo_geo_agent import vitals as seo_vitals
from seo_geo_agent import state as seo_state
from seo_geo_agent.sources import CredentialMissing, ga_fetch_pages

router = APIRouter()
logger = logging.getLogger("agentos.seo_geo")

SEO_AGENT_ID = "a2"  # "SEO Analyst" slot in the frontend agent catalog
SEO_AGENT_NAME = "SEO Analyst"
TODO_STATUSES = {"todo", "assigned", "done"}


#: Every unit of SEO Analyst work lands here — see THE RULE in run_tracking.py.
trail = ActivityTrail(agent_id=SEO_AGENT_ID, agent_name=SEO_AGENT_NAME, category="seo")


def _for(act: Activity, brand: dict) -> None:
    """Stamp the brand this unit of work was about onto its trail row."""
    act.note(brand=brand.get("name"), brand_id=brand.get("id"))


class BrandIn(BaseModel):
    id: str = ""  # slug; derived from name when omitted
    name: str
    domain: str
    gsc_property: str = ""
    seeds: list[str] = []
    enabled: bool = True


class TodoStatusIn(BaseModel):
    status: str


class CompetitorsIn(BaseModel):
    domains: list[str]


class CustomQueryIn(BaseModel):
    query: str


class QueryIn(BaseModel):
    query: str


class KeywordIn(BaseModel):
    keyword: str


class PageIn(BaseModel):
    page: str


class DraftIn(BaseModel):
    text: str
    keyword: str


class AskIn(BaseModel):
    question: str


def _rows_28d(brand: dict) -> tuple[list, list[str]]:
    """Latest 28-day GSC rows, degrading to empty + a note when access is missing."""
    prop = brand.get("gsc_property") or f"sc-domain:{brand['domain']}"
    end = date.today()
    try:
        return sources.gsc_fetch(prop, end - timedelta(days=28), end), []
    except CredentialMissing as exc:
        return [], [f"Search Console: {exc}"]


def _brand_or_404(brand_id: str) -> dict:
    brand = next((b for b in insights.list_brands() if b["id"] == brand_id), None)
    if not brand:
        raise HTTPException(status_code=404, detail="Unknown brand")
    return brand


def _headline(run: dict | None, review: dict | None) -> str | None:
    """The one line a busy owner should read first on the brand card."""
    if run:
        todo = next((t for t in run.get("todos", []) if t.get("status") != "done"), None)
        if todo:
            gain = f" → est. +{todo['est_monthly_clicks']}/mo" if todo.get("est_monthly_clicks") else ""
            return f"Top action: {todo['action']}{gain}"
    if review and review.get("positioning"):
        return review["positioning"]
    return None


@router.get("/seo-geo/overview")
def overview(user=Depends(get_current_user)):
    cards = []
    for brand in insights.list_brands():
        run = insights.latest_run(brand["id"])
        review = seo_site.latest_review(brand["id"])
        cards.append({
            "brand": brand,
            "gsc_connected": bool(seo_oauth.connection(brand["id"])),
            "headline": _headline(run, review),
            "last_run": run and {
                "at": run["at"],
                "summary": run["summary"],
                "degraded": run["degraded"],
                "todo_count": len(run["todos"]),
                "topic_count": len(run["topics"]),
            },
        })
    return {
        "sources": {"gsc": sources.gsc_available(), "serp": sources.serper_available()},
        "brands": cards,
    }


@router.post("/seo-geo/brands")
def save_brand(payload: BrandIn, user=Depends(get_current_user),
               act: Activity = trail.records("brand_saved", "Saved a brand", unit=CHANGE)):
    # Shared with the GEO editor's self-serve create route: one answer to "what
    # is a valid brand id / domain", in the module that owns brand records. The
    # copy that used to live here also kept the path when a full URL was pasted
    # ("brand.com/pricing"), which broke ``sc-domain:`` and every alias derived
    # from the domain.
    try:
        slug = insights.slugify_brand_id(payload.id or payload.name)
        domain = insights.normalize_domain(payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    brand = {
        "id": slug,
        "name": payload.name.strip() or slug,
        "domain": domain,
        "gsc_property": payload.gsc_property.strip() or f"sc-domain:{domain}",
        "seeds": [s.strip() for s in payload.seeds if s.strip()][:10],
        "enabled": payload.enabled,
    }
    _for(act, brand)
    act.note(f"Brand saved — {brand['name']} ({domain})")
    return {"brands": insights.upsert_brand(brand)}


@router.delete("/seo-geo/brands/{brand_id}")
def remove_brand(brand_id: str, user=Depends(require_creator),
                 act: Activity = trail.records("brand_deleted", "Deleted a brand", unit=CHANGE)):
    _for(act, _brand_or_404(brand_id))
    act.note(f"Brand deleted — {brand_id}")
    return {"brands": insights.delete_brand(brand_id)}


@router.post("/seo-geo/run/{brand_id}")
def run_brand(brand_id: str, user=Depends(get_current_user),
              act: Activity = trail.records("run", "Full SEO refresh", unit=JOB)):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    run = insights.run_brand(brand, trigger=f"manual:{user['email']}")
    act.note(f"Full SEO refresh — {len(run['todos'])} to-dos, {len(run['topics'])} topics")
    return {"at": run["at"], "summary": run["summary"], "degraded": run["degraded"],
            "todo_count": len(run["todos"]), "topic_count": len(run["topics"])}


def _plan_of_action(brand_id: str, run: dict | None) -> list[dict]:
    """Top 3 next moves across every surface — the 'what do I do' strip."""
    plan: list[dict] = []
    todo = next((t for t in (run or {}).get("todos", []) if t.get("status") != "done"), None)
    if todo:
        plan.append({"source": "fix list", "action": todo["action"], "detail": todo["why"]})
    lab = seo_keywords.latest(brand_id) or {}
    cluster = next((c for c in lab.get("clusters", []) if c.get("tier") == "high"), None)
    if cluster:
        plan.append({"source": "keywords", "action": f"Go after “{cluster['name']}”",
                     "detail": cluster.get("recommendation", "")})
    report = seo_audit.latest_audit(brand_id) or {}
    issue = next((i for i in report.get("issues", []) if i["severity"] == "high"), None)
    if issue:
        plan.append({"source": "audit", "action": f"Fix: {issue['issue']}", "detail": issue["fix"]})
    else:
        failed = next((c for c in report.get("site_checks", []) if not c["ok"]), None)
        if failed:
            plan.append({"source": "audit", "action": f"Fix: {failed['name']}", "detail": failed["fix"]})
    return plan[:3]


@router.get("/seo-geo/brands/{brand_id}")
def brand_detail(brand_id: str, user=Depends(get_current_user)):
    brand = _brand_or_404(brand_id)
    conn = seo_oauth.connection(brand_id)
    run = insights.latest_run(brand_id)
    return {
        "brand": brand,
        "run": run,
        "gsc": {"connected": bool(conn), "property": (conn or {}).get("property")},
        "plan": _plan_of_action(brand_id, run),
        "site_review": seo_site.latest_review(brand_id),
    }


@router.post("/seo-geo/site-review/{brand_id}")
def run_site_review(brand_id: str, user=Depends(get_current_user),
                    act: Activity = trail.records("site_review", "Expert site review")):
    """Crawl the brand's site, build the corpus, and run the expert review."""
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    try:
        review = seo_site.analyze(brand)
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    act.note(f"Expert site review of {brand['domain']}")
    return review


@router.get("/seo-geo/site-review/{brand_id}")
def get_site_review(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"review": seo_site.latest_review(brand_id)}


# ------------------------- page intelligence -------------------------

@router.get("/seo-geo/pages/{brand_id}")
def get_pages(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"pages": seo_pages.latest(brand_id)}


@router.post("/seo-geo/pages/{brand_id}/refresh")
def refresh_pages(brand_id: str, user=Depends(get_current_user),
                  act: Activity = trail.records("pages_refresh", "Rebuilt page intelligence")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    corpus = seo_state.load(f"corpus-{brand_id}") or {}
    if not corpus.get("pages"):
        raise HTTPException(status_code=409, detail="Run the site analysis first")
    rows, notes = _rows_28d(brand)
    ga_pages = []
    prop = brand.get("ga4_property")
    if prop:
        try:
            end = date.today()
            ga_pages = ga_fetch_pages(prop, end - timedelta(days=28), end)
        except CredentialMissing as exc:
            notes.append(f"Google Analytics: {exc}")
    intel = seo_pages.build_page_intel(brand, corpus["pages"], ga_pages, rows, data_notes=notes)
    act.note(f"Rebuilt page intelligence for {brand['domain']}")
    return intel


@router.post("/seo-geo/todos/{brand_id}/{todo_id}")
def set_todo_status(brand_id: str, todo_id: str, payload: TodoStatusIn,
                    user=Depends(get_current_user),
                    act: Activity = trail.records("todo_status", "Moved a to-do", unit=CHANGE)):
    _for(act, _brand_or_404(brand_id))
    if payload.status not in TODO_STATUSES:
        raise HTTPException(status_code=422, detail=f"Status must be one of {sorted(TODO_STATUSES)}")
    insights.set_todo_status(brand_id, todo_id, payload.status)
    act.note(f"To-do {todo_id} → {payload.status}")
    return {"id": todo_id, "status": payload.status}


# ------------------------- keyword lab -------------------------

@router.post("/seo-geo/keywords/{brand_id}/run")
def run_keyword_lab(brand_id: str, user=Depends(get_current_user),
                    act: Activity = trail.records("keyword_lab", "Keyword lab run")):
    brand = seo_site.effective_seeds(_brand_or_404(brand_id))
    _for(act, brand)
    rows, notes = _rows_28d(brand)
    lab = seo_keywords.run_keyword_lab(
        brand, rows, trigger=f"manual:{user['email']}", extra_notes=notes
    )
    act.note(f"Keyword lab run — {len(lab.get('clusters', []))} clusters")
    return lab


@router.get("/seo-geo/keywords/{brand_id}")
def get_keywords(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"lab": seo_keywords.latest(brand_id)}


# ------------------------- competitors & SERP -------------------------

@router.get("/seo-geo/competitors/{brand_id}")
def get_competitors(brand_id: str, user=Depends(get_current_user)):
    brand = _brand_or_404(brand_id)
    ranks_doc = seo_state.load(f"ranks-{brand_id}") or {}
    sitemap_doc = seo_state.load(f"sitemaps-{brand_id}") or {}
    return {
        "tracked": brand.get("competitors", []),
        "suggested": ranks_doc.get("suggested_competitors", []),
        "shifts": seo_competitors.rank_shifts(brand_id),
        "feed": sitemap_doc.get("last_feed", {}),
        "custom_queries": seo_competitors.list_custom_queries(brand_id),
        "pool_size": len(seo_competitors.rank_tracking_pool(brand)),
        "pool_cap": seo_competitors.MAX_RANK_POOL,
    }


@router.put("/seo-geo/competitors/{brand_id}")
def set_competitors(brand_id: str, payload: CompetitorsIn, user=Depends(require_creator),
                    act: Activity = trail.records("competitors_saved",
                                                  "Edited the tracked competitors", unit=CHANGE)):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    brand["competitors"] = [d.strip().lower() for d in payload.domains if d.strip()][:8]
    insights.upsert_brand(brand)
    act.note(f"Tracked competitors set to {', '.join(brand['competitors']) or 'none'}")
    return {"tracked": brand["competitors"]}


@router.post("/seo-geo/competitors/{brand_id}/track")
def track_competitors(brand_id: str, user=Depends(get_current_user),
                      act: Activity = trail.records("competitor_track",
                                                    "Rank snapshot + competitor sitemap check")):
    """Take a rank snapshot + check competitor sitemaps for new content, now."""
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    degraded: list[str] = []
    try:
        seo_competitors.rank_snapshot(brand)
    except CredentialMissing as exc:
        degraded.append(str(exc))
    feed = {}
    try:
        feed = seo_competitors.sitemap_watch(brand)
    except CredentialMissing as exc:
        degraded.append(f"Sitemap watch: {exc}")
    return {"shifts": seo_competitors.rank_shifts(brand_id), "feed": feed, "degraded": degraded}


@router.post("/seo-geo/competitors/{brand_id}/custom-queries")
def add_custom_query(brand_id: str, payload: CustomQueryIn, user=Depends(require_creator),
                     act: Activity = trail.records("custom_query_added", "Added a custom rank-tracking query")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    try:
        queries = seo_competitors.add_custom_query(brand_id, payload.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    act.note(f"Added query “{payload.query.strip()}” ({len(queries)} custom queries total)")
    return {"custom_queries": queries, "pool_size": len(seo_competitors.rank_tracking_pool(brand))}


@router.delete("/seo-geo/competitors/{brand_id}/custom-queries")
def remove_custom_query(brand_id: str, payload: CustomQueryIn, user=Depends(require_creator),
                        act: Activity = trail.records("custom_query_removed", "Removed a custom rank-tracking query")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    queries = seo_competitors.remove_custom_query(brand_id, payload.query)
    act.note(f"Removed query “{payload.query.strip()}” ({len(queries)} custom queries remain)")
    return {"custom_queries": queries, "pool_size": len(seo_competitors.rank_tracking_pool(brand))}


@router.post("/seo-geo/serp/{brand_id}")
def serp_xray(brand_id: str, payload: QueryIn, user=Depends(get_current_user),
              act: Activity = trail.records("serp_xray", "SERP X-ray")):
    """Reverse-engineer the top of the SERP for any query, on demand."""
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    try:
        result = seo_competitors.serp_deep_dive(brand, payload.query.strip())
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    act.note(f"SERP X-ray: “{payload.query.strip()}”")
    return result


@router.get("/seo-geo/competitors/{brand_id}/profiles")
def get_competitor_profiles(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"profiles": seo_competitors.latest_profiles(brand_id)}


@router.post("/seo-geo/competitors/{brand_id}/profiles/refresh")
def refresh_competitor_profiles(brand_id: str, user=Depends(get_current_user),
                               act: Activity = trail.records("competitor_profiles",
                                                             "Rebuilt top-competitor profiles")):
    """Rebuild the top-5 competitor profiles: visibility, keywords won, content feed."""
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    try:
        profiles = seo_competitors.build_profiles(brand)
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return profiles


# ------------------------- briefs & decay plans -------------------------

@router.get("/seo-geo/briefs/{brand_id}")
def get_briefs(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"briefs": seo_briefs.list_briefs(brand_id)}


@router.post("/seo-geo/briefs/{brand_id}")
def build_brief(brand_id: str, payload: KeywordIn, user=Depends(get_current_user),
                act: Activity = trail.records("brief", "Built a content brief")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    rows, _ = _rows_28d(brand)
    try:
        brief = seo_briefs.build_brief(brand, payload.keyword.strip(), rows)
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    act.note(f"Content brief for “{payload.keyword.strip()}”")
    return brief


@router.post("/seo-geo/update-plan/{brand_id}")
def build_update_plan(brand_id: str, payload: PageIn, user=Depends(get_current_user),
                      act: Activity = trail.records("update_plan", "Built an update plan")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    rows, _ = _rows_28d(brand)
    try:
        plan = seo_briefs.update_plan(brand, payload.page.strip(), rows)
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    act.note(f"Update plan for {payload.page.strip()}")
    return plan


# ------------------------- audit & draft scoring -------------------------

@router.post("/seo-geo/audit/{brand_id}/run")
def run_audit(brand_id: str, user=Depends(get_current_user),
              act: Activity = trail.records("audit", "Technical site audit")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    try:
        report = seo_audit.site_audit(brand)
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    act.note(f"Technical site audit of {brand['domain']}")
    return report


@router.get("/seo-geo/audit/{brand_id}")
def get_audit(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"report": seo_audit.latest_audit(brand_id)}


@router.post("/seo-geo/draft-score/{brand_id}")
def draft_score(brand_id: str, payload: DraftIn, user=Depends(get_current_user),
                act: Activity = trail.records("draft_score", "Scored a draft")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    brief = next(
        (b for b in seo_briefs.list_briefs(brand_id)
         if b["keyword"].lower() == payload.keyword.strip().lower()),
        None,
    )
    score = seo_audit.score_draft(brand, payload.text, payload.keyword.strip(), brief)
    act.note(f"Scored a draft for “{payload.keyword.strip()}”")
    return score


# --------------------- Search Console connect (OAuth) ---------------------

def _oauth_redirect(request: Request) -> str:
    base = os.environ.get("SEO_OAUTH_REDIRECT_BASE", "") or str(request.base_url).rstrip("/")
    if "localhost" not in base and base.startswith("http://"):
        base = "https://" + base.removeprefix("http://")  # Cloud Run sits behind TLS proxy
    return f"{base}/api/seo-geo/oauth/callback"


def _close_page(title: str, body: str, status: int = 200, *, strong: str = "") -> HTMLResponse:
    """The OAuth landing page — the ONE place this backend serves HTML.

    Every caller value is attacker-reachable (``?error=`` comes straight off the
    query string), so ``title``, ``body`` and ``strong`` are all escaped: markup
    never comes from a value. ``body`` may carry one ``{strong}`` marker, which
    is where the escaped ``strong`` text is emphasised — the tag is wrapped
    around already-escaped text, so the value itself can never carry markup.

    The CSP grants a fresh per-response nonce to the two inline blocks this
    function authors and nothing else: injected script has no nonce, so
    ``default-src 'none'`` still applies to it. Never a constant nonce — a
    predictable one is the same as ``unsafe-inline``.
    """
    nonce = secrets.token_urlsafe(16)
    safe_body = html.escape(body)
    if strong:
        safe_body = safe_body.replace("{strong}", f"<b>{html.escape(strong)}</b>")
    return HTMLResponse(
        f"<html><head><style nonce=\"{nonce}\">"
        "body{font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center}"
        f"</style></head><body><h2>{html.escape(title)}</h2>"
        f"<p>{safe_body}</p><p>You can close this tab.</p>"
        f"<script nonce=\"{nonce}\">setTimeout(()=>window.close(),4000)</script>"
        "</body></html>",
        status_code=status,
        headers={
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/seo-geo/oauth/start/{brand_id}")
def oauth_start(brand_id: str, request: Request, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    try:
        return {"url": seo_oauth.auth_url(brand_id, _oauth_redirect(request))}
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/seo-geo/oauth/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Google redirects the customer's browser here — gated by the signed state."""
    if error:
        return _close_page("Not connected", f"Google returned: {error}", status=400)
    try:
        brand = _brand_or_404(seo_oauth.read_state(state))
        result = seo_oauth.complete(brand, code, _oauth_redirect(request))
        return _close_page(
            "Search Console connected ✓",
            f"{brand['name']} is now reading data from {{strong}}. "
            "Go back to the dashboard and hit Refresh data.",
            strong=result["property"],
        )
    except ValueError as exc:
        return _close_page("Not connected", str(exc), status=400)
    except CredentialMissing as exc:
        return _close_page("Not connected", str(exc), status=503)


@router.post("/seo-geo/oauth/disconnect/{brand_id}")
def oauth_disconnect(brand_id: str, user=Depends(require_creator),
                     act: Activity = trail.records("gsc_disconnect",
                                                   "Disconnected Search Console", unit=CHANGE)):
    _for(act, _brand_or_404(brand_id))
    seo_oauth.disconnect(brand_id)
    act.note(f"Search Console disconnected for {brand_id}")
    return {"connected": False}


@router.post("/seo-geo/ask/{brand_id}")
def ask_expert(brand_id: str, payload: AskIn, user=Depends(get_current_user),
               act: Activity = trail.records("ask", "Asked the SEO strategist")):
    """Grounded SEO-strategist chat over everything the agent knows about the brand."""
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Ask a question")
    try:
        answer = seo_advisor.ask(brand, question)
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    act.note(f"Asked: {question}")
    return answer


# --------------------------- technical health ---------------------------
#
# Three reads and three rebuilds, in the same shape as the rest of this router:
# GET returns whatever the last run persisted (instant, no network), POST
# rebuilds it. Splitting them matters here more than elsewhere — a sitemap
# audit spot-checks a dozen URLs and a CrUX report makes several API calls, so
# neither belongs on a panel that renders on every page load.


@router.get("/seo-geo/sitemap/{brand_id}")
def get_sitemap_health(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"sitemap": seo_sitemap.latest(brand_id)}


@router.post("/seo-geo/sitemap/{brand_id}/refresh")
def refresh_sitemap_health(brand_id: str, user=Depends(get_current_user),
                           act: Activity = trail.records("sitemap", "Started a deep audit", unit=JOB)):
    """Kept for the dashboard tile's button. The sitemap is now diagnosed by the
    deep audit - every sitemap, every URL - so this starts that job."""
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    return {"job": _start_deep(brand)}


@router.get("/seo-geo/vitals/{brand_id}")
def get_vitals(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"vitals": seo_vitals.latest(brand_id), "available": seo_vitals.available()}


@router.post("/seo-geo/vitals/{brand_id}/refresh")
def refresh_vitals(brand_id: str, user=Depends(get_current_user),
                   act: Activity = trail.records("vitals", "Pulled Core Web Vitals")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    # The pages worth asking CrUX about are the ones with traffic; CrUX only
    # holds a record for URLs busy enough to anonymise, so asking about quiet
    # pages just spends quota on 404s.
    intel = seo_pages.latest(brand_id) or {}
    top = [
        p["url"] for p in sorted(
            (intel.get("pages") or []),
            key=lambda r: -(r.get("clicks") or 0),
        )[:5] if p.get("url")
    ]
    try:
        doc = seo_vitals.build(brand, top)
    except CredentialMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    act.note(f"Pulled Core Web Vitals for {brand['domain']}")
    return {"vitals": doc}


@router.get("/seo-geo/keyword-pool/{brand_id}")
def get_keyword_pool(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"pool": seo_kwpool.latest(brand_id)}


@router.post("/seo-geo/keyword-pool/{brand_id}/refresh")
def refresh_keyword_pool(brand_id: str, user=Depends(get_current_user),
                         act: Activity = trail.records("keyword_pool", "Rebuilt the keyword pool")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    rows, notes = _rows_28d(brand)
    run = insights.latest_run(brand_id) or {}
    doc = seo_kwpool.build(brand, rows, topics=run.get("topics") or [], notes=notes)
    act.note(f"Pooled {doc['totals']['keywords']} keywords for {brand['domain']}")
    return {"pool": doc}


@router.get("/seo-geo/priorities/{brand_id}")
def get_priorities(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"priorities": seo_priorities.latest(brand_id)}


@router.post("/seo-geo/priorities/{brand_id}/refresh")
def refresh_priorities(brand_id: str, user=Depends(get_current_user),
                       act: Activity = trail.records("priorities", "Rebuilt the priority list")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    doc = seo_priorities.build(brand_id)
    act.note(f"Built {len(doc['items'])} priority items for {brand['domain']}")
    return {"priorities": doc}


# ------------------------------- deep audit -------------------------------
#
# The rigorous audit: every sitemap, every URL crawled once, then sitemap
# diagnosis, landing-page audit, cannibalization and blog keyword density from
# that one snapshot. A background job - a 1,000-page site takes minutes - so
# POST starts it and returns at once, and the panel polls GET for progress.
# Page speed is its own, much longer job.

def _start_deep(brand: dict) -> dict:
    if not seo_state.use_network():
        raise HTTPException(status_code=503, detail="offline mode - set SEO_ALLOW_NETWORK=1")
    return seo_jobs.start("deep", brand["id"],
                          lambda progress: seo_deep.run(brand, progress, rows_fn=_rows_28d))


@router.post("/seo-geo/deep-audit/{brand_id}/run")
def run_deep_audit(brand_id: str, user=Depends(get_current_user),
                   act: Activity = trail.records("deep_audit", "Started a deep audit", unit=JOB)):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    act.note(f"Deep audit of {brand['domain']}")
    return {"job": _start_deep(brand)}


@router.get("/seo-geo/deep-audit/{brand_id}")
def get_deep_audit(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {
        "summary": seo_deep.latest(brand_id),
        "job": seo_jobs.status("deep", brand_id),
        "speed_job": seo_jobs.status("speed", brand_id),
    }


@router.get("/seo-geo/deep-audit/{brand_id}/sitemap")
def get_deep_sitemap(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"sitemap": seo_sitemap.latest(brand_id), "urls": seo_sitemap.latest_urls(brand_id)}


def _page_row(p: dict) -> dict:
    """A landing page without its findings - the list view. Findings come from
    the per-page endpoint when a row is opened, so the list stays small."""
    return {k: v for k, v in p.items() if k != "findings"}


@router.get("/seo-geo/deep-audit/{brand_id}/landing")
def get_deep_landing(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"landing": seo_landing.latest(brand_id),
            "pages": [_page_row(p) for p in seo_landing.latest_pages(brand_id)]}


@router.get("/seo-geo/deep-audit/{brand_id}/landing/page")
def get_deep_landing_page(brand_id: str, url: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    page = next((p for p in seo_landing.latest_pages(brand_id) if p["url"] == url), None)
    if not page:
        raise HTTPException(status_code=404, detail="That page is not in the last audit")
    return {"page": page}


@router.get("/seo-geo/deep-audit/{brand_id}/cannibalization")
def get_deep_cannibalization(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"cannibalization": seo_cannibal.latest(brand_id)}


@router.get("/seo-geo/deep-audit/{brand_id}/density")
def get_deep_density(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {"density": seo_density.latest(brand_id), "posts": seo_density.latest_posts(brand_id)}


# ------------------------------- page speed -------------------------------

class SpeedRunIn(BaseModel):
    #: Measure only the first N pages in priority order (home, landing, blog).
    #: None = every page in the sitemap.
    limit: int | None = None
    #: Re-measure pages already measured today instead of resuming.
    fresh: bool = False


def _speed_row(r: dict) -> dict:
    return {k: v for k, v in r.items()
            if k not in ("heaviest", "bytes_by_type", "lcp_element_detail", "third_party_hosts",
                         "lcp_candidates", "shifts", "blocked_requests", "failed_requests")}


@router.post("/seo-geo/page-speed/{brand_id}/run")
def run_page_speed(brand_id: str, payload: SpeedRunIn | None = None, user=Depends(get_current_user),
                   act: Activity = trail.records("page_speed", "Started page-speed measurement", unit=JOB)):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    records = seo_deep.latest_crawl(brand_id)
    if not records:
        raise HTTPException(status_code=409,
                            detail="Run the deep audit first - page speed measures the pages it found")
    opts = payload or SpeedRunIn()

    def body(progress):
        try:
            seo_speed.run(brand, records, progress, limit=opts.limit, fresh=opts.fresh)
        finally:
            # Also after a run that ends as failed: the pages it did measure
            # are real measurements and belong in the landing audit.
            progress.phase("folding speed into the landing-page audit")
            seo_deep.refresh_landing(brand)

    act.note(f"Page speed for {brand['domain']}" + (f" (first {opts.limit})" if opts.limit else ""))
    return {"job": seo_jobs.start("speed", brand_id, body)}


@router.get("/seo-geo/page-speed/{brand_id}")
def get_page_speed(brand_id: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    return {
        "summary": seo_speed.latest(brand_id),
        "rows": [_speed_row(r) for r in seo_speed.latest_rows(brand_id)],
        "job": seo_jobs.status("speed", brand_id),
    }


@router.get("/seo-geo/page-speed/{brand_id}/page")
def get_page_speed_page(brand_id: str, url: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    row = next((r for r in seo_speed.latest_rows(brand_id) if r["url"] == url), None)
    if not row:
        raise HTTPException(status_code=404, detail="That page has not been measured")
    return {"page": row}


# --------------------------- rank tracker ---------------------------
# The scheduled scoreboard: a 200-query pool swept every two hours, with
# history, tracked-competitor positions, and a worklist. The sweep is a
# background job - 200 sequential SERP calls take minutes - so POST starts it
# and the panel polls GET for progress, exactly like the deep audit.

def _rank_payload(brand: dict) -> dict:
    brand_id = brand["id"]
    pool = seo_rank.latest_pool(brand_id) or {"queries": [], "notes": [], "sources_used": []}
    # Annotated once and handed to worklist() so a single GET loads history
    # once, not twice — worklist() would otherwise redo annotate_rows() (and
    # its one all_history()/latest_rows() read) on top of the line below.
    annotated = seo_rank.annotate_rows(brand)
    return {
        "rows": annotated,
        "meta": seo_rank.latest_meta(brand_id),
        "worklist": seo_rank.worklist(brand, limit=10, rows=annotated),
        "pool": {
            "size": len([q for q in pool["queries"] if q.get("active")]),
            "cap": seo_rank.MAX_POOL,
            "built_at": pool.get("built_at"),
            "sources_used": pool.get("sources_used", []),
            "notes": pool.get("notes", []),
        },
        "budget": seo_rank.budget_status(brand_id),
        "competitors": [d.lower() for d in (brand.get("competitors") or [])][:8],
        "enabled": seo_rank.enabled(brand),
        "job": seo_jobs.status(seo_rank.JOB_KIND, brand_id),
    }


@router.get("/seo-geo/rank-tracker/{brand_id}")
def get_rank_tracker(brand_id: str, user=Depends(get_current_user)):
    return _rank_payload(_brand_or_404(brand_id))


@router.get("/seo-geo/rank-tracker/{brand_id}/history")
def get_rank_history(brand_id: str, query: str, user=Depends(get_current_user)):
    _brand_or_404(brand_id)
    row = seo_rank.history_for(brand_id, query)
    if not row:
        raise HTTPException(status_code=404, detail="That query has no recorded history yet")
    return {"history": row}


@router.post("/seo-geo/rank-tracker/{brand_id}/sweep")
def run_rank_sweep(brand_id: str, user=Depends(require_creator),
                   act: Activity = trail.records("rank_sweep", "Started a rank sweep", unit=JOB)):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    if not seo_state.use_network():
        raise HTTPException(status_code=503, detail="offline mode - set SEO_ALLOW_NETWORK=1")
    act.note(f"Rank sweep for {brand['domain']}")
    job = seo_jobs.start(seo_rank.JOB_KIND, brand_id,
                         lambda progress: seo_rank.sweep(brand, progress))
    return {"job": job}


@router.post("/seo-geo/rank-tracker/{brand_id}/pool/rebuild")
def rebuild_rank_pool(brand_id: str, user=Depends(require_creator),
                      act: Activity = trail.records("rank_pool", "Rebuilt the rank-tracking pool")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    pool = seo_rank.build_pool(brand, rows_fn=_rows_28d)
    active = len([q for q in pool["queries"] if q.get("active")])
    act.note(f"Pool rebuilt: {active} active queries from {', '.join(pool['sources_used']) or 'no source'}")
    return _rank_payload(brand)


@router.post("/seo-geo/rank-tracker/{brand_id}/gap")
def rank_gap_card(brand_id: str, payload: QueryIn, user=Depends(get_current_user),
                  act: Activity = trail.records("rank_gap", "Explained a ranking gap")):
    brand = _brand_or_404(brand_id)
    _for(act, brand)
    doc = seo_rank_gap.explain(brand, payload.query.strip())
    if not doc:
        raise HTTPException(status_code=404,
                            detail="That query has not been swept, or nothing is ranking above us")
    act.note(f"Gap for “{payload.query.strip()}” vs {doc['their_domain']}")
    return {"gap": doc}


@router.post("/seo-geo/rank-tracker/cron")
def rank_cron(request: Request, response: Response,
              act: Activity = trail.records("rank_cron", "Scheduled rank sweep",
                                            unit=JOB, actor=CRON)):
    """Two-hourly rank sweep across every enabled brand.

    Separate from /seo-geo/cron/run on purpose: that one runs the full brand
    report, which has no business running twelve times a day. Status contract is
    the same, because Cloud Scheduler reads only the status code.
    """
    expected = os.environ.get("SEO_CRON_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="SEO_CRON_KEY not configured")
    if not hmac.compare_digest(request.headers.get("x-cron-key", ""), expected):
        raise HTTPException(status_code=403, detail="Bad cron key")

    results: dict[str, dict] = {}
    today = date.today().isoformat()
    for brand in insights.list_brands():
        if not brand.get("enabled", True):
            continue
        try:
            pool = seo_rank.latest_pool(brand["id"]) or {}
            # The pool is rebuilt once a day; sweeping twelve times against a
            # pool that changes every run would make every trend line a lie.
            if (pool.get("built_at") or "")[:10] != today:
                seo_rank.build_pool(brand, rows_fn=_rows_28d)
            results[brand["id"]] = {"ok": True, **seo_rank.sweep(brand)}
        except Exception as exc:  # noqa: BLE001 — one bad brand must not kill the sweep
            logger.exception("rank sweep failed for %s", brand["id"])
            results[brand["id"]] = {"ok": False, "error": str(exc)}

    ok = sum(1 for r in results.values() if r.get("ok"))
    failed = len(results) - ok
    out = {"brands": results, "ok": ok, "failed": failed, "status": "ok"}
    if results and ok == 0:
        out["status"] = "failed"
        response.status_code = 502
        logger.error("rank sweep FAILED: all %d brands errored", failed)
    elif failed:
        out["status"] = "partial"
        response.status_code = 207
        logger.warning("rank sweep degraded: %d/%d brands failed", failed, len(results))
    act.note(f"Rank sweep across {len(results)} brands — {ok} ok, {failed} failed",
             status=str(out["status"]))
    return out


# ------------------------------- cron -------------------------------

@router.post("/seo-geo/cron/run")
def cron_run(request: Request, response: Response,
             act: Activity = trail.records("cron", "Scheduled SEO sweep",
                                           unit=JOB, actor=CRON)):
    """Scheduled per-brand sweep.

    Status is honest: 200 every brand ran, 207 some brands failed, 502 EVERY
    brand failed. Cloud Scheduler only reads the status code, so the old
    unconditional 200 meant a sweep could be dead for weeks with the reason
    buried in a response body nobody reads. 207 stays 2xx on purpose — one
    permanently broken brand must not put the job into an endless retry loop —
    and the log line is what a human alerts on."""
    expected = os.environ.get("SEO_CRON_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="SEO_CRON_KEY not configured")
    if not hmac.compare_digest(request.headers.get("x-cron-key", ""), expected):
        raise HTTPException(status_code=403, detail="Bad cron key")
    results = {}
    for brand in insights.list_brands():
        if not brand.get("enabled", True):
            continue
        try:
            run = insights.run_brand(brand, trigger="cron")
            entry = {"ok": True, "todo_count": len(run["todos"])}
            # Tracking extras are best-effort: missing keys must not fail the
            # sweep, and they deliberately do NOT count toward the status below.
            # Rank tracking itself is now rank_tracker's own scheduled sweep —
            # running rank_snapshot's live sweep here too would double-bill Serper.
            try:
                seo_competitors.sitemap_watch(brand)
                entry["sitemaps"] = "updated"
            except Exception as exc:  # noqa: BLE001
                entry["sitemaps"] = f"skipped: {exc}"
            results[brand["id"]] = entry
        except Exception as exc:  # noqa: BLE001 — one bad brand must not kill the sweep
            logger.exception("seo cron failed for %s", brand["id"])
            results[brand["id"]] = {"ok": False, "error": str(exc)}
    ok = sum(1 for r in results.values() if r.get("ok"))
    failed = len(results) - ok
    out = {"brands": results, "ok": ok, "failed": failed}
    if results and ok == 0:
        out["status"] = "failed"
        response.status_code = 502
        logger.error("SEO cron sweep FAILED: all %d brands errored", failed)
    elif failed:
        out["status"] = "partial"
        response.status_code = 207
        logger.warning("SEO cron sweep degraded: %d/%d brands failed", failed, len(results))
    else:
        out["status"] = "ok"
    act.note(f"Scheduled sweep across {len(results)} brands — {ok} ok, {failed} failed",
             status=str(out["status"]))
    return out
