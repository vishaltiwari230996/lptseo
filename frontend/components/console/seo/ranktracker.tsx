"use client";

/** Rank tracker — the scheduled scoreboard.
 *
 *  200 rows is not an answer, so the worklist leads: the queries where a win
 *  is worth most and nearest. The full table is below it for the person who
 *  wants to look for themselves, and every row opens a drawer with its
 *  history and the gap card.
 *
 *  Two shapes bite if assumed instead of read:
 *  - `RankDaily` ({best, worst, last}) is all-null on a day we ranked
 *    nowhere. The chart renders that as a break in the line, never as a
 *    point at position zero — zero would read as the best possible rank.
 *  - `RankGap["metrics"]` values are null when the page could not be read,
 *    distinct from a real 0. The gap card says so instead of printing "0".
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  isAbortError, RequestSequence,
  seoBuildBrief, seoRankGap, seoRankHistory, seoRankPoolRebuild, seoRankSweep, seoRankTracker,
  type RankDaily, type RankGap, type RankHistory, type RankRow, type RankTrackerDoc,
} from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";
import { Icon } from "@/lib/kit-ui";

type Filter = "all" | "striking" | "losing" | "won" | "unranked";

const STRIKING_LOW = 4;
const STRIKING_HIGH = 20;

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "striking", label: "Striking distance" },
  { id: "losing", label: "Losing" },
  { id: "won", label: "Won" },
  { id: "unranked", label: "Not ranking" },
];

const errMsg = describeFailure;
const fmt = (n: number) => n.toLocaleString("en-US");

function matches(row: RankRow, filter: Filter): boolean {
  const p = row.position;
  switch (filter) {
    case "striking": return p !== null && p >= STRIKING_LOW && p <= STRIKING_HIGH;
    case "won":      return p !== null && p < STRIKING_LOW;
    case "unranked": return p === null;
    case "losing":   return p === null || p >= STRIKING_LOW;
    default:         return true;
  }
}

/** Same visual language as `labs.tsx`'s `Notes` (not imported — that file
 *  doesn't export it), reused here under a different name to avoid a clash
 *  if this panel is ever composed alongside it. */
function NoteList({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <div className="seo-degraded">
      <Icon name="alert-triangle" size={14} />
      <div>{notes.map((n, i) => <div key={i}>{n}</div>)}</div>
    </div>
  );
}

/* --------------------------------- chart ---------------------------------- */

const CHART_W = 520;
const CHART_H = 110;
const CHART_PAD = 10;
const CHART_WORST = 50; // positions beyond this are clamped, not scaled off-chart

function clampPos(pos: number): number {
  return Math.max(1, Math.min(pos, CHART_WORST));
}

/** Position 1 plots near the top; `CHART_WORST` near the bottom. */
function yForPosition(pos: number): number {
  const frac = (clampPos(pos) - 1) / (CHART_WORST - 1);
  return CHART_PAD + frac * (CHART_H - 2 * CHART_PAD);
}

function HistoryChart({ daily }: { daily: [string, RankDaily][] }) {
  if (!daily.length) return <div className="seo-empty">No history yet for this query.</div>;

  const n = daily.length;
  const xFor = (i: number) => (n === 1 ? CHART_W / 2 : CHART_PAD + (i / (n - 1)) * (CHART_W - 2 * CHART_PAD));

  const segments: { x: number; y: number }[][] = [];
  const gaps: { x: number; date: string }[] = [];
  let current: { x: number; y: number }[] = [];
  daily.forEach(([date, d], i) => {
    const x = xFor(i);
    // `last` null means we ranked nowhere that day — a break in the line,
    // never a point plotted at the bottom as if that were rank zero.
    if (d.last == null) {
      if (current.length) { segments.push(current); current = []; }
      gaps.push({ x, date });
      return;
    }
    current.push({ x, y: yForPosition(d.last) });
  });
  if (current.length) segments.push(current);

  return (
    <div>
      <svg className="seo-rank__chart" viewBox={`0 0 ${CHART_W} ${CHART_H}`} role="img"
           aria-label="Rank history over time, lower is better">
        {segments.map((seg, i) => (
          <polyline key={i} fill="none" stroke="var(--brand)" strokeWidth={2}
                    points={seg.map((p) => `${p.x},${p.y}`).join(" ")} />
        ))}
        {segments.flatMap((seg, si) => seg.map((p, pi) => (
          <circle key={`${si}-${pi}`} cx={p.x} cy={p.y} r={2.5} fill="var(--brand)" />
        )))}
        {gaps.map((g, i) => (
          <circle key={i} cx={g.x} cy={CHART_H - CHART_PAD} r={3} className="seo-rank__chart-gap" />
        ))}
      </svg>
      {gaps.length > 0 && (
        <div className="seo-rank__chart-note">
          Not ranking on: {gaps.map((g) => g.date).join(", ")}
        </div>
      )}
    </div>
  );
}

/* --------------------------------- gap card -------------------------------- */

function metricText(v: number | string[] | null): string {
  if (v == null) return "Couldn't read this page";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "None found";
  return String(v);
}

function GapCard({ gap }: { gap: RankGap }) {
  const rows: { label: string; ours: number | string[] | null; theirs: number | string[] | null }[] = [
    { label: "Word count", ours: gap.metrics.words.ours, theirs: gap.metrics.words.theirs },
    { label: "Headings", ours: gap.metrics.headings.ours, theirs: gap.metrics.headings.theirs },
    { label: "Schema types", ours: gap.metrics.schema.ours, theirs: gap.metrics.schema.theirs },
    { label: "Questions answered", ours: gap.metrics.questions.ours, theirs: gap.metrics.questions.theirs },
  ];
  return (
    <div className="seo-rank__gap">
      <p className="seo-rank__gap-narrative">{gap.narrative}</p>
      <NoteList notes={gap.notes} />
      <div className="seo-rank__gap-table">
        <div className="seo-rank__gap-row seo-rank__gap-row--head">
          <span>Metric</span><span>Us</span><span>{gap.their_domain}</span>
        </div>
        {rows.map((r) => (
          <div key={r.label} className="seo-rank__gap-row">
            <span className="seo-rank__gap-label">{r.label}</span>
            <span className={r.ours == null ? "seo-rank__gap-unread" : undefined}>{metricText(r.ours)}</span>
            <span className={r.theirs == null ? "seo-rank__gap-unread" : undefined}>{metricText(r.theirs)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ================================ container ================================ */

export function RankTrackerView({ brandId, isCreator, onToast }: {
  brandId: string; isCreator: boolean; onToast: ToastFn;
}) {
  const [doc, setDoc] = useState<RankTrackerDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const [history, setHistory] = useState<RankHistory | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [gap, setGap] = useState<RankGap | null>(null);
  const [gapBusy, setGapBusy] = useState<string | null>(null);
  const [sweepBusy, setSweepBusy] = useState(false);
  const [poolBusy, setPoolBusy] = useState(false);
  const [briefBusy, setBriefBusy] = useState<string | null>(null);
  const drawerRef = useRef<HTMLDivElement | null>(null);

  // The drawer loads two different entities (history, gap) into the same
  // on-screen slot. Without a supersession guard, opening a slow row and then
  // a fast one lets the slow response land last and overwrite the fast row's
  // data under the fast row's still-showing title — `load()` above already
  // guards its own single entity the same way; these need their own because
  // either can be in flight independently of the other.
  const historySeq = useRef(new RequestSequence());
  const gapSeq = useRef(new RequestSequence());
  useEffect(() => () => { historySeq.current.cancel(); gapSeq.current.cancel(); }, []);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const d = await seoRankTracker(brandId, { signal });
      setDoc(d);
      setError(null);
    } catch (e) {
      if (isAbortError(e)) return;
      setError(errMsg(e, "The rank tracker could not be loaded"));
    }
  }, [brandId]);

  useEffect(() => {
    const ctrl = new AbortController();
    setDoc(null);
    setError(null);
    void load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  // While a sweep runs, poll its progress; stop the moment it isn't, and
  // always clear the interval on unmount.
  useEffect(() => {
    if (doc?.job?.status !== "running") return;
    const timer = setInterval(() => { void load(); }, 4000);
    return () => clearInterval(timer);
  }, [doc?.job?.status, load]);

  useEffect(() => {
    if (open) drawerRef.current?.focus();
  }, [open]);

  async function loadHistory(query: string) {
    const ticket = historySeq.current.start();
    setHistoryError(null);
    try {
      const r = await seoRankHistory(brandId, query, { signal: ticket.signal });
      if (!historySeq.current.isCurrent(ticket)) return; // a newer row was opened meanwhile
      setHistory(r.history);
    } catch (e) {
      if (isAbortError(e) || !historySeq.current.isCurrent(ticket)) return;
      setHistoryError(errMsg(e, "History could not be loaded"));
    }
  }

  function openDrawer(query: string) {
    setOpen(query);
    setHistory(null);
    setHistoryError(null);
    setGap(null);
    void loadHistory(query);
  }

  function closeDrawer() {
    historySeq.current.cancel();
    gapSeq.current.cancel();
    setOpen(null);
    setHistory(null);
    setGap(null);
  }

  async function loadGap(query: string) {
    const ticket = gapSeq.current.start();
    setGapBusy(query);
    try {
      const r = await seoRankGap(brandId, query);
      if (!gapSeq.current.isCurrent(ticket)) return; // the drawer moved on to a different row
      setGap(r.gap);
    } catch (e) {
      if (!gapSeq.current.isCurrent(ticket)) return;
      onToast(errMsg(e, "Could not build the gap analysis"), "error");
    } finally {
      setGapBusy((q) => (q === query ? null : q));
    }
  }

  function whyGap(query: string) {
    if (open !== query) openDrawer(query);
    void loadGap(query);
  }

  async function brief(keyword: string) {
    setBriefBusy(keyword);
    onToast(`Building a brief for "${keyword}"…`);
    try {
      await seoBuildBrief(brandId, keyword);
      onToast("Brief ready — open the Briefs tab");
    } catch (e) {
      onToast(errMsg(e, "Brief failed"), "error");
    } finally {
      setBriefBusy(null);
    }
  }

  async function runSweep() {
    setSweepBusy(true);
    try {
      const { job } = await seoRankSweep(brandId);
      setDoc((d) => d && { ...d, job });
      onToast("Sweep started", "ok");
    } catch (e) {
      onToast(errMsg(e, "Could not start the sweep"), "error");
    } finally {
      setSweepBusy(false);
    }
  }

  async function rebuildPool() {
    setPoolBusy(true);
    try {
      const next = await seoRankPoolRebuild(brandId);
      setDoc(next);
      onToast(next.pool.notes[0] ?? "Pool rebuilt", "ok");
    } catch (e) {
      onToast(errMsg(e, "Could not rebuild the pool"), "error");
    } finally {
      setPoolBusy(false);
    }
  }

  const rows = useMemo(
    () => (doc?.rows ?? []).filter((r) => !r.error && matches(r, filter)),
    [doc, filter],
  );

  const openRow = useMemo(
    () => (doc?.rows ?? []).find((r) => r.query === open) ?? null,
    [doc, open],
  );

  const running = doc?.job?.status === "running";
  const budgetExhausted = doc?.budget.remaining === 0;

  return (
    <div className="seo-stack">
      {error && (
        <div className="seo-degraded">
          <Icon name="alert-triangle" size={14} />
          <div>
            <div><strong>Couldn&apos;t load the rank tracker.</strong> This is a load failure, not an empty result.</div>
            <div>{error}</div>
            <button className="seo-btn" onClick={() => void load()}>
              <Icon name="refresh-cw" size={13} /> Try again
            </button>
          </div>
        </div>
      )}

      {doc && (
        <div className="mr-section">
          <div className="seo-rank__head">
            <div className="seo-rank__head-meta">
              <span>Tracking <strong>{fmt(doc.pool.size)}</strong> of {fmt(doc.pool.cap)} queries</span>
              <span>Last sweep {doc.meta?.at ?? "never"}</span>
              <span>Credits today <strong>{fmt(doc.budget.searches)}</strong> / {fmt(doc.budget.cap)}</span>
              {running && (
                <span className="seo-rank__job" role="status" aria-live="polite">
                  <span className="seo-rank__job-spin" aria-hidden="true" /> Sweep running
                  {doc.job?.total ? ` · ${fmt(doc.job.done)} of ${fmt(doc.job.total)}` : ""}
                </span>
              )}
            </div>
            {isCreator && (
              <div className="seo-rank__head-actions">
                <button className="seo-btn seo-btn--primary" disabled={running || sweepBusy || budgetExhausted}
                        onClick={() => void runSweep()}>
                  <Icon name="refresh-cw" size={13} /> Run now
                </button>
                {/* Rebuilding the pool merges query sources already in storage — it
                    costs zero Serper credits (`build_pool` never calls `charge()`),
                    so an exhausted search budget must not block it, only Run now. */}
                <button className="seo-btn" disabled={running || poolBusy}
                        onClick={() => void rebuildPool()}>
                  Rebuild pool
                </button>
              </div>
            )}
          </div>

          {budgetExhausted && (
            <div className="seo-degraded">
              <Icon name="alert-triangle" size={14} />
              <div>Daily search budget reached — the next sweep runs after midnight UTC.</div>
            </div>
          )}

          <NoteList notes={doc.pool.notes} />

          <div className="seo-rank__worklist">
            <h3 className="mr-section__title">Fix these next</h3>
            {!doc.worklist.length && (
              <div className="seo-empty">Nothing urgent — no query is both close and worth winning right now.</div>
            )}
            {doc.worklist.map((w) => (
              <div key={w.query} className="seo-rank__row">
                <span className="seo-rank__row-query">
                  {w.query}
                  {w.dropped && <span className="seo-chip seo-chip--warn">Dropped out</span>}
                </span>
                <span className="seo-rank__row-pos">{w.position != null ? `#${w.position}` : "not ranking"}</span>
                <span className="seo-rank__row-reason">{w.reason}</span>
                <span className="seo-rank__row-leader">{w.leader} #{w.leader_position}</span>
                <span className="seo-rank__row-actions">
                  <button className="seo-btn" disabled={gapBusy === w.query} onClick={() => whyGap(w.query)}>
                    Why?
                  </button>
                  <button className="seo-btn" disabled={briefBusy === w.query} onClick={() => void brief(w.query)}>
                    Brief
                  </button>
                </span>
              </div>
            ))}
          </div>

          <div className="seo-rank__filters">
            {FILTERS.map((f) => (
              <button key={f.id} className={`seo-rank__filter${filter === f.id ? " seo-rank__filter--on" : ""}`}
                      aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
                {f.label}
              </button>
            ))}
          </div>

          <div className="seo-rank__table-wrap">
            <table className="seo-rank__table">
              <thead>
                <tr>
                  <th scope="col">Query</th><th scope="col">Our rank</th><th scope="col">Δ7d</th>
                  <th scope="col">Leader</th><th scope="col">Impressions</th>
                </tr>
              </thead>
              <tbody>
                {/* Every field below comes straight off the row — `annotate_rows`
                    on the backend computes delta_7d, leader fields and impressions for all
                    200 rows, not just the worklist's top 10, so there is no
                    `top[0]` fallback left here. A row with no leader (we rank
                    #1) truthfully shows "—", not a guess.
                    All three nullable numeric fields (`position`, `delta_7d`,
                    `impressions`) are checked with `!= null`, never truthily —
                    a real 0 (held position exactly, or a custom/harvested/seed
                    query `build_pool` seeds at 0 impressions by construction)
                    is information, not absence, same rule this file already
                    applies to `RankDaily` and `RankGap`'s metrics. */}
                {rows.map((r) => {
                  const delta = r.delta_7d;
                  return (
                    <tr key={r.query}>
                      <td><button className="seo-rank__row-link" onClick={() => openDrawer(r.query)}>{r.query}</button></td>
                      <td className="num">{r.position != null ? `#${r.position}` : "—"}</td>
                      <td className={`num${delta != null ? (delta > 0 ? " seo-rank__delta--up" : delta < 0 ? " seo-rank__delta--down" : "") : ""}`}>
                        {delta == null ? "—" : delta > 0 ? `+${delta}` : `${delta}`}
                      </td>
                      <td>{r.leader ? `${r.leader}${r.leader_position != null ? ` #${r.leader_position}` : ""}` : "—"}</td>
                      <td className="num">{r.impressions != null ? fmt(r.impressions) : "—"}</td>
                    </tr>
                  );
                })}
                {!rows.length && (
                  <tr><td colSpan={5} className="seo-empty">No queries match this filter.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {open && (
        <div className="seo-rank__drawer-backdrop" onClick={closeDrawer}>
          <div
            ref={drawerRef}
            className="seo-rank__drawer"
            role="dialog"
            aria-modal="true"
            aria-label={`History for ${open}`}
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => { if (e.key === "Escape") closeDrawer(); }}
          >
            <div className="seo-rank__drawer-head">
              <h3 className="mr-section__title">{open}</h3>
              <button className="seo-rank__drawer-close" onClick={closeDrawer} aria-label="Close">
                <Icon name="x" size={14} /> Close
              </button>
            </div>

            {historyError && (
              <div className="seo-degraded">
                <Icon name="alert-triangle" size={14} />
                <div>{historyError}</div>
              </div>
            )}
            {history && <HistoryChart daily={history.daily} />}

            {!!openRow?.top.length && (
              <div>
                <div className="seo-stat__label">Top results</div>
                <ol className="seo-rank__top-list">
                  {openRow.top.map((t) => (
                    <li key={t.position}>
                      #{t.position} {t.domain}
                      {t.url && <> — <a href={t.url} target="_blank" rel="noreferrer">{t.url}</a></>}
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {!gap && (
              <button className="seo-btn" disabled={gapBusy === open} onClick={() => void loadGap(open)}>
                {gapBusy === open ? "Comparing…" : "Why? — compare against the leader"}
              </button>
            )}
            {gap && <GapCard gap={gap} />}
          </div>
        </div>
      )}
    </div>
  );
}
