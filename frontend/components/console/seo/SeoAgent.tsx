"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  seoAnalyzeSite, seoBrandDetail, seoDeleteBrand, seoOauthDisconnect, seoOauthStart, seoOverview,
  seoKeywordPool, seoPages, seoPagesRefresh, seoRunBrand, seoSaveBrand, seoSetTodoStatus,
  seoSitemap, seoVitals,
  type SeoBrand, type SeoGa, type SeoGscStatus, type SeoKeywordPoolDoc, type SeoOverview,
  type SeoPagesDoc, type SeoPlanItem, type SeoRun, type SeoSiteReview, type SeoSitemapDoc,
  type SeoTodoStatus, type SeoTopic, type SeoVitalsDoc,
} from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { useAuth } from "@/lib/auth";
import { BrandMark, Icon } from "@/lib/kit-ui";
import { describeFailure, useLoadSession } from "@/lib/load";
import { AskView, AuditView, BriefsView, CompetitorsView, KeywordsView, UpdatePlanButton } from "./labs";
import { DashboardTiles, KeywordPoolView, VitalsView } from "./dashboard";
import { DeepAuditPanel } from "./deep";

type SeoTool = "ask" | "keywords" | "briefs" | "audit";

const TOOL_LABELS: Record<SeoTool, string> = {
  ask: "Ask the analyst",
  keywords: "Keyword lab",
  briefs: "Content briefs",
  audit: "Site audit",
};

/** SEO agent (a2) — per-brand insights, traffic-estimated to-dos, blog topic lab. */

const fmt = (n: number) => n.toLocaleString("en-US");

/** The path part of a page URL, for the meta line under a fix.
 *
 *  `new URL()` percent-encodes whatever it is handed, so a `page` value that
 *  was really a sentence came back as `/a%20(3991w),%20/b%20%E2%80%94%20three
 *  %20pages...` — unreadable, and with no spaces in it, unwrappable, which is
 *  what pushed the whole console into a horizontal scroll. The source of those
 *  values is fixed (`site_brain._evidence_page`), but this decodes and caps
 *  regardless: a display helper should not be the thing that breaks the page
 *  layout when it is handed something unexpected. */
function shortPage(url: string): string {
  const cap = (s: string) => (s.length > 80 ? `${s.slice(0, 79)}…` : s);
  try {
    const u = new URL(url);
    let path = u.pathname;
    try {
      path = decodeURIComponent(path);
    } catch {
      /* malformed escapes — keep the raw path */
    }
    return cap(path === "/" ? u.hostname : path);
  } catch {
    return cap(url);
  }
}

function Delta({ now, prev }: { now: number; prev: number }) {
  const diff = now - prev;
  if (!prev && !now) return null;
  const dir = diff >= 0 ? "up" : "down";
  return (
    <span className={`seo-delta seo-delta--${dir}`}>
      <Icon name={diff >= 0 ? "trending-up" : "trending-down"} size={13} />
      {fmt(Math.abs(diff))}
    </span>
  );
}

function TrendChip({ trend }: { trend: SeoTopic["trend"] }) {
  const label = { rising: "Rising", falling: "Cooling", flat: "Steady", new: "New" }[trend];
  return <span className={`seo-chip seo-chip--trend-${trend}`}>{label}</span>;
}

function DifficultyChip({ difficulty }: { difficulty: string | null }) {
  if (!difficulty) return <span className="seo-chip">—</span>;
  const cls = difficulty === "easy win" ? "easy" : difficulty === "medium" ? "medium" : "hard";
  return <span className={`seo-chip seo-chip--diff-${cls}`}>{difficulty}</span>;
}

function DegradedNotes({ notes, domain }: { notes: string[]; domain: string }) {
  if (!notes.length) return null;
  const friendly = notes.map((n) =>
    n.startsWith("Search Console")
      ? `Search Console access is missing for ${domain} — share the property with the backend service account, then refresh.`
      : n.startsWith("Serper")
        ? "Serper key is not set — blog topics are limited to our own search data (no live Google checks)."
        : n.startsWith("Google Analytics")
          ? `Google Analytics is not connected for ${domain} — give the backend service account Viewer access to the GA4 property, then refresh.`
          : n,
  );
  return (
    <div className="seo-degraded">
      <Icon name="alert-triangle" size={14} />
      <div>{friendly.map((n, i) => <div key={i}>{n}</div>)}</div>
    </div>
  );
}

function PagesView({ doc, busy, error, onRefresh }: {
  doc: SeoPagesDoc | null; busy: boolean; error: string | null; onRefresh: () => void;
}) {
  if (!doc) {
    return (
      <div className="mr-section">
        {error ? (
          // A failed load used to render as "run the site analysis first",
          // which reads as "there is nothing here" and sends the user into an
          // action that was never the problem.
          <div className="seo-degraded">
            <Icon name="alert-triangle" size={14} />
            <div>
              <div><strong>Couldn&apos;t load the pages for this brand.</strong></div>
              <div>{error}</div>
            </div>
          </div>
        ) : (
          <div className="seo-empty">Run the site analysis first — then refresh this tab.</div>
        )}
        <button className="seo-btn seo-btn--primary" disabled={busy} onClick={onRefresh}>
          <Icon name="refresh-cw" size={13} /> {error ? "Try again" : "Refresh"}
        </button>
      </div>
    );
  }
  return (
    <div className="mr-section">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">Pages — {fmt(doc.pages.length)} tracked · {doc.at}</h3>
        <button className="seo-btn seo-btn--primary" disabled={busy} onClick={onRefresh}>
          <Icon name="refresh-cw" size={13} /> Refresh
        </button>
      </div>
      {doc.notes.length > 0 && (
        <div className="seo-degraded">
          <Icon name="alert-triangle" size={14} />
          <div>{doc.notes.map((n, i) => <div key={i}>{n}</div>)}</div>
        </div>
      )}
      {doc.pages.length === 0 && (
        <div className="seo-empty">No pages found — run the site analysis, then refresh this tab.</div>
      )}
      {doc.pages.map((p) => (
        <div key={p.path} className="seo-pages__row">
          <div className="seo-pages__main">
            <div className="seo-pages__head">
              {p.url ? (
                <a className="seo-pages__path" href={p.url} target="_blank" rel="noreferrer">{p.path}</a>
              ) : (
                <span className="seo-pages__path">{p.path}</span>
              )}
              <span className="seo-pages__title">{p.title || "No title"}</span>
            </div>
            {p.flags.length > 0 && (
              <div className="seo-pages__flags">
                {p.flags.map((f) => <span key={f} className="seo-chip seo-chip--sev-medium">{f}</span>)}
              </div>
            )}
            <div className="seo-pages__rec">{p.ai ? "AI: " : "Rule: "}{p.recommendation}</div>
          </div>
          <div className="seo-pages__nums">
            <span>{fmt(p.views)} views</span>
            <span>{fmt(p.sessions)} sessions</span>
            <span>{Math.round(p.engagement_rate * 100)}% engaged</span>
            <span>{fmt(p.clicks)} clicks</span>
            <span>{fmt(p.impressions)} impressions</span>
            <span>{p.position != null ? `pos ${p.position}` : "not ranked"}</span>
            {p.best_query && <span className="seo-pages__query" title={p.best_query}>{p.best_query}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function pctPhrase(now: number, prev: number): string {
  if (!prev) return "";
  const d = Math.round(((now - prev) / prev) * 100);
  if (d === 0) return "steady vs the month before";
  return d > 0 ? `up ${d}% vs the month before` : `down ${Math.abs(d)}% vs the month before`;
}

/** The panel speaks first: what happened, in sentences a busy owner can read in 5 seconds. */
function Story({ run }: { run: SeoRun | null }) {
  if (!run) {
    return (
      <div className="seo-story">
        <p>No analysis yet. Hit <strong>Refresh data</strong> and this page will tell you exactly what to do next.</p>
      </div>
    );
  }
  const ga = run.ga;
  if (ga) {
    const lead = ga.key_events.find((e) => e.event === "generate_lead");
    const organic = ga.channels.find((c) => c.channel === "Organic Search");
    const organicRank = organic ? ga.channels.filter((c) => c.sessions > organic.sessions).length + 1 : 0;
    const trend = pctPhrase(ga.totals.sessions, ga.prev_totals.sessions);
    return (
      <div className="seo-story">
        <p>
          <strong>{fmt(ga.totals.sessions)} people</strong> visited your site in the last 28 days
          {trend ? ` — ${trend}` : ""}.
        </p>
        <p>
          {lead && <><strong>{fmt(lead.count)}</strong> became leads. </>}
          {organic && (
            <>Google search brought <strong>{fmt(organic.sessions)}</strong> of them
              {organicRank > 0 ? ` — your #${organicRank} traffic source` : ""}.</>
          )}
        </p>
      </div>
    );
  }
  if (run.summary.mode === "rank-tracking") {
    return (
      <div className="seo-story">
        <p>
          <strong>{run.summary.top10 ?? 0} of {run.summary.tracked ?? 0}</strong> keywords we track are on
          page 1 of Google. The plan below is the fastest way to add more.
        </p>
      </div>
    );
  }
  const trend = pctPhrase(run.summary.clicks_28d, run.summary.clicks_prev_28d);
  return (
    <div className="seo-story">
      <p>
        <strong>{fmt(run.summary.clicks_28d)} visits came from Google</strong> in the last 28 days
        {trend ? ` — ${trend}` : ""}.
      </p>
    </div>
  );
}

/** A quiet, collapsed section: the headline carries the takeaway, the body carries the detail. */
function Fold({ title, hint, count, defaultOpen = false, children }: {
  title: string; hint?: string; count?: number; defaultOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="seo-fold">
      <button className="seo-fold__head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={14} />
        <span className="seo-fold__title">{title}</span>
        {count != null && count > 0 && <span className="seo-fold__count">{fmt(count)}</span>}
        {hint && !open && <span className="seo-fold__hint">{hint}</span>}
      </button>
      {open && <div className="seo-fold__body">{children}</div>}
    </div>
  );
}

function GaSection({ ga }: { ga: SeoGa }) {
  const t = ga.totals;
  const p = ga.prev_totals;
  return (
    <div className="mr-section">
      <h3 className="mr-section__title">
        Website analytics · 28d
        <span className="seo-ga__src">{ga.property_name || "Google Analytics"}</span>
      </h3>
      <div className="seo-summary">
        <div className="seo-stat">
          <span className="seo-stat__label">Sessions</span>
          <span className="seo-stat__num">{fmt(t.sessions)}<Delta now={t.sessions} prev={p.sessions} /></span>
        </div>
        <div className="seo-stat">
          <span className="seo-stat__label">Visitors</span>
          <span className="seo-stat__num">{fmt(t.users)}<Delta now={t.users} prev={p.users} /></span>
        </div>
        <div className="seo-stat">
          <span className="seo-stat__label">New visitors</span>
          <span className="seo-stat__num">{fmt(t.new_users)}</span>
        </div>
        <div className="seo-stat">
          <span className="seo-stat__label">Engagement</span>
          <span className="seo-stat__num">{Math.round(t.engagement_rate * 100)}%</span>
        </div>
        <div className="seo-stat">
          <span className="seo-stat__label">Pageviews</span>
          <span className="seo-stat__num">{fmt(t.pageviews)}<Delta now={t.pageviews} prev={p.pageviews} /></span>
        </div>
      </div>
      {(ga.top_pages.length > 0 || ga.channels.length > 0) && (
        <div className="seo-ga__cols">
          {ga.top_pages.length > 0 && (
            <div>
              <div className="seo-lab__meta">Most visited pages</div>
              <ul className="seo-ga__list">
                {ga.top_pages.map((pg) => (
                  <li key={pg.path}>
                    <span className="seo-ga__name" title={pg.path}>{pg.path}</span>
                    <span className="seo-ga__num">{fmt(pg.views)} views</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ga.channels.length > 0 && (
            <div>
              <div className="seo-lab__meta">Where traffic comes from</div>
              <ul className="seo-ga__list">
                {ga.channels.map((c) => (
                  <li key={c.channel}>
                    <span className="seo-ga__name">{c.channel}</span>
                    <span className="seo-ga__num">
                      {fmt(c.sessions)}
                      <Delta now={c.sessions} prev={c.prev_sessions} />
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {ga.key_events.length > 0 && (
        <>
          <div className="seo-lab__meta">Key events (conversions)</div>
          <div className="seo-cluster__kws">
            {ga.key_events.map((e) => (
              <span key={e.event} className="seo-chip seo-chip--on">{e.event} · {fmt(e.count)}</span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function AddBrandForm({ onSaved, onToast }: { onSaved: () => void; onToast: ToastFn }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [seeds, setSeeds] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await seoSaveBrand({
        name,
        domain,
        seeds: seeds.split(",").map((s) => s.trim()).filter(Boolean),
      });
      onToast(`${name} added — run its first analysis`);
      setOpen(false);
      setName(""); setDomain(""); setSeeds("");
      onSaved();
    } catch (e) {
      onToast(describeFailure(e, "Could not save brand"), "error");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="seo-card seo-card--add" onClick={() => setOpen(true)}>
        <Icon name="plus" size={18} />
        <span>Add a brand site</span>
      </button>
    );
  }
  return (
    <div className="seo-card seo-card--form">
      <input className="seo-input" placeholder="Brand name" value={name} onChange={(e) => setName(e.target.value)} />
      <input className="seo-input" placeholder="Site domain, e.g. brand.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
      <input className="seo-input" placeholder="Seed keywords, comma separated" value={seeds} onChange={(e) => setSeeds(e.target.value)} />
      <div className="seo-form__row">
        <button className="seo-btn seo-btn--primary" disabled={busy || !name || !domain} onClick={() => void save()}>
          Save brand
        </button>
        <button className="seo-btn" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

export function SeoAgent({ onToast, onBack }: { onToast: ToastFn; onBack: () => void }) {
  const { user } = useAuth();
  const [overview, setOverview] = useState<SeoOverview | null>(null);
  const [brand, setBrand] = useState<SeoBrand | null>(null);
  const [run, setRun] = useState<SeoRun | null>(null);
  const [gsc, setGsc] = useState<SeoGscStatus | null>(null);
  const [plan, setPlan] = useState<SeoPlanItem[]>([]);
  const [siteReview, setSiteReview] = useState<SeoSiteReview | null>(null);
  const [siteBusy, setSiteBusy] = useState(false);
  const [pagesDoc, setPagesDoc] = useState<SeoPagesDoc | null>(null);
  const [pagesBusy, setPagesBusy] = useState(false);
  const [pagesError, setPagesError] = useState<string | null>(null);
  /* Technical health. Each is a read of what a previous run persisted — the
     rebuilds are behind their own buttons because they cost live requests. */
  const [sitemapDoc, setSitemapDoc] = useState<SeoSitemapDoc | null>(null);
  const [vitalsDoc, setVitalsDoc] = useState<SeoVitalsDoc | null>(null);
  const [vitalsAvailable, setVitalsAvailable] = useState(false);
  const [poolDoc, setPoolDoc] = useState<SeoKeywordPoolDoc | null>(null);
  /** The brand whose detail is being fetched right now, for the card's cue. */
  const [opening, setOpening] = useState<string | null>(null);
  const [tool, setTool] = useState<SeoTool | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAllTodos, setShowAllTodos] = useState(false);
  const [showAvoided, setShowAvoided] = useState(false);

  const session = useLoadSession();

  const refreshOverview = useCallback(async () => {
    const attempt = session.begin("overview");
    try {
      const next = await seoOverview();
      if (attempt.current()) setOverview(next);
    } catch (e) {
      const message = attempt.failure(e, "Couldn't load the SEO overview");
      if (message) onToast(message, "error");
    }
  }, [onToast, session]);

  useEffect(() => { void refreshOverview(); }, [refreshOverview]);

  /** The brand actually on screen. A ref, not the `brand` state: these handlers
   *  resume after an await and their captured `brand` is whatever it was when
   *  the click happened, which is exactly the value that must not be trusted. */
  const shownBrandId = useRef<string | null>(null);
  useEffect(() => { shownBrandId.current = brand?.id ?? null; }, [brand]);

  /** Two chained requests write into seven pieces of shared state, so without a
   *  ticket a slower brand A lands after the user has opened brand B and every
   *  panel below B's name is A's data. This was one of only three modules that
   *  got that right by hand; the slot is the same discipline, minus the
   *  `RequestSequence` ref and the unmount cleanup it needed. */
  async function openBrand(id: string) {
    const attempt = session.begin("brand");
    setOpening(id);
    try {
      const detail = await seoBrandDetail(id, { signal: attempt.signal });
      if (!attempt.current()) return; // a newer brand owns the screen
      setBrand(detail.brand);
      setRun(detail.run);
      setGsc(detail.gsc ?? null);
      setPlan(detail.plan ?? []);
      setSiteReview(detail.site_review ?? null);
      setTool(null);
      setShowAllTodos(false);
      setPagesDoc(null);
      setPagesError(null);
      setSitemapDoc(null);
      setVitalsDoc(null);
      setPoolDoc(null);
      // Cheap stored reads, fired together — none of them touches the network
      // beyond this API, and a failure in one must not blank the others.
      void Promise.allSettled([
        seoSitemap(id, { signal: attempt.signal }),
        seoVitals(id, { signal: attempt.signal }),
        seoKeywordPool(id, { signal: attempt.signal }),
      ]).then(([sm, vt, kp]) => {
        if (!attempt.current()) return;
        if (sm.status === "fulfilled") setSitemapDoc(sm.value.sitemap);
        if (vt.status === "fulfilled") {
          setVitalsDoc(vt.value.vitals);
          setVitalsAvailable(vt.value.available);
        }
        if (kp.status === "fulfilled") setPoolDoc(kp.value.pool);
      });
      try {
        const pagesRes = await seoPages(id, { signal: attempt.signal });
        if (!attempt.current()) return;
        setPagesDoc(pagesRes.pages);
      } catch (e) {
        const msg = attempt.failure(e, "Could not load pages");
        if (!msg) return;
        setPagesDoc(null);
        setPagesError(msg);
        onToast(msg, "error");
      }
    } catch (e) {
      const msg = attempt.failure(e, "Failed to load brand");
      if (msg) onToast(msg, "error");
    } finally {
      if (attempt.current()) setOpening(null);
    }
  }

  async function refreshPages(id: string) {
    setPagesBusy(true);
    try {
      const doc = await seoPagesRefresh(id);
      if (shownBrandId.current !== id) return; // user moved on mid-refresh
      setPagesDoc(doc);
      setPagesError(null);
      onToast(`Pages refreshed — ${fmt(doc.pages.length)} page(s)`);
    } catch (e) {
      if (shownBrandId.current !== id) return;
      const msg = describeFailure(e, "Could not refresh pages");
      setPagesError(msg);
      onToast(msg, "error");
    } finally {
      setPagesBusy(false);
    }
  }

  async function runBrand(id: string) {
    setBusy(true);
    onToast("Analyzing — pulling Search Console and topic data…");
    try {
      await seoRunBrand(id);
      const detail = await seoBrandDetail(id);
      // `brand` captured in this closure is the brand at click time; the ref is
      // the one on screen now.
      if (shownBrandId.current === id) {
        setBrand(detail.brand);
        setRun(detail.run);
        setGsc(detail.gsc ?? null);
        setPlan(detail.plan ?? []);
        setSiteReview(detail.site_review ?? null);
      }
      await refreshOverview();
      const summary = detail.run?.summary;
      if (summary?.mode === "rank-tracking") {
        onToast(`Done — ${summary.top10 ?? 0}/${summary.tracked ?? 0} tracked keywords on page 1 (live rankings)`);
      } else if (summary?.est_potential_clicks) {
        onToast(`Done — est. +${fmt(summary.est_potential_clicks)} clicks/month on the table`);
      } else {
        onToast("Done — see the brand card for data status");
      }
    } catch (e) {
      onToast(describeFailure(e, "Analysis failed"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function analyzeSite(id: string) {
    setSiteBusy(true);
    onToast("Reading the website — crawling pages and running the expert review (takes a minute or two)…");
    try {
      const review = await seoAnalyzeSite(id);
      const detail = await seoBrandDetail(id);
      // A crawl takes minutes — long enough for the user to open another brand,
      // whose fix list must not be replaced by this one's.
      if (shownBrandId.current !== id) return;
      setSiteReview(review);
      setRun(detail.run);
      setPlan(detail.plan ?? []);
      onToast(`Site review done — ${review.issues.length} finding(s) added to the fix list`);
    } catch (e) {
      onToast(describeFailure(e, "Site analysis failed"), "error");
    } finally {
      setSiteBusy(false);
    }
  }

  async function connectGsc(id: string) {
    try {
      const { url } = await seoOauthStart(id);
      window.open(url, "_blank", "width=540,height=680");
      onToast("Choose the Google account that owns the site's Search Console, press Allow, then hit Refresh data here.");
    } catch (e) {
      onToast(describeFailure(e, "Could not start the Google connect"), "error");
    }
  }

  async function disconnectGsc(id: string) {
    if (!window.confirm("Disconnect Search Console? The brand falls back to live rank tracking.")) return;
    try {
      await seoOauthDisconnect(id);
      setGsc({ connected: false, property: null });
      onToast("Search Console disconnected");
    } catch (e) {
      onToast(describeFailure(e, "Disconnect failed"), "error");
    }
  }

  async function removeBrand(b: SeoBrand) {
    if (!window.confirm(`Remove ${b.name}? Its insights and to-dos disappear from the dashboard.`)) return;
    try {
      await seoDeleteBrand(b.id);
      onToast(`${b.name} removed`);
      setBrand(null); setRun(null);
      await refreshOverview();
    } catch (e) {
      onToast(describeFailure(e, "Remove failed"), "error");
    }
  }

  async function setStatus(todoId: string, status: SeoTodoStatus) {
    if (!brand || !run) return;
    setRun({ ...run, todos: run.todos.map((t) => (t.id === todoId ? { ...t, status } : t)) });
    try {
      await seoSetTodoStatus(brand.id, todoId, status);
    } catch (e) {
      onToast(describeFailure(e, "Could not save status"), "error");
    }
  }

  const sources = overview?.sources;

  return (
    <div className="mr-app seo-app">
      <header className="mr-top">
        {/* At the root there is nowhere to go back TO — `onBack` is a no-op in
            this standalone extract — so the slot carries the mark instead of a
            control that does nothing. Inside a brand it is a real Back. */}
        {brand ? (
          <button
            className="mr-top__back"
            onClick={() => { setBrand(null); setRun(null); }}
            aria-label="Back to all brands"
          >
            <Icon name="arrow-left" size={18} />
          </button>
        ) : (
          <span className="mr-top__mark"><BrandMark size={30} /></span>
        )}
        <div className="mr-top__id">
          <span className="mr-top__name">{brand ? brand.name : "Law Prep Tutorial"}</span>
          <span className="mr-top__sub">
            {brand ? brand.domain : "SEO console — search insights, prioritised fixes and blog topics"}
          </span>
        </div>
        {sources && (
          <div className="seo-sources">
            <span className={`seo-chip ${sources.gsc ? "seo-chip--on" : "seo-chip--off"}`}>Search Console</span>
            <span className={`seo-chip ${sources.serp ? "seo-chip--on" : "seo-chip--off"}`}>Google SERP</span>
          </div>
        )}
      </header>

      <div className="mr-body">
        {!brand && (
          <div className="mr-panel">
            <div className="mr-panel__head">
              <h2 className="mr-panel__title">Brands</h2>
              <span className="mr-panel__sub">Every site we watch — open one for its fix list and blog topics.</span>
            </div>
            <div className="seo-grid">
              {(overview?.brands ?? []).map(({ brand: b, last_run, gsc_connected, headline }) => (
                <div key={b.id} className="seo-card" role="button" tabIndex={0}
                     aria-busy={opening === b.id}
                     onClick={() => void openBrand(b.id)}
                     onKeyDown={(e) => e.key === "Enter" && void openBrand(b.id)}>
                  <div className="seo-card__head">
                    <span className="seo-card__name">
                      {b.name}
                      {gsc_connected && <span className="seo-chip seo-chip--on seo-card__gsc">GSC ✓</span>}
                      {opening === b.id && <span className="seo-chip">Opening…</span>}
                    </span>
                    <span className="seo-card__domain">{b.domain}</span>
                  </div>
                  {headline && <div className="seo-card__headline">{headline}</div>}
                  {last_run ? (
                    <>
                      <div className="seo-card__stats">
                        {last_run.summary.mode === "rank-tracking" ? (
                          <>
                            <div className="seo-stat">
                              <span className="seo-stat__label">Page 1</span>
                              <span className="seo-stat__num">
                                {last_run.summary.top10 ?? 0}/{last_run.summary.tracked ?? 0}
                              </span>
                            </div>
                            <div className="seo-stat">
                              <span className="seo-stat__label">Not ranking</span>
                              <span className="seo-stat__num">{last_run.summary.unranked ?? 0}</span>
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="seo-stat">
                              <span className="seo-stat__label">Clicks · 28d</span>
                              <span className="seo-stat__num">
                                {fmt(last_run.summary.clicks_28d)}
                                <Delta now={last_run.summary.clicks_28d} prev={last_run.summary.clicks_prev_28d} />
                              </span>
                            </div>
                            <div className="seo-stat">
                              <span className="seo-stat__label">Est. upside</span>
                              <span className="seo-stat__num seo-stat__num--good">
                                +{fmt(last_run.summary.est_potential_clicks)}/mo
                              </span>
                            </div>
                          </>
                        )}
                      </div>
                      <div className="seo-card__meta">
                        <span>{last_run.todo_count} fixes</span>
                        <span>{last_run.topic_count} topics</span>
                        <span>run {last_run.at}</span>
                        {last_run.degraded.length > 0 && <Icon name="alert-triangle" size={13} />}
                      </div>
                    </>
                  ) : (
                    <div className="seo-card__empty">No analysis yet</div>
                  )}
                  <button className="seo-btn seo-btn--primary" disabled={busy}
                          onClick={(e) => { e.stopPropagation(); void runBrand(b.id); }}>
                    <Icon name="refresh-cw" size={13} /> {last_run ? "Refresh" : "Run first analysis"}
                  </button>
                </div>
              ))}
              {user?.is_creator && <AddBrandForm onSaved={() => void refreshOverview()} onToast={onToast} />}
            </div>
          </div>
        )}

        {/* Before the first run this console has nothing to show, and a page
            that is 60% empty is not a neutral state — it reads as broken. So
            the emptiness is spent saying what happens next. It goes away for
            good the moment any brand has been analysed once.

            Numbered, unusually: these three genuinely are a sequence — you
            cannot review a fix list before the analysis that produces it — and
            the order is the useful part. */}
        {!brand && overview && overview.brands.length > 0 &&
         overview.brands.every((b) => !b.last_run) && (
          <ol className="seo-start">
            <li className="seo-start__step">
              <span className="seo-start__n">1</span>
              <div>
                <h3>Run the first analysis</h3>
                <p>
                  We read the site, pull whatever Search Console will give us and
                  score every page. It takes a couple of minutes.
                </p>
              </div>
            </li>
            <li className="seo-start__step">
              <span className="seo-start__n">2</span>
              <div>
                <h3>Work the fix list</h3>
                <p>
                  Findings come back ordered by the traffic each one stands to
                  win, so the top of the list is where to start.
                </p>
              </div>
            </li>
            <li className="seo-start__step">
              <span className="seo-start__n">3</span>
              <div>
                <h3>Brief the next article</h3>
                <p>
                  Pick a keyword and the agent drafts the angle and outline from
                  what already ranks for it.
                </p>
              </div>
            </li>
          </ol>
        )}

        {brand && (
          <div className="mr-panel">
            <div className="seo-detail__bar">
              <div className="mr-panel__head">
                <h2 className="mr-panel__title">{brand.name}</h2>
                <span className="mr-panel__sub">{brand.domain}{run ? ` · analyzed ${run.at}` : ""}</span>
              </div>
              <div className="seo-detail__actions">
                {gsc?.connected ? (
                  <span className="seo-chip seo-chip--on" title={gsc.property ?? undefined}>
                    Search Console ✓
                    {user?.is_creator && (
                      <button className="seo-chip__x" aria-label="Disconnect Search Console"
                              onClick={() => void disconnectGsc(brand.id)}>×</button>
                    )}
                  </span>
                ) : (
                  <button className="seo-btn" onClick={() => void connectGsc(brand.id)}>
                    <Icon name="link" size={13} /> Connect Search Console
                  </button>
                )}
                <button className="seo-btn" disabled={siteBusy} onClick={() => void analyzeSite(brand.id)}>
                  <Icon name="search" size={13} /> {siteReview ? "Re-analyze website" : "Analyze website"}
                </button>
                <button className="seo-btn seo-btn--primary" disabled={busy} onClick={() => void runBrand(brand.id)}>
                  <Icon name="refresh-cw" size={13} /> Refresh data
                </button>
                {user?.is_creator && (
                  <button className="seo-btn" onClick={() => void removeBrand(brand)}>Remove</button>
                )}
              </div>
            </div>

            <Story run={run} />

            {/* The summary row. It renders whether or not anything has been
                built yet — an un-audited tile reading "—" is the honest state,
                and it is also the prompt to go and build it. */}
            <DashboardTiles
              sitemap={sitemapDoc}
              vitals={vitalsDoc}
              pool={poolDoc}
              healthFindings={siteReview ? siteReview.issues.length : null}
            />

            {plan.length > 0 && (
              <div className="seo-hero">
                <div className="seo-hero__title">This week — do these {Math.min(plan.length, 3)}</div>
                {plan.slice(0, 3).map((p, i) => (
                  <div key={i} className="seo-hero__item">
                    <span className="seo-hero__num">{i + 1}</span>
                    <div className="seo-hero__body">
                      <span className="seo-hero__action">{p.action}</span>
                      <span className="seo-hero__detail">{p.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {run && <DegradedNotes notes={run.degraded} domain={brand.domain} />}

            {/* The rigorous audit sits first and open: it is the one section that
                answers "what is wrong with this site, page by page", and the
                folds below it are the older, narrower views. */}
            <Fold title="Deep audit" defaultOpen
                  hint="landing pages · sitemap · cannibalization · keyword density · page speed">
              <DeepAuditPanel brandId={brand.id} onToast={onToast} />
            </Fold>

            {siteReview && (
              <Fold title="Website health"
                    hint={`${siteReview.issues.length} finding(s) · ${siteReview.page_count} pages read`}>
              <div className="mr-section">
                <h3 className="mr-section__title">What the expert review found · {siteReview.at}</h3>
                {siteReview.positioning && <div className="seo-poa__action">{siteReview.positioning}</div>}
                {siteReview.scorecard && Object.keys(siteReview.scorecard).length > 0 && (
                  <div className="seo-cluster__kws">
                    {Object.entries(siteReview.scorecard).map(([key, cell]) => {
                      const label = {
                        intent: "Intent", content_depth: "Content depth", architecture: "Architecture",
                        trust: "Trust", conversion: "Conversion", ai_search: "AI search",
                      }[key] ?? key;
                      const cls = cell.grade >= 4 ? "seo-chip--on" : "";
                      return (
                        <span key={key} className={`seo-chip ${cls}`} title={cell.note}>
                          {label} {cell.grade}/5
                        </span>
                      );
                    })}
                  </div>
                )}
                {siteReview.strengths.length > 0 && (
                  <div className="seo-cluster__kws">
                    {siteReview.strengths.map((s) => (
                      <span key={s} className="seo-chip seo-chip--on">{s}</span>
                    ))}
                  </div>
                )}
                {siteReview.missing_topics.length > 0 && (
                  <>
                    <div className="seo-lab__meta">Topics the site doesn't cover yet — blog fuel:</div>
                    <div className="seo-cluster__kws">
                      {siteReview.missing_topics.map((t) => (
                        <span key={t} className="seo-chip seo-chip--cov-gap">{t}</span>
                      ))}
                    </div>
                  </>
                )}
                <div className="seo-lab__meta">
                  Findings are in the Fix list with everything else — nothing to read twice.
                </div>
              </div>
              </Fold>
            )}

            {run && (
              <Fold title="Traffic & rankings"
                    hint={run.ga
                      ? `${fmt(run.ga.totals.sessions)} visits · ${fmt(run.ga.key_events.find((e) => e.event === "generate_lead")?.count ?? 0)} leads`
                      : run.summary.mode === "rank-tracking"
                        ? `${run.summary.top10 ?? 0}/${run.summary.tracked ?? 0} keywords on page 1`
                        : `${fmt(run.summary.clicks_28d)} clicks from Google`}>
                {run.summary.mode === "rank-tracking" ? (
                  <div className="seo-summary">
                    <div className="seo-stat">
                      <span className="seo-stat__label">Tracked keywords</span>
                      <span className="seo-stat__num">{run.summary.tracked ?? 0}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Top 3</span>
                      <span className="seo-stat__num seo-stat__num--good">{run.summary.top3 ?? 0}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Page 1</span>
                      <span className="seo-stat__num">{run.summary.top10 ?? 0}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Not ranking</span>
                      <span className="seo-stat__num">{run.summary.unranked ?? 0}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Since last check</span>
                      <span className="seo-stat__num">
                        {(run.summary.moved_up ?? 0) > 0 && (
                          <span className="seo-delta seo-delta--up">
                            <Icon name="trending-up" size={13} />{run.summary.moved_up}
                          </span>
                        )}
                        {(run.summary.moved_down ?? 0) > 0 && (
                          <span className="seo-delta seo-delta--down">
                            <Icon name="trending-down" size={13} />{run.summary.moved_down}
                          </span>
                        )}
                        {!(run.summary.moved_up || run.summary.moved_down) && "steady"}
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="seo-summary">
                    <div className="seo-stat">
                      <span className="seo-stat__label">Clicks · 28d</span>
                      <span className="seo-stat__num">
                        {fmt(run.summary.clicks_28d)}
                        <Delta now={run.summary.clicks_28d} prev={run.summary.clicks_prev_28d} />
                      </span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Impressions · 28d</span>
                      <span className="seo-stat__num">{fmt(run.summary.impressions_28d)}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Avg. position</span>
                      <span className="seo-stat__num">{run.summary.avg_position || "—"}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Est. upside if fixed</span>
                      <span className="seo-stat__num seo-stat__num--good">+{fmt(run.summary.est_potential_clicks)}/mo</span>
                    </div>
                  </div>
                )}

                {run.ga && <GaSection ga={run.ga} />}

                {run.insights.length > 0 && (
                  <div className="mr-section">
                    <h3 className="mr-section__title">What changed</h3>
                    <ul className="seo-insights">
                      {run.insights.map((line, i) => <li key={i}>{line}</li>)}
                    </ul>
                  </div>
                )}
              </Fold>
            )}

            <Fold title="Pages" count={pagesDoc?.pages.length} hint="traffic + health, page by page">
              <PagesView doc={pagesDoc} busy={pagesBusy} error={pagesError}
                onRefresh={() => void refreshPages(brand.id)} />
            </Fold>

            <Fold title="Competitors" hint="top 5 — and what they publish">
              <CompetitorsView brandId={brand.id} isCreator={!!user?.is_creator} onToast={onToast} />
            </Fold>

            <Fold title="Core Web Vitals"
                  hint={
                    vitalsDoc?.origin_vitals?.mobile
                      ? `mobile · ${vitalsDoc.origin_vitals.mobile.assessment}`
                      : "real Chrome field data"
                  }>
              <VitalsView brandId={brand.id} doc={vitalsDoc} available={vitalsAvailable}
                          onLoaded={setVitalsDoc} onToast={onToast} />
            </Fold>

            <Fold title="Keyword pool"
                  count={poolDoc?.totals.keywords}
                  hint={poolDoc ? `+${poolDoc.totals.opportunity} est. clicks within reach` : "every keyword in one table"}>
              <KeywordPoolView brandId={brand.id} doc={poolDoc} onLoaded={setPoolDoc} onToast={onToast} />
            </Fold>

            <Fold title="More tools" hint="ask · keyword lab · briefs · audit">
              <div className="seo-cluster__kws">
                {(Object.keys(TOOL_LABELS) as SeoTool[]).map((t) => (
                  <button key={t} className={`seo-chip${tool === t ? " seo-chip--on" : ""}`}
                          onClick={() => setTool(tool === t ? null : t)}>
                    {TOOL_LABELS[t]}
                  </button>
                ))}
              </div>
              {tool === "ask" && <AskView brandId={brand.id} brandName={brand.name} />}
              {tool === "keywords" && <KeywordsView brandId={brand.id} onToast={onToast} />}
              {tool === "briefs" && <BriefsView brandId={brand.id} onToast={onToast} />}
              {tool === "audit" && <AuditView brandId={brand.id} brandName={brand.name} onToast={onToast} />}
            </Fold>

            {run && (
              <>
                <Fold title="Fix list" count={run.todos.length} hint="every fix, biggest win first">
                {(
                  <div className="mr-section">
                    <h3 className="mr-section__title">
                      {run.summary.mode === "rank-tracking"
                        ? "Top 10 actions — from live rankings (drops first)"
                        : "Top 10 actions — biggest estimated gain first"}
                    </h3>
                    {run.todos.length === 0 && <div className="seo-empty">Nothing above the impact threshold — refresh after the next content push.</div>}
                    {(showAllTodos ? run.todos : run.todos.slice(0, 10)).map((t, i) => (
                      <div key={t.id} className={`seo-todo${t.status === "done" ? " seo-todo--done" : ""}`}>
                        <span className="seo-todo__rank">{i + 1}</span>
                        {t.est_monthly_clicks != null ? (
                          <div className="seo-todo__gain">+{fmt(t.est_monthly_clicks)}<small>est. clicks/mo</small></div>
                        ) : (
                          <div className="seo-todo__gain seo-todo__gain--pos">
                            {t.position > 0 ? `#${t.position}` : "—"}
                            <small>{t.position > 0 ? "current pos" : t.kind === "site" ? "site fix" : "not ranked"}</small>
                          </div>
                        )}
                        <div className="seo-todo__body">
                          <div className="seo-todo__action">{t.action}</div>
                          <div className="seo-todo__why">{t.why}</div>
                          <div className="seo-todo__meta">
                            {!!t.page && <span>{shortPage(t.page)}</span>}
                            {t.est_monthly_clicks != null && t.position > 0 && <span>pos {t.position}</span>}
                            {t.impressions != null && <span>{fmt(t.impressions)} impressions</span>}
                          </div>
                          {t.kind === "decay" && (
                            <UpdatePlanButton brandId={brand.id} page={t.page} onToast={onToast} />
                          )}
                        </div>
                        <select className={`seo-status seo-status--${t.status}`} value={t.status}
                                onChange={(e) => void setStatus(t.id, e.target.value as SeoTodoStatus)}>
                          <option value="todo">To do</option>
                          <option value="assigned">Assigned</option>
                          <option value="done">Done</option>
                        </select>
                      </div>
                    ))}
                    {run.todos.length > 10 && (
                      <button className="seo-btn seo-todo__more" onClick={() => setShowAllTodos((v) => !v)}>
                        {showAllTodos ? "Show top 10 only" : `Show all ${run.todos.length}`}
                      </button>
                    )}
                  </div>
                )}
                </Fold>

                <Fold title="Blog plan" count={run.topics.filter((t) => !t.avoided).length}
                      hint="the next posts to publish">
                {(() => {
                  const liveTopics = run.topics.filter((t) => !t.avoided);
                  const avoidedTopics = run.topics.filter((t) => t.avoided);
                  return (
                    <div className="mr-section">
                      <h3 className="mr-section__title">Next 10 blogs to publish</h3>
                      {liveTopics.length === 0 && avoidedTopics.length === 0 && (
                        <div className="seo-empty">No topics yet — add seed keywords to this brand and refresh.</div>
                      )}
                      {liveTopics.map((t, i) => (
                        <div key={t.keyword} className="seo-topic">
                          <div className="seo-topic__main">
                            <span className="seo-topic__kw"><span className="seo-topic__num">{i + 1}</span> {t.keyword}</span>
                            <div className="seo-topic__chips">
                              <span className={`seo-chip seo-chip--tier-${t.priority === "high" ? "high" : t.priority === "medium" ? "medium" : "watch"}`}>
                                {t.priority} priority
                              </span>
                              {t.source === "new idea" && <span className="seo-chip seo-chip--trend-new">new idea</span>}
                              <span className="seo-chip">{t.angle}</span>
                              {t.intent && <span className="seo-chip">{t.intent}</span>}
                              <TrendChip trend={t.trend} />
                              <DifficultyChip difficulty={t.difficulty} />
                            </div>
                            <div className="seo-topic__why">{t.why}</div>
                            <div className="seo-topic__impact">{t.impact}</div>
                          </div>
                          <div className="seo-topic__nums">
                            <span className="seo-topic__vol">{t.volume_label}</span>
                            {t.est_monthly_clicks != null && (
                              <span className="seo-stat__num--good">≈ +{fmt(t.est_monthly_clicks)} clicks/mo</span>
                            )}
                          </div>
                        </div>
                      ))}
                      {avoidedTopics.length > 0 && (
                        <div className="seo-avoided">
                          <button className="seo-btn seo-avoided__toggle" onClick={() => setShowAvoided((v) => !v)}>
                            <Icon name={showAvoided ? "chevron-down" : "chevron-right"} size={13} />
                            Avoided (would cannibalize existing pages) · {avoidedTopics.length}
                          </button>
                          {showAvoided && (
                            <div className="seo-avoided__list">
                              {avoidedTopics.map((t) => (
                                <div key={t.keyword} className="seo-avoided__row">
                                  <span className="seo-topic__kw">{t.keyword}</span>
                                  <span className="seo-avoided__reason">{t.avoided_reason ?? "overlaps an existing page"}</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })()}
                </Fold>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default SeoAgent;
