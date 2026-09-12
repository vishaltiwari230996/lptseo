"use client";

/* The deep audit — landing pages, sitemap, cannibalization, blog keyword
 * density and page speed, each from one crawl of every URL in the sitemap.
 *
 * Built for the reading an SEO lead actually does:
 *
 *  - Every tab opens on its conclusion (the summary strip), then the problems
 *    ranked worst-first, then the complete per-URL table. Nothing is sampled:
 *    every affected URL is reachable, and every finding carries its evidence,
 *    why it matters and the fix.
 *  - Lists are paged ("show more"), and per-page detail loads only when a row
 *    is opened, so a 1,000-page site never ships megabytes to the browser.
 *  - Both audits are background jobs. While one runs, this polls its progress
 *    and reloads the open tab the moment it finishes.
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  seoDeepAudit, seoDeepAuditRun, seoDeepCannibal, seoDeepDensity, seoDeepLanding,
  seoDeepLandingPage, seoDeepSitemap, seoPageSpeed, seoPageSpeedPage, seoPageSpeedRun,
  type DeepCannibalDoc, type DeepDensityDoc, type DeepDensityPost, type DeepFinding,
  type DeepIssue, type DeepJob, type DeepLandingDoc, type DeepLandingPage,
  type DeepSeverity, type DeepSitemapDoc, type DeepSitemapUrl, type DeepSummary,
  type SpeedRow, type SpeedSummary,
} from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";
import { Icon } from "@/lib/kit-ui";

type Tab = "landing" | "sitemap" | "cannibal" | "density" | "speed";

const TABS: { id: Tab; label: string }[] = [
  { id: "landing", label: "Landing pages" },
  { id: "sitemap", label: "Sitemap" },
  { id: "cannibal", label: "Cannibalization" },
  { id: "density", label: "Blog keyword density" },
  { id: "speed", label: "Page speed" },
];

const PAGE = 40;
const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString("en-IN"));
const secs = (ms: number | null | undefined) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`);
const kb = (b: number | null | undefined) =>
  b == null ? "—" : b >= 1048576 ? `${(b / 1048576).toFixed(1)}MB` : `${Math.round(b / 1024)}KB`;
const short = (u: string) => u.replace(/^https?:\/\/[^/]+/, "") || "/";

/* ------------------------------ shared bits ------------------------------ */

function Sev({ s }: { s: DeepSeverity | string }) {
  return <span className={`seo-chip seo-chip--sev-${s}`}>{s}</span>;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" | "bad" }) {
  return (
    <div className={tone ? `deep-stat deep-stat--${tone}` : "deep-stat"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function More({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  if (shown >= total) return null;
  return (
    <button className="seo-btn deep-more" onClick={onMore}>
      Show {Math.min(PAGE, total - shown)} more · {total - shown} remaining
    </button>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="seo-empty">{children}</div>;
}

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fn().then(
      (d) => { if (live) { setData(d); setError(null); setLoading(false); } },
      (e) => { if (live) { setError(describeFailure(e, "Could not load")); setLoading(false); } },
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

/* --------------------------------- job bar -------------------------------- */

function JobBar({ job, label }: { job: DeepJob | null; label: string }) {
  if (!job || !(job.status === "running" && job.alive)) {
    if (job?.status === "failed") {
      return <div className="seo-degraded"><Icon name="alert-triangle" size={14} />
        <div>{label} failed: {job.error}</div></div>;
    }
    if (job?.status === "interrupted") {
      return <div className="seo-degraded"><Icon name="alert-triangle" size={14} />
        <div>{label} was interrupted by a server restart at “{job.phase}”. Start it again —
          page speed resumes where it stopped.</div></div>;
    }
    return null;
  }
  const pct = job.total ? Math.round((100 * job.done) / job.total) : null;
  return (
    <div className="deep-job" role="status" aria-live="polite">
      <div className="deep-job__head">
        <span className="deep-job__spin" aria-hidden="true" />
        <strong>{label}</strong>
        <span>{job.phase}{job.total ? ` · ${fmt(job.done)} of ${fmt(job.total)}` : ""}</span>
      </div>
      {pct != null && (
        <div className="deep-job__track"><span style={{ width: `${pct}%` }} /></div>
      )}
    </div>
  );
}

/* ================================ container =============================== */

export function DeepAuditPanel({ brandId, onToast }: { brandId: string; onToast: ToastFn }) {
  const [tab, setTab] = useState<Tab>("landing");
  const [summary, setSummary] = useState<DeepSummary | null>(null);
  const [job, setJob] = useState<DeepJob | null>(null);
  const [speedJob, setSpeedJob] = useState<DeepJob | null>(null);
  const [version, setVersion] = useState(0);
  const [starting, setStarting] = useState(false);
  const wasRunning = useRef({ deep: false, speed: false });

  const refresh = useCallback(async () => {
    try {
      const d = await seoDeepAudit(brandId);
      setSummary(d.summary);
      setJob(d.job);
      setSpeedJob(d.speed_job);
      const deepLive = !!(d.job && d.job.status === "running" && d.job.alive);
      const speedLive = !!(d.speed_job && d.speed_job.status === "running" && d.speed_job.alive);
      // A job that was running and no longer is: reload what the tabs show.
      if ((wasRunning.current.deep && !deepLive) || (wasRunning.current.speed && !speedLive)) {
        setVersion((v) => v + 1);
        if (wasRunning.current.deep && !deepLive && d.job?.status === "done") onToast("Deep audit complete", "ok");
      }
      wasRunning.current = { deep: deepLive, speed: speedLive };
      return deepLive || speedLive;
    } catch (e) {
      onToast(describeFailure(e, "Could not load the deep audit"), "error");
      return false;
    }
  }, [brandId, onToast]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let live = true;
    const tick = async () => {
      const running = await refresh();
      if (live) timer = setTimeout(tick, running ? 2500 : 15000);
    };
    void tick();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [refresh]);

  async function start() {
    setStarting(true);
    try {
      const { job: j } = await seoDeepAuditRun(brandId);
      setJob(j);
      wasRunning.current.deep = true;
      onToast("Deep audit started — crawling every URL in the sitemap", "ok");
    } catch (e) {
      onToast(describeFailure(e, "Could not start the deep audit"), "error");
    } finally {
      setStarting(false);
    }
  }

  const deepRunning = !!(job && job.status === "running" && job.alive);

  return (
    <div className="deep">
      <div className="deep-head">
        <div>
          <h3 className="mr-section__title">
            Deep audit{summary ? ` · ${fmt(summary.urls)} URLs · ${summary.at}` : ""}
          </h3>
          <p className="deep-head__sub">
            Every URL in every sitemap, crawled once. Landing pages, sitemap, cannibalization and
            blog keyword density are all read from that one snapshot.
          </p>
        </div>
        <button className="seo-btn seo-btn--primary" disabled={deepRunning || starting} onClick={() => void start()}>
          <Icon name="refresh-cw" size={13} /> {summary ? "Re-run deep audit" : "Run deep audit"}
        </button>
      </div>

      <JobBar job={job} label="Deep audit" />
      <JobBar job={speedJob} label="Page speed" />

      {summary && (
        <div className="deep-overview">
          <Stat label="Sitemap health" value={`${summary.sitemap.score}/100`}
                tone={summary.sitemap.score >= 85 ? "good" : summary.sitemap.score >= 60 ? "warn" : "bad"} />
          <Stat label="Landing pages · avg score" value={`${summary.landing.avg_score}`}
                tone={summary.landing.avg_score >= 80 ? "good" : summary.landing.avg_score >= 60 ? "warn" : "bad"} />
          <Stat label="Template-level faults" value={fmt(summary.landing.template_issues)}
                tone={summary.landing.template_issues ? "bad" : "good"} />
          <Stat label="Cannibalization · high" value={fmt(summary.cannibalization.high)}
                tone={summary.cannibalization.high ? "bad" : "good"} />
          <Stat label="Blogs meeting the 150-word rule"
                value={`${fmt(summary.density.pass)} / ${fmt(summary.density.posts)}`}
                tone={summary.density.posts && summary.density.pass / summary.density.posts > 0.8 ? "good" : "bad"} />
        </div>
      )}

      <div className="deep-tabs" role="tablist" aria-label="Deep audit sections">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id}
                  className={`deep-tab${tab === t.id ? " deep-tab--on" : ""}`}
                  onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {!summary && tab !== "speed" ? (
          <Empty>
            No deep audit yet. It discovers every sitemap (following nested indexes to any depth),
            crawls every URL once, and runs all four diagnostics from that crawl. On a 1,000-page
            site it takes about five minutes.
          </Empty>
        ) : tab === "landing" ? <LandingTab brandId={brandId} version={version} />
          : tab === "sitemap" ? <SitemapTab brandId={brandId} version={version} />
          : tab === "cannibal" ? <CannibalTab brandId={brandId} version={version} />
          : tab === "density" ? <DensityTab brandId={brandId} version={version} />
          : <SpeedTab brandId={brandId} version={version} job={speedJob} hasCrawl={!!summary}
                      onToast={onToast} onStarted={(j) => { setSpeedJob(j); wasRunning.current.speed = true; }} />}
      </div>
    </div>
  );
}

/* ============================== landing pages ============================= */

const GRADE_TONE: Record<string, string> = { A: "good", B: "good", C: "warn", D: "bad", F: "bad" };

function FindingList({ findings }: { findings: DeepFinding[] }) {
  if (!findings.length) return <p className="seo-note">Every check passed.</p>;
  let group = "";
  return (
    <div className="deep-findings">
      {findings.map((f, i) => {
        const header = f.group !== group ? (group = f.group) : null;
        return (
          <Fragment key={i}>
            {header && <div className="deep-findings__group">{header}</div>}
            <div className="seo-finding">
              <Sev s={f.severity} />
              <div className="deep-finding__body">
                <div className="seo-finding__title">{f.title}</div>
                <div className="deep-evidence">{f.evidence}</div>
                <div className="seo-finding__detail"><strong>Why it matters.</strong> {f.why}</div>
                <div className="seo-finding__detail"><strong>Fix.</strong> {f.fix}</div>
              </div>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

function LandingTab({ brandId, version }: { brandId: string; version: number }) {
  const { data, error, loading } = useLoad(() => seoDeepLanding(brandId), [brandId, version]);
  const [open, setOpen] = useState<string | null>(null);
  const [openCheck, setOpenCheck] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, DeepLandingPage>>({});
  const [sub, setSub] = useState("all");
  const [shown, setShown] = useState(PAGE);
  const [q, setQ] = useState("");

  const doc: DeepLandingDoc | null = data?.landing ?? null;
  const pages = useMemo(() => (data?.pages ?? []).filter((p) =>
    (sub === "all" || p.subtype === sub) && (!q || p.url.toLowerCase().includes(q.toLowerCase())
      || p.keyword.includes(q.toLowerCase()))), [data, sub, q]);

  async function toggle(url: string) {
    if (open === url) { setOpen(null); return; }
    setOpen(url);
    if (!detail[url]) {
      try {
        const { page } = await seoDeepLandingPage(brandId, url);
        setDetail((d) => ({ ...d, [url]: page }));
      } catch { /* the row stays open with its summary */ }
    }
  }

  if (loading) return <Empty>Loading the landing-page audit…</Empty>;
  if (error) return <Empty>{error}</Empty>;
  if (!doc) return <Empty>No landing-page audit yet — run the deep audit.</Empty>;
  const templates = doc.issues.filter((m) => m.template);

  return (
    <div className="deep-tabbody">
      <div className="deep-strip">
        <Stat label="Landing pages audited" value={fmt(doc.pages)} />
        <Stat label="Average score" value={`${doc.avg_score}/100`}
              tone={doc.avg_score >= 80 ? "good" : doc.avg_score >= 60 ? "warn" : "bad"} />
        <Stat label="High-severity findings" value={fmt(doc.high_total)} tone={doc.high_total ? "bad" : "good"} />
        {(["A", "B", "C", "D", "F"] as const).map((g) => (
          <Stat key={g} label={`Grade ${g}`} value={fmt(doc.grades[g])}
                tone={GRADE_TONE[g] as "good" | "warn" | "bad"} />
        ))}
      </div>
      {!doc.speed_included && (
        <p className="seo-note">
          Performance checks are not included yet — run Page speed and this audit re-scores every
          landing page with its real mobile load time.
        </p>
      )}

      {templates.length > 0 && (
        <section className="deep-callout">
          <h4>Fix once, in the template ({templates.length})</h4>
          <p className="seo-note">
            These fail on at least 80% of landing pages, so they come from the shared template.
            One fix in the template clears them from every page at once — do these first.
          </p>
          {templates.map((m) => (
            <div key={m.check} className="deep-tpl">
              <Sev s={m.severity} />
              <div>
                <div className="seo-finding__title">
                  {m.label} — {fmt(m.count)} of {fmt(doc.pages)} pages ({Math.round(m.share * 100)}%)
                </div>
                <div className="seo-finding__detail">{m.why}</div>
                <div className="seo-finding__detail"><strong>Fix.</strong> {m.fix}</div>
              </div>
            </div>
          ))}
        </section>
      )}

      <section>
        <h4 className="deep-h4">Every check, across every landing page</h4>
        <div className="deep-table-wrap">
          <table className="deep-table">
            <thead><tr>
              <th>Check</th><th>Group</th><th>Severity</th><th className="num">Pages</th><th>Share</th><th />
            </tr></thead>
            <tbody>
              {doc.issues.map((m) => (
                <Fragment key={m.check}>
                  <tr className="deep-row" onClick={() => setOpenCheck(openCheck === m.check ? null : m.check)}>
                    <td>{m.label}{m.template && <span className="seo-chip deep-chip-tpl">template</span>}</td>
                    <td>{m.group}</td>
                    <td><Sev s={m.severity} /></td>
                    <td className="num">{fmt(m.count)}</td>
                    <td><span className="deep-bar"><span style={{ width: `${Math.round(m.share * 100)}%` }} /></span></td>
                    <td className="deep-caret">{openCheck === m.check ? "▾" : "▸"}</td>
                  </tr>
                  {openCheck === m.check && (
                    <tr><td colSpan={6} className="deep-expand">
                      <p className="seo-finding__detail"><strong>Why.</strong> {m.why}</p>
                      <p className="seo-finding__detail"><strong>Fix.</strong> {m.fix}</p>
                      <ul className="deep-evlist">
                        {m.pages.map((p) => (
                          <li key={p.url}><code>{short(p.url)}</code> — {p.evidence}</li>
                        ))}
                      </ul>
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h4 className="deep-h4">Every landing page, worst first</h4>
        <div className="seo-poolbar">
          <input className="seo-input" placeholder="Filter by URL or keyword" value={q}
                 onChange={(e) => { setQ(e.target.value); setShown(PAGE); }} aria-label="Filter pages" />
          <select className="seo-input" value={sub} onChange={(e) => { setSub(e.target.value); setShown(PAGE); }}
                  aria-label="Filter by page type">
            <option value="all">All types ({fmt(doc.pages)})</option>
            {doc.subtypes.map((s) => (
              <option key={s.subtype} value={s.subtype}>{s.subtype} ({s.pages}) · avg {s.avg_score}</option>
            ))}
          </select>
        </div>
        <div className="deep-table-wrap">
          <table className="deep-table">
            <thead><tr>
              <th>Page</th><th>Target keyword</th><th className="num">Score</th>
              <th className="num">High</th><th className="num">Med</th><th className="num">Words</th>
              <th className="num">Links in</th><th className="num">LCP</th><th />
            </tr></thead>
            <tbody>
              {pages.slice(0, shown).map((p) => (
                <Fragment key={p.url}>
                  <tr className="deep-row" onClick={() => void toggle(p.url)}>
                    <td className="deep-url"><code>{short(p.url)}</code><span className="deep-sub">{p.subtype}</span></td>
                    <td>{p.keyword}<span className="deep-sub">from {p.keyword_source}</span></td>
                    <td className="num"><span className={`deep-grade deep-grade--${GRADE_TONE[p.grade]}`}>{p.score} {p.grade}</span></td>
                    <td className="num">{p.high}</td>
                    <td className="num">{p.medium}</td>
                    <td className="num">
                      {fmt(p.words)}
                      {p.unique_words != null && p.unique_words < p.words && (
                        <span className="deep-sub">{fmt(p.unique_words)} unique</span>
                      )}
                    </td>
                    <td className="num">{p.inlinks}</td>
                    <td className="num">{secs(p.lcp_ms)}</td>
                    <td className="deep-caret">{open === p.url ? "▾" : "▸"}</td>
                  </tr>
                  {open === p.url && (
                    <tr><td colSpan={9} className="deep-expand">
                      <div className="deep-expand__meta">
                        <span><strong>Title</strong> {p.title || "—"}</span>
                        <span><strong>H1</strong> {p.h1 || "—"}</span>
                        <span>{p.checks_run} checks run · {p.checks_failed} failed</span>
                      </div>
                      {detail[p.url]?.findings
                        ? <FindingList findings={detail[p.url].findings!} />
                        : <p className="seo-note">Loading findings…</p>}
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        <More shown={shown} total={pages.length} onMore={() => setShown((s) => s + PAGE)} />
      </section>

      {doc.near_duplicate_pairs.length > 0 && (
        <section>
          <h4 className="deep-h4">Near-duplicate landing pages ({doc.near_duplicate_pairs.length} pairs)</h4>
          <p className="seo-note">
            Main content compared word-for-word (exact 5-word shingle overlap). Above 85%, Google treats
            the pages as duplicates and indexes one.
          </p>
          <div className="deep-table-wrap">
            <table className="deep-table">
              <thead><tr><th>Page</th><th>Page</th><th className="num">Identical</th></tr></thead>
              <tbody>
                {doc.near_duplicate_pairs.slice(0, 60).map((d) => (
                  <tr key={d.a + d.b}>
                    <td><code>{short(d.a)}</code></td><td><code>{short(d.b)}</code></td>
                    <td className="num"><strong>{Math.round(d.similarity * 100)}%</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

/* ================================= sitemap ================================ */

function IssueBlock({ issue }: { issue: DeepIssue }) {
  const [open, setOpen] = useState(false);
  const rows = issue.detail.length ? issue.detail : issue.urls.map((u) => ({ url: u }));
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div className="seo-finding deep-issue">
      <Sev s={issue.severity} />
      <div className="deep-finding__body">
        <div className="seo-finding__title">{issue.title}</div>
        <div className="seo-finding__detail">{issue.why}</div>
        <div className="seo-finding__detail"><strong>Fix.</strong> {issue.fix}</div>
        {rows.length > 0 && (
          <button className="deep-link" onClick={() => setOpen((v) => !v)}>
            {open ? "Hide" : "Show"} all {fmt(rows.length)} affected
          </button>
        )}
        {open && (
          <div className="deep-table-wrap">
            <table className="deep-table deep-table--tight">
              <thead><tr>{cols.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
              <tbody>
                {rows.slice(0, 500).map((r, i) => (
                  <tr key={i}>
                    {cols.map((c) => {
                      const v = (r as Record<string, unknown>)[c];
                      return <td key={c}>{Array.isArray(v) ? v.join(", ") : v == null ? "—" : String(v)}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function SitemapTab({ brandId, version }: { brandId: string; version: number }) {
  const { data, error, loading } = useLoad(() => seoDeepSitemap(brandId), [brandId, version]);
  const [onlyProblems, setOnlyProblems] = useState(true);
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(PAGE);
  const doc: DeepSitemapDoc | null = data?.sitemap ?? null;
  const urls: DeepSitemapUrl[] = useMemo(() => (data?.urls ?? []).filter((u) =>
    (!onlyProblems || u.problems.length) && (!q || u.url.toLowerCase().includes(q.toLowerCase()))),
  [data, onlyProblems, q]);

  if (loading) return <Empty>Loading the sitemap diagnosis…</Empty>;
  if (error) return <Empty>{error}</Empty>;
  if (!doc) return <Empty>No sitemap diagnosis yet — run the deep audit.</Empty>;

  return (
    <div className="deep-tabbody">
      <div className="deep-strip">
        <Stat label="Score" value={`${doc.score}/100`} tone={doc.score >= 85 ? "good" : doc.score >= 60 ? "warn" : "bad"} />
        <Stat label="Sitemap files" value={fmt(doc.sitemap_count)} />
        <Stat label="URLs listed" value={fmt(doc.url_count)} />
        <Stat label="URLs with a problem" value={fmt(doc.affected_urls)} tone={doc.affected_urls ? "bad" : "good"} />
        <Stat label="Clean URLs" value={fmt(doc.healthy_urls)} tone="good" />
        <Stat label="URLs with lastmod" value={`${fmt(doc.counts.with_lastmod)}`} />
      </div>

      <section>
        <h4 className="deep-h4">Every sitemap file</h4>
        <div className="deep-table-wrap">
          <table className="deep-table">
            <thead><tr>
              <th>File</th><th>Status</th><th>Kind</th><th className="num">URLs</th>
              <th className="num">lastmod</th><th>In robots.txt</th><th>Listed by</th>
            </tr></thead>
            <tbody>
              {doc.sitemaps.map((s) => (
                <tr key={s.url} className={s.status !== 200 ? "deep-row--bad" : undefined}>
                  <td><code>{s.url}</code></td>
                  <td>{s.status || "—"}</td>
                  <td>{s.kind}{s.kind === "index" ? ` · ${s.child_count} children` : ""}</td>
                  <td className="num">{s.kind === "urlset" ? fmt(s.url_count) : "—"}</td>
                  <td className="num">{s.kind === "urlset" ? `${fmt(s.lastmod_count)} / ${fmt(s.url_count)}` : "—"}</td>
                  <td>{s.declared_in_robots ? "yes" : "no"}</td>
                  <td>{s.parents.length ? s.parents.join(", ") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h4 className="deep-h4">Findings ({doc.issues.length}), worst first</h4>
        {doc.issues.length === 0
          ? <p className="seo-note">No contradictions between the sitemaps and the pages they list.</p>
          : doc.issues.map((i) => <IssueBlock key={i.code} issue={i} />)}
      </section>

      {doc.probes.host_checks.length > 0 && (
        <section>
          <h4 className="deep-h4">Canonical host enforcement</h4>
          <div className="deep-table-wrap">
            <table className="deep-table deep-table--tight">
              <thead><tr><th>Variant</th><th>First response</th><th>Ends at</th><th>Consolidated</th></tr></thead>
              <tbody>
                {doc.probes.host_checks.map((h) => (
                  <tr key={h.url}><td><code>{h.url}</code></td><td>{h.status}</td>
                    <td><code>{h.final_url}</code></td><td>{h.ok ? "yes" : <strong>no</strong>}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <h4 className="deep-h4">Every sitemap URL</h4>
        <div className="seo-poolbar">
          <input className="seo-input" placeholder="Filter URLs" value={q}
                 onChange={(e) => { setQ(e.target.value); setShown(PAGE); }} aria-label="Filter URLs" />
          <label className="deep-check">
            <input type="checkbox" checked={onlyProblems} onChange={(e) => { setOnlyProblems(e.target.checked); setShown(PAGE); }} />
            Only URLs with a problem
          </label>
        </div>
        {urls.length === 0 ? <p className="seo-note">No URLs match.</p> : (
          <div className="deep-table-wrap">
            <table className="deep-table deep-table--tight">
              <thead><tr><th>URL</th><th>Type</th><th>Status</th><th>Canonical</th>
                <th className="num">Links in</th><th>lastmod</th><th>Problems</th></tr></thead>
              <tbody>
                {urls.slice(0, shown).map((u) => (
                  <tr key={u.url}>
                    <td className="deep-url"><code>{u.url}</code>{u.final && <span className="deep-sub">→ {u.final}</span>}</td>
                    <td>{u.type}</td>
                    <td>{u.status || "—"}</td>
                    <td>{u.canonical || "—"}{u.noindex ? " · noindex" : ""}</td>
                    <td className="num">{u.inlinks}</td>
                    <td>{u.lastmod ? u.lastmod.slice(0, 10) : "—"}</td>
                    <td>{u.problems.length ? u.problems.join(", ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <More shown={shown} total={urls.length} onMore={() => setShown((s) => s + PAGE)} />
      </section>
    </div>
  );
}

/* ============================== cannibalization =========================== */

const KIND_LABEL: Record<string, string> = {
  "same-target": "Same target",
  "copied-spoke": "Copied page",
  "numbered-series": "Numbered copy",
  "blog-vs-landing": "Blog vs landing page",
  "hub-spoke-overlap": "Overlapping hub & spoke",
  "hub-spoke": "Hub & spoke (healthy)",
};

function CannibalTab({ brandId, version }: { brandId: string; version: number }) {
  const { data, error, loading } = useLoad(() => seoDeepCannibal(brandId), [brandId, version]);
  const [shown, setShown] = useState(20);
  const doc: DeepCannibalDoc | null = data?.cannibalization ?? null;
  if (loading) return <Empty>Loading the cannibalization report…</Empty>;
  if (error) return <Empty>{error}</Empty>;
  if (!doc) return <Empty>No cannibalization report yet — run the deep audit.</Empty>;

  return (
    <div className="deep-tabbody">
      <div className="deep-strip">
        <Stat label="Indexable pages compared" value={fmt(doc.pages_checked)} />
        <Stat label="High" value={fmt(doc.by_severity.high)} tone={doc.by_severity.high ? "bad" : "good"} />
        <Stat label="Medium" value={fmt(doc.by_severity.medium)} tone={doc.by_severity.medium ? "warn" : "good"} />
        <Stat label="Healthy hub & spoke" value={fmt(doc.by_severity.low)} tone="good" />
        <Stat label="Confirmed by Search Console" value={doc.gsc_connected ? fmt(doc.confirmed.length) : "not connected"} />
      </div>

      <div className="deep-kinds">
        {Object.entries(doc.by_kind).map(([k, n]) => (
          <span key={k} className="deep-kind"><strong>{fmt(n)}</strong> {KIND_LABEL[k] ?? k}</span>
        ))}
      </div>

      {doc.confirmed.length > 0 && (
        <section>
          <h4 className="deep-h4">Confirmed: one query, several of your pages</h4>
          {doc.confirmed.slice(0, 40).map((c) => (
            <div key={c.query} className="seo-finding">
              <Sev s={c.severity} />
              <div className="deep-finding__body">
                <div className="seo-finding__title">“{c.query}” — {fmt(c.impressions)} impressions split</div>
                <ul className="deep-evlist">
                  {c.pages.map((p) => <li key={p.url}><code>{short(p.url)}</code> — {fmt(p.impressions)} impr ·
                    pos {p.position} · {fmt(p.clicks)} clicks</li>)}
                </ul>
              </div>
            </div>
          ))}
        </section>
      )}

      <section>
        <h4 className="deep-h4">Pages competing for the same search ({doc.clusters.length} groups)</h4>
        {doc.clusters.length === 0 ? <p className="seo-note">No competing pages found.</p> :
          doc.clusters.slice(0, shown).map((c) => (
            <div key={c.keep} className="deep-cluster">
              <div className="deep-cluster__head">
                <Sev s={c.severity} />
                <div>
                  <div className="seo-finding__title">Keep <code>{short(c.keep)}</code></div>
                  <div className="deep-sub">target: {c.keyword}</div>
                </div>
              </div>
              {c.competitors.map((x) => (
                <div key={x.url} className="deep-competitor">
                  <div className="deep-competitor__head">
                    <code>{short(x.url)}</code>
                    <span className="seo-chip">{KIND_LABEL[x.kind] ?? x.kind}</span>
                    <span className="deep-sub">
                      {x.similarity == null ? "too little text to compare content"
                        : `${Math.round(x.similarity * 100)}% same content`}
                    </span>
                  </div>
                  <div className="seo-finding__detail">{x.action}</div>
                </div>
              ))}
            </div>
          ))}
        <More shown={shown} total={doc.clusters.length} onMore={() => setShown((s) => s + 20)} />
      </section>

      {(doc.duplicate_titles.length > 0 || doc.duplicate_h1s.length > 0) && (
        <section>
          <h4 className="deep-h4">Identical titles and H1s across indexable pages</h4>
          {[...doc.duplicate_titles.map((d) => ({ ...d, what: "Title" })),
            ...doc.duplicate_h1s.map((d) => ({ ...d, what: "H1" }))].slice(0, 40).map((d, i) => (
            <div key={i} className="seo-finding">
              <span className="seo-chip">{d.what}</span>
              <div className="deep-finding__body">
                <div className="seo-finding__title">“{d.text}” on {d.urls.length} pages</div>
                <div className="deep-evidence">{d.urls.map(short).join(" · ")}</div>
              </div>
            </div>
          ))}
        </section>
      )}

      <section className="deep-method">
        <h4 className="deep-h4">How this is decided</h4>
        <dl>
          {Object.entries(doc.method).map(([k, v]) => (
            <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{v}</dd></Fragment>
          ))}
        </dl>
      </section>
    </div>
  );
}

/* ============================ blog keyword density ======================== */

const VERDICT: Record<string, { label: string; tone: string }> = {
  pass: { label: "Meets the rule", tone: "good" },
  "pass-on-average": { label: "Enough mentions, badly spread", tone: "warn" },
  fail: { label: "Too few mentions", tone: "bad" },
};

const PLACEMENT_LABEL: Record<string, string> = {
  title: "Title", h1: "H1", url: "URL", meta_description: "Meta description",
  first_100_words: "First 100 words", a_subheading: "An H2/H3", image_alt: "An image alt",
  last_100_words: "Last 100 words",
};

function DensityRow({ p, open, onToggle }: { p: DeepDensityPost; open: boolean; onToggle: () => void }) {
  const v = VERDICT[p.verdict];
  return (
    <>
      <tr className="deep-row" onClick={onToggle}>
        <td className="deep-url">{p.title || short(p.url)}<span className="deep-sub"><code>{short(p.url)}</code></span></td>
        <td>{p.focus_keyword}<span className="deep-sub">{p.keyword_source}</span></td>
        <td className="num">{fmt(p.words)}</td>
        <td className="num">{p.mentions}<span className="deep-sub">needs {p.required}</span></td>
        <td className="num">{p.words_per_mention ?? "never"}</td>
        <td className="num">{p.windows_covered}/{p.windows}</td>
        <td><span className={`deep-verdict deep-verdict--${v.tone}`}>{v.label}</span>
          {p.stuffing && <span className="seo-chip seo-chip--sev-medium deep-chip-tpl">stuffing</span>}</td>
        <td className="deep-caret">{open ? "▾" : "▸"}</td>
      </tr>
      {open && (
        <tr><td colSpan={8} className="deep-expand">
          <div className="deep-windows" aria-label="Mentions per 150-word window">
            {p.per_window.map((c, i) => (
              <span key={i} className={c === 0 ? "deep-win deep-win--gap" : c >= 4 ? "deep-win deep-win--hot" : "deep-win"}
                    title={`words ${i * 150 + 1}–${(i + 1) * 150}: ${c} mention(s)`}>{c}</span>
            ))}
          </div>
          <p className="seo-note">
            Each box is one 150-word stretch of the article, left to right; the number is how many times
            the keyword appears in it. Empty stretches are the ones to fix.
            {" "}{p.mentions_exact} exact · {p.mentions_variant} close variant · {p.density_pct}% density.
          </p>
          {p.gaps.length > 0 && (
            <>
              <div className="deep-findings__group">Stretches with no mention ({p.gaps.length})</div>
              <ul className="deep-evlist">
                {p.gaps.map((g) => (
                  <li key={g.window}><strong>Words {g.words}</strong> — starts: “{g.starts_with}”</li>
                ))}
              </ul>
            </>
          )}
          <div className="deep-findings__group">Where the keyword appears</div>
          <div className="deep-placement">
            {Object.entries(p.placement).map(([k, ok]) => (
              <span key={k} className={ok ? "deep-place deep-place--ok" : "deep-place"}>
                {ok ? "✓" : "✕"} {PLACEMENT_LABEL[k] ?? k}
              </span>
            ))}
          </div>
        </td></tr>
      )}
    </>
  );
}

function DensityTab({ brandId, version }: { brandId: string; version: number }) {
  const { data, error, loading } = useLoad(() => seoDeepDensity(brandId), [brandId, version]);
  const [verdict, setVerdict] = useState("all");
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<string | null>(null);
  const doc: DeepDensityDoc | null = data?.density ?? null;
  const posts = useMemo(() => (data?.posts ?? []).filter((p) =>
    (verdict === "all" || p.verdict === verdict) &&
    (!q || p.url.toLowerCase().includes(q.toLowerCase()) || p.focus_keyword.includes(q.toLowerCase()))),
  [data, verdict, q]);

  if (loading) return <Empty>Loading keyword density…</Empty>;
  if (error) return <Empty>{error}</Empty>;
  if (!doc) return <Empty>No keyword-density report yet — run the deep audit.</Empty>;
  const bmax = Math.max(1, ...Object.values(doc.buckets));

  return (
    <div className="deep-tabbody">
      <p className="deep-rule">Rule: {doc.rule}, in every blog article.</p>
      <div className="deep-strip">
        <Stat label="Blog articles measured" value={fmt(doc.posts)} />
        <Stat label="Meet the rule in every stretch" value={fmt(doc.pass_every_window)} tone="good" />
        <Stat label="Enough mentions, badly spread" value={fmt(doc.pass_on_average_only)} tone="warn" />
        <Stat label="Too few mentions" value={fmt(doc.fail)} tone="bad" />
        <Stat label="Median words per mention" value={fmt(doc.median_words_per_mention)}
              tone={(doc.median_words_per_mention ?? 999) <= 150 ? "good" : "bad"} />
        <Stat label="Mentions missing, site-wide" value={fmt(doc.total_missing_mentions)} />
        <Stat label="Over-optimised (stuffing)" value={fmt(doc.stuffing)} tone={doc.stuffing ? "warn" : "good"} />
      </div>

      <div className="deep-two">
        <section>
          <h4 className="deep-h4">Words per mention, across all articles</h4>
          {Object.entries(doc.buckets).map(([k, n]) => {
            // The brand colour is red, so a neutral bar would make the GOOD
            // bucket look like an alarm. Tone follows the rule instead.
            const tone = k.startsWith("≤150") ? "good" : k.startsWith("151") ? "warn" : "bad";
            return (
              <div key={k} className="deep-hbar">
                <span className="deep-hbar__label">{k}</span>
                <span className={`deep-hbar__track deep-hbar__track--${tone}`}>
                  <span style={{ width: `${(100 * n) / bmax}%` }} />
                </span>
                <span className="deep-hbar__n">{fmt(n)}</span>
              </div>
            );
          })}
        </section>
        <section>
          <h4 className="deep-h4">Keyword missing from…</h4>
          {Object.entries(doc.placement_missing).map(([k, n]) => (
            <div key={k} className="deep-hbar">
              <span className="deep-hbar__label">{PLACEMENT_LABEL[k] ?? k}</span>
              <span className="deep-hbar__track deep-hbar__track--bad"><span style={{ width: `${(100 * n) / Math.max(1, doc.posts)}%` }} /></span>
              <span className="deep-hbar__n">{fmt(n)}</span>
            </div>
          ))}
        </section>
      </div>

      <section>
        <h4 className="deep-h4">Every blog article, worst coverage first</h4>
        <div className="seo-poolbar">
          <input className="seo-input" placeholder="Filter by URL or keyword" value={q}
                 onChange={(e) => { setQ(e.target.value); setShown(PAGE); }} aria-label="Filter articles" />
          <select className="seo-input" value={verdict} onChange={(e) => { setVerdict(e.target.value); setShown(PAGE); }}
                  aria-label="Filter by verdict">
            <option value="all">All articles</option>
            <option value="fail">Too few mentions</option>
            <option value="pass-on-average">Enough mentions, badly spread</option>
            <option value="pass">Meets the rule</option>
          </select>
        </div>
        <div className="deep-table-wrap">
          <table className="deep-table">
            <thead><tr>
              <th>Article</th><th>Focus keyword</th><th className="num">Words</th><th className="num">Mentions</th>
              <th className="num">Words / mention</th><th className="num">Stretches covered</th><th>Verdict</th><th />
            </tr></thead>
            <tbody>
              {posts.slice(0, shown).map((p) => (
                <DensityRow key={p.url} p={p} open={open === p.url} onToggle={() => setOpen(open === p.url ? null : p.url)} />
              ))}
            </tbody>
          </table>
        </div>
        <More shown={shown} total={posts.length} onMore={() => setShown((s) => s + PAGE)} />
      </section>

      <section className="deep-method">
        <h4 className="deep-h4">How this is measured</h4>
        <dl>
          {Object.entries(doc.method).map(([k, v]) => (
            <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{v}</dd></Fragment>
          ))}
        </dl>
      </section>
    </div>
  );
}

/* ================================ page speed ============================== */

function RatingCell({ value, rating, text }: { value: number | null | undefined; rating?: string | null; text: string }) {
  if (value == null) return <td className="num">—</td>;
  return <td className="num"><span className={`deep-rate deep-rate--${rating ?? "none"}`}>{text}</span></td>;
}

/** LCP as Google records it — and, when a popup is what painted last, when the
 *  page's own content was actually on screen. */
function LcpCell({ r }: { r: SpeedRow }) {
  if (r.lcp_ms == null) return <td className="num">—</td>;
  return (
    <td className="num">
      <span className={`deep-rate deep-rate--${r.lcp_rating ?? "none"}`}>{secs(r.lcp_ms)}</span>
      {r.lcp_overlay && r.content_lcp_ms != null && (
        <span className="deep-sub">popup · content {secs(r.content_lcp_ms)}</span>
      )}
    </td>
  );
}

function PaintTimeline({ row }: { row: SpeedRow }) {
  const cands = row.lcp_candidates ?? [];
  const shifts = row.shifts ?? [];
  if (!cands.length && !shifts.length) return null;
  return (
    <div className="deep-two deep-two--tight">
      {cands.length > 0 && (
        <section>
          <h5 className="deep-h5">What painted, in order</h5>
          <ol className="deep-steps">
            {cands.map((c, i) => (
              <li key={i}>
                <span className="deep-steps__t">{secs(c.t)}</span>
                <span className="deep-steps__what">
                  <code>{c.el || "(unnamed element)"}</code>
                  {c.overlay && <span className="deep-steps__tag">in popup <code>{c.overlay}</code></span>}
                  {i === cands.length - 1 && <span className="deep-steps__tag deep-steps__tag--lcp">LCP</span>}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}
      {shifts.length > 0 && (
        <section>
          <h5 className="deep-h5">What moved (largest shifts)</h5>
          <ol className="deep-steps">
            {shifts.map((s, i) => (
              <li key={i}>
                <span className="deep-steps__t">{secs(s.t)}</span>
                <span className="deep-steps__what">
                  <strong>{s.v.toFixed(3)}</strong>{" "}
                  {s.nodes.length ? s.nodes.map((n) => <code key={n}>{n}</code>) : "unattributed"}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

function SpeedTab({ brandId, version, job, hasCrawl, onToast, onStarted }: {
  brandId: string; version: number; job: DeepJob | null; hasCrawl: boolean;
  onToast: ToastFn; onStarted: (j: DeepJob) => void;
}) {
  const { data, error, loading } = useLoad(() => seoPageSpeed(brandId), [brandId, version]);
  const [type, setType] = useState("all");
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, SpeedRow>>({});
  const [busy, setBusy] = useState(false);
  const running = !!(job && job.status === "running" && job.alive);
  const sum: SpeedSummary | null = data?.summary ?? null;
  const popup = sum?.popup_lcp;
  const tone = (ms: number | null | undefined) => ((ms ?? 0) <= 2500 ? "good" : (ms ?? 0) <= 4000 ? "warn" : "bad");
  const rows = useMemo(() => (data?.rows ?? [])
    .filter((r) => !r.error && !r.degraded && (type === "all" || r.type === type))
    .sort((a, b) => (b.lcp_ms ?? 0) - (a.lcp_ms ?? 0)), [data, type]);
  const unmeasured = (sum?.failed ?? 0) + (sum?.degraded ?? 0);

  async function start(limit: number | null) {
    setBusy(true);
    try {
      const { job: j } = await seoPageSpeedRun(brandId, { limit });
      onStarted(j);
      onToast(limit ? `Measuring the ${limit} most important pages` : "Measuring every page in the sitemap", "ok");
    } catch (e) {
      onToast(describeFailure(e, "Could not start page speed"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(url: string) {
    if (open === url) { setOpen(null); return; }
    setOpen(url);
    if (!detail[url]) {
      try { const { page } = await seoPageSpeedPage(brandId, url); setDetail((d) => ({ ...d, [url]: page })); }
      catch { /* summary stays visible */ }
    }
  }

  if (loading) return <Empty>Loading page speed…</Empty>;
  if (error) return <Empty>{error}</Empty>;

  return (
    <div className="deep-tabbody">
      <p className="deep-rule">
        Measured in a real Chrome browser, the way Google models a mobile visitor: a mid-range Android
        phone on Slow 4G (150ms round trip, 1.6 Mbps), CPU slowed 4×, empty cache. LCP (Largest
        Contentful Paint) is the moment the biggest thing on the screen is painted; Google’s good
        threshold is 2.5 seconds. When a popup opens later and is bigger than anything on the page,
        Chrome records the popup as the LCP — so both numbers are shown.
      </p>
      <div className="deep-controls">
        <button className="seo-btn seo-btn--primary" disabled={running || busy || !hasCrawl} onClick={() => void start(40)}>
          <Icon name="refresh-cw" size={13} /> Measure the 40 most important pages
        </button>
        <button className="seo-btn" disabled={running || busy || !hasCrawl} onClick={() => void start(null)}>
          Measure every page in the sitemap
        </button>
        <span className="deep-sub">
          {hasCrawl ? "About 20 seconds per page, four at a time. Home and landing pages go first; "
            + "results appear as they land, and a restart resumes." : "Run the deep audit first."}
        </span>
      </div>

      {!sum || !sum.measured ? (
        <Empty>No pages measured yet.</Empty>
      ) : (
        <>
          {popup && popup.pages > 0 && (
            <section className="deep-callout">
              <h4>A popup is the LCP on {fmt(popup.pages)} of {fmt(sum.measured)} pages</h4>
              <p className="seo-note">
                Their own content is on screen at a median {secs(popup.content_median_ms)}. Then{" "}
                <code>{popup.overlays[0]?.overlay ?? "a popup"}</code> opens at a median{" "}
                {secs(popup.lcp_median_ms)}, and because it is the largest thing painted, Chrome records
                the popup as the page’s LCP. A popup covering the page just after a visitor arrives
                from search is also what Google calls an intrusive interstitial.
              </p>
              <div className="seo-finding__detail">
                <strong>Fix.</strong> Don’t open it on arrival — trigger it on exit intent, after a
                scroll or a second page view, or show it as a small bar instead of a modal. That one
                change takes these pages’ LCP from {secs(popup.lcp_median_ms)} to about{" "}
                {secs(popup.content_median_ms)}.
              </div>
            </section>
          )}

          <div className="deep-strip">
            <Stat label="Pages measured" value={`${fmt(sum.measured)} / ${fmt(sum.queued)}`} />
            <Stat label="LCP as Google records it · median" value={secs(sum.lcp.median_ms)}
                  tone={tone(sum.lcp.median_ms)} />
            {popup && popup.pages > 0 && sum.content_lcp && (
              <Stat label="Your content visible · median" value={secs(sum.content_lcp.median_ms)}
                    tone={tone(sum.content_lcp.median_ms)} />
            )}
            <Stat label="75th percentile" value={secs(sum.lcp.p75_ms)} />
            <Stat label="Good (≤2.5s)" value={fmt(sum.lcp.good)} tone="good" />
            <Stat label="Needs improvement" value={fmt(sum.lcp.needs_improvement)} tone="warn" />
            <Stat label="Poor (>4s)" value={fmt(sum.lcp.poor)} tone="bad" />
            <Stat label="Median page weight" value={kb(sum.bytes_median)} />
            <Stat label="Median requests" value={fmt(sum.requests_median)} />
          </div>
          {unmeasured > 0 && (
            <p className="seo-note">
              {fmt(unmeasured)} page{unmeasured === 1 ? "" : "s"} could not be measured from here — the
              load failed, or this machine’s network failed during it — so they are left out of every
              number above. Run “Measure every page” again: it keeps what is measured and does only these.
            </p>
          )}

          <div className="deep-two">
            <section>
              <h4 className="deep-h4">What paints last (most common LCP elements)</h4>
              {sum.common_lcp_elements.map((e) => (
                <div key={e.element} className="deep-hbar deep-hbar--pair">
                  <span className="deep-hbar__label"><code>{e.element}</code></span>
                  <span className="deep-hbar__n">{fmt(e.pages)} page{e.pages === 1 ? "" : "s"}</span>
                </div>
              ))}
            </section>
            <section>
              <h4 className="deep-h4">What moves the layout (worst first)</h4>
              {(sum.common_shift_sources ?? []).length === 0
                ? <p className="seo-note">No layout shift attributed to an element.</p>
                : sum.common_shift_sources!.map((s) => (
                  <div key={s.node} className="deep-hbar deep-hbar--pair">
                    <span className="deep-hbar__label"><code>{s.node}</code></span>
                    <span className="deep-hbar__n">{(s.cls / Math.max(1, s.pages)).toFixed(2)} per page · {s.pages} pages</span>
                  </div>
                ))}
            </section>
          </div>

          <section>
            <h4 className="deep-h4">Third-party weight, per page on average</h4>
            {sum.third_party.map((h) => (
              <div key={h.host} className="deep-hbar deep-hbar--pair">
                <span className="deep-hbar__label">{h.host}</span>
                <span className="deep-hbar__n">{kb(h.bytes / Math.max(1, sum.measured))}</span>
              </div>
            ))}
          </section>

          <section>
            <h4 className="deep-h4">Every measured page, slowest first</h4>
            <div className="seo-poolbar">
              <select className="seo-input" value={type} onChange={(e) => { setType(e.target.value); setShown(PAGE); }}
                      aria-label="Filter by page type">
                <option value="all">All types</option>
                {Object.entries(sum.by_type).map(([t, v]) => (
                  <option key={t} value={t}>{t} ({v.pages}) · median {secs(v.lcp_median_ms)}</option>
                ))}
              </select>
            </div>
            <div className="deep-table-wrap">
              <table className="deep-table">
                <thead><tr>
                  <th>Page</th><th className="num">Main content (LCP)</th><th className="num">First paint</th>
                  <th className="num">Server</th><th className="num">Fully loaded</th><th className="num">Shift (CLS)</th>
                  <th className="num">Blocking</th><th className="num">Size</th><th className="num">Requests</th><th />
                </tr></thead>
                <tbody>
                  {rows.slice(0, shown).map((r) => (
                    <Fragment key={r.url}>
                      <tr className="deep-row" onClick={() => void toggle(r.url)}>
                        <td className="deep-url"><code>{short(r.url)}</code><span className="deep-sub">{r.subtype || r.type}</span></td>
                        <LcpCell r={r} />
                        <td className="num">{secs(r.fcp_ms)}</td>
                        <td className="num">{r.ttfb_ms == null ? "—" : `${r.ttfb_ms}ms`}</td>
                        <td className="num">{secs(r.load_ms)}</td>
                        <RatingCell value={r.cls} rating={r.cls_rating} text={r.cls == null ? "—" : r.cls.toFixed(2)} />
                        <RatingCell value={r.tbt_ms} rating={r.tbt_rating} text={r.tbt_ms == null ? "—" : `${r.tbt_ms}ms`} />
                        <td className="num">{kb(r.bytes)}</td>
                        <td className="num">{fmt(r.requests)}</td>
                        <td className="deep-caret">{open === r.url ? "▾" : "▸"}</td>
                      </tr>
                      {open === r.url && (
                        <tr><td colSpan={10} className="deep-expand">
                          <div className="deep-expand__meta">
                            <span><strong>LCP element</strong> {r.lcp_element || "—"}
                              {r.lcp_overlay ? ` (in popup ${r.lcp_overlay})` : ""}</span>
                            {r.lcp_overlay && (
                              <span><strong>Own content visible</strong> {secs(r.content_lcp_ms)} · {r.content_lcp_element}</span>
                            )}
                            <span>{fmt(r.third_party_requests)} third-party requests · {kb(r.third_party_bytes)}</span>
                            {!!r.cdn_requests && (
                              <span>{fmt(r.cdn_requests)} from CDN hosts · {kb(r.cdn_bytes)}</span>
                            )}
                            <span>{fmt(r.render_blocking)} render-blocking resources</span>
                          </div>
                          {detail[r.url] && <PaintTimeline row={detail[r.url]} />}
                          {!!detail[r.url]?.failed_requests?.length && (
                            <p className="seo-note">
                              <strong>Failed to load:</strong>{" "}
                              {detail[r.url].failed_requests!.slice(0, 5).map((f, i) => (
                                <Fragment key={i}>{i > 0 && " · "}<code>{f.url.slice(0, 90)}</code> ({f.error.replace("net::", "")})</Fragment>
                              ))}
                            </p>
                          )}
                          {detail[r.url]?.heaviest ? (
                            <div className="deep-table-wrap">
                              <table className="deep-table deep-table--tight">
                                <thead><tr><th>Heaviest resources</th><th>Type</th><th className="num">Size</th>
                                  <th className="num">Time</th><th>Party</th></tr></thead>
                                <tbody>
                                  {detail[r.url].heaviest!.map((h, i) => (
                                    <tr key={i}><td className="deep-url"><code>{h.url}</code></td><td>{h.type}</td>
                                      <td className="num">{kb(h.bytes)}</td>
                                      <td className="num">{h.ms == null ? "—" : `${h.ms}ms`}</td>
                                      <td>{h.party === "cdn" ? "CDN" : h.third ? "third-party" : "own"}</td></tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          ) : <p className="seo-note">Loading resource detail…</p>}
                        </td></tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <More shown={shown} total={rows.length} onMore={() => setShown((s) => s + PAGE)} />
          </section>
        </>
      )}
    </div>
  );
}
