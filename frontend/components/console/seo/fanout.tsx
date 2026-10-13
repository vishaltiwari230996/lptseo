"use client";

/* Query fan-out — which sub-queries an AI answer engine splits a topic into,
 * and where the brand stands on each. Gaps lead the list: they are the blog
 * fuel. Each row's provenance chips say where the sub-query came from
 * (Google's own PAA / related searches, or the model's expansion), and the
 * actions reuse machinery that already exists: track on the rank board,
 * build a content brief.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  seoAddCustomQuery, seoBuildBrief, seoFanout, seoFanoutBulk, seoFanoutRemove,
  type DeepJob, type SeoFanoutDoc, type SeoFanoutOverview, type SeoFanoutRow,
} from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";
import { Icon } from "@/lib/kit-ui";

const short = (u: string) => u.replace(/^https?:\/\/[^/]+/, "") || "/";

const SOURCE_LABEL: Record<string, string> = {
  paa: "People also ask",
  related: "Related search",
  ai: "AI expansion",
};

const VERDICT_TONE: Record<string, string> = { covered: "good", weak: "warn", gap: "bad" };
const VERDICT_LABEL: Record<string, string> = {
  covered: "covered", weak: "weak", gap: "gap — write this",
};

function Row({ r, isCreator, busy, onTrack, onBrief }: {
  r: SeoFanoutRow; isCreator: boolean; busy: string | null;
  onTrack: (q: string) => void; onBrief: (q: string) => void;
}) {
  return (
    <tr>
      <td>
        {r.query}
        <span className="deep-sub">
          {r.sources.map((s) => SOURCE_LABEL[s] ?? s).join(" · ")}
        </span>
      </td>
      <td>
        {r.covered_by
          ? <code>{short(r.covered_by)}</code>
          : <span className="seo-note">no page</span>}
      </td>
      <td className="num">{r.position != null ? `#${r.position}` : "—"}</td>
      <td className="num">{r.impressions ? r.impressions.toLocaleString("en-US") : "—"}</td>
      <td><span className={`deep-verdict deep-verdict--${VERDICT_TONE[r.verdict]}`}>{VERDICT_LABEL[r.verdict]}</span></td>
      <td className="seo-fanout__actions">
        {isCreator && (
          <>
            <button type="button" className="seo-btn" disabled={busy === r.query}
                    onClick={() => onTrack(r.query)}>Track</button>
            {r.verdict !== "covered" && (
              <button type="button" className="seo-btn" disabled={busy === r.query}
                      onClick={() => onBrief(r.query)}>Brief</button>
            )}
          </>
        )}
      </td>
    </tr>
  );
}

export function FanoutView({ brandId, isCreator, onToast }: {
  brandId: string; isCreator: boolean; onToast: ToastFn;
}) {
  const [doc, setDoc] = useState<SeoFanoutDoc | null>(null);
  const [recent, setRecent] = useState<string[]>([]);
  const [overview, setOverview] = useState<SeoFanoutOverview | null>(null);
  const [draft, setDraft] = useState("");
  const [building, setBuilding] = useState(false);
  const [bulkJob, setBulkJob] = useState<DeepJob | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (pollTimer.current) clearTimeout(pollTimer.current); }, []);

  const open = useCallback((query: string) => {
    seoFanout(brandId, query)
      .then((r) => {
        setRecent(r.recent);
        if (r.overview !== undefined) setOverview(r.overview ?? null);
        if (r.fanout) setDoc(r.fanout);
        else if (query) void buildFor(query); // not cached yet — build it
      })
      .catch((exc) => onToast(describeFailure(exc, "Could not load the fan-out"), "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brandId, onToast]);

  useEffect(() => {
    setDoc(null);
    seoFanout(brandId, "")
      .then((r) => {
        setRecent(r.recent);
        setOverview(r.overview ?? null);
        if (r.recent[0]) open(r.recent[0]); // land on the last mapped topic
      })
      .catch(() => { /* an empty panel is a normal first-run state */ });
  }, [brandId, open]);

  async function buildFor(raw: string) {
    // Same paste grammar as the rank tracker's box: one per line, or commas.
    const queries = Array.from(new Set(raw.split(/[\n,]/).map((q) => q.trim()).filter(Boolean)));
    if (!queries.length) return;
    setBuilding(true);
    try {
      const r = await seoFanoutBulk(brandId, queries);
      setRecent(r.recent);
      setDraft("");
      if (r.job) {
        // Big paste — it runs like the deep audit: poll until done, then
        // report the persisted outcome.
        setBulkJob(r.job);
        onToast(`Mapping ${r.queued} topics in the background…`, "ok");
        const poll = async () => {
          try {
            const s = await seoFanout(brandId, "");
            setRecent(s.recent);
            const running = !!(s.job && s.job.status === "running" && s.job.alive);
            setBulkJob(s.job ?? null);
            if (running) {
              pollTimer.current = setTimeout(poll, 2500);
              return;
            }
            const out = s.outcome;
            if (out) {
              const failedRows = out.results.filter((x) => !x.ok);
              onToast(`${out.ok} topics mapped`
                + (out.failed ? `; ${out.failed} failed (${failedRows.map((f) => f.query).join(", ")})` : "")
                + (out.skipped.length ? `; ${out.skipped.length} past the cap` : ""),
                out.failed ? "warn" : "ok");
              const first = out.results.find((x) => x.ok);
              if (first) open(first.query);
            }
          } catch {
            pollTimer.current = setTimeout(poll, 4000); // transient — keep polling
          }
        };
        pollTimer.current = setTimeout(poll, 2500);
        return;
      }
      const ok = (r.results ?? []).filter((x) => x.ok);
      const failed = (r.results ?? []).filter((x) => !x.ok);
      if (ok[0]) open(ok[0].query);
      if (failed.length) {
        onToast(`${ok.length} mapped; failed: ${failed.map((f) => `“${f.query}” (${f.error})`).join("; ")}`,
                ok.length ? "warn" : "error");
      } else if (queries.length > 1) {
        onToast(`${ok.length} topics mapped — pick one from the chips`, "ok");
      }
      if (r.skipped?.length) {
        onToast(`${r.skipped.length} past the per-paste cap, not mapped: ${r.skipped.join(", ")}`, "warn");
      }
    } catch (exc) {
      onToast(describeFailure(exc, "Could not map the fan-out"), "error");
    } finally {
      setBuilding(false);
    }
  }

  async function removeTopics(queries: string[]) {
    try {
      const r = await seoFanoutRemove(brandId, queries);
      setRecent(r.recent);
      if (doc && queries.some((q) => q.toLowerCase() === doc.query.toLowerCase())) {
        setDoc(null);
        if (r.recent[0]) open(r.recent[0]);
      }
      onToast(queries.length === 1 ? `Removed “${queries[0]}”` : `Removed ${queries.length} topics`, "ok");
    } catch (exc) {
      onToast(describeFailure(exc, "Could not remove"), "error");
    }
  }

  async function track(query: string) {
    setRowBusy(query);
    try {
      await seoAddCustomQuery(brandId, query);
      onToast(`Tracking “${query}” — it ranks on the next sweep`, "ok");
    } catch (exc) {
      onToast(describeFailure(exc, "Could not add the query"), "error");
    } finally {
      setRowBusy(null);
    }
  }

  async function brief(query: string) {
    setRowBusy(query);
    try {
      await seoBuildBrief(brandId, query);
      onToast(`Brief ready for “${query}” — it's in Tools → Content briefs`, "ok");
    } catch (exc) {
      onToast(describeFailure(exc, "Could not build the brief"), "error");
    } finally {
      setRowBusy(null);
    }
  }

  const summary = doc?.summary;

  return (
    <div className="mr-section">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">
          Fan-out{doc ? ` — “${doc.query}”` : ""}
          {doc?.cached ? <span className="seo-chip" title="Served from the day's cache">cached</span> : null}
        </h3>
      </div>
      {overview && (
        <div className="seo-herogrid seo-fanout__overview">
          <div className="seo-kpi seo-kpi--still">
            <span className="seo-kpi__label">Topics mapped</span>
            <span className="seo-kpi__value">{overview.topics}</span>
            <span className="seo-kpi__sub">head queries with a live fan-out</span>
          </div>
          <div className="seo-kpi seo-kpi--still">
            <span className="seo-kpi__label">Content gaps</span>
            <span className="seo-kpi__value seo-kpi__value--bad">{overview.gap}</span>
            <span className="seo-kpi__sub">sub-queries with no page — the writing queue</span>
          </div>
          <div className="seo-kpi seo-kpi--still">
            <span className="seo-kpi__label">Weak</span>
            <span className="seo-kpi__value">{overview.weak}</span>
            <span className="seo-kpi__sub">page exists, not ranking top-10</span>
          </div>
          <div className="seo-kpi seo-kpi--still">
            <span className="seo-kpi__label">Covered</span>
            <span className="seo-kpi__value seo-kpi__value--good">{overview.covered}</span>
            <span className="seo-kpi__sub">already answered top-10</span>
          </div>
          {overview.top_gaps[0] && (
            <button type="button" className="seo-kpi" onClick={() => open(overview.top_gaps[0].query)}>
              <span className="seo-kpi__label">Biggest gap topic</span>
              <span className="seo-kpi__value"><span className="seo-kpi__name">{overview.top_gaps[0].query}</span></span>
              <span className="seo-kpi__sub">{overview.top_gaps[0].gap} gaps — open it</span>
            </button>
          )}
        </div>
      )}

      <p className="seo-note" style={{ maxWidth: "42rem" }}>
        AI answers don&apos;t retrieve for one query — they fan out into the questions around it.
        Map a topic to see every sub-query Google and the model expect, and whether a page of
        ours answers each one. Gaps lead the list: they are the next articles.
      </p>

      {isCreator ? (
        <form className="seo-poolbar seo-fanout__form"
              onSubmit={(e) => { e.preventDefault(); if (draft.trim()) void buildFor(draft); }}>
          <textarea className="seo-input seo-fanout__paste" value={draft} rows={2}
                    placeholder={"Head queries — one per line (or commas)\ne.g. clat coaching, judiciary exam preparation"}
                    onChange={(e) => setDraft(e.target.value)} aria-label="Head queries to fan out" />
          <button className="seo-btn seo-btn--primary" type="submit" disabled={building || !draft.trim()}>
            <Icon name="search" size={13} /> {building ? "Mapping…" : "Map the fan-out"}
          </button>
        </form>
      ) : (
        <p className="seo-note">A creator maps new topics; the results below are shared.</p>
      )}

      {bulkJob && bulkJob.status === "running" && bulkJob.alive && (
        <p className="seo-note" role="status">
          Mapping {bulkJob.done} of {bulkJob.total || "…"} topics — chips fill in live, you can
          open any of them meanwhile.
        </p>
      )}

      {recent.length > 0 && (
        <div className="seo-cluster__kws">
          {recent.map((q) => (
            <span key={q} className={`seo-chip seo-fanout__chip${doc?.query === q ? " seo-chip--viz-on" : ""}`}>
              <button type="button" className="seo-fanout__chipopen" onClick={() => open(q)}>{q}</button>
              {isCreator && (
                <button type="button" className="seo-chip__x" aria-label={`Remove ${q}`}
                        onClick={() => void removeTopics([q])}>×</button>
              )}
            </span>
          ))}
          {isCreator && recent.length > 1 && (
            <button type="button" className="seo-chip"
                    onClick={() => void removeTopics(recent)}>
              Clear all ({recent.length})
            </button>
          )}
        </div>
      )}

      {!doc ? (
        <div className="seo-empty">
          {building ? "Mapping the fan-out — one live SERP plus the model…"
            : "No fan-out mapped yet — give it a head query the brand must own."}
        </div>
      ) : (
        <>
          {summary && (
            <div className="seo-brief__rankstats">
              <span className="seo-brief__bad"><strong>{summary.gap}</strong> gaps</span>
              <span><strong>{summary.weak}</strong> weak</span>
              <span className="seo-brief__good"><strong>{summary.covered}</strong> covered</span>
            </div>
          )}
          <div className="deep-table-wrap">
            <table className="deep-table">
              <thead><tr>
                <th>Sub-query</th><th>Our page</th><th className="num">Rank</th>
                <th className="num">Impr · 28d</th><th>Verdict</th><th />
              </tr></thead>
              <tbody>
                {doc.subqueries.map((r) => (
                  <Row key={r.query} r={r} isCreator={isCreator} busy={rowBusy}
                       onTrack={(q) => void track(q)} onBrief={(q) => void brief(q)} />
                ))}
              </tbody>
            </table>
          </div>
          {doc.notes.map((n) => <p key={n} className="seo-note">{n}</p>)}
        </>
      )}
    </div>
  );
}
