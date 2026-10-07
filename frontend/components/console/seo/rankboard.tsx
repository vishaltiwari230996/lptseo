"use client";

/* The expert-curated rank board.
 *
 * Queries here are written by the team's SEO experts — never generated. For
 * each one: where the brand ranks, and every competitor ranking above it,
 * expandable to the full list. Queries the wider tracker harvested from GSC
 * or SERP features stay in the Rank tracker section; this board is only what
 * the team chose to watch.
 */

import { Fragment, useCallback, useEffect, useState } from "react";
import {
  seoAddCustomQuery, seoRankBoard, seoRemoveCustomQuery,
  type SeoRankBoardDoc, type SeoRankBoardEntry,
} from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";
import { Icon } from "@/lib/kit-ui";

const short = (u: string) => u.replace(/^https?:\/\/[^/]+/, "") || "/";

function Delta7({ value, dropped }: { value: number | null; dropped: boolean }) {
  if (dropped) return <span className="seo-chip seo-chip--sev-high">dropped out</span>;
  if (value == null || value === 0) return <span className="seo-note">—</span>;
  return value > 0
    ? <span className="seo-delta seo-delta--up"><Icon name="trending-up" size={13} />{value}</span>
    : <span className="seo-delta seo-delta--down"><Icon name="trending-down" size={13} />{Math.abs(value)}</span>;
}

function Row({ r, isCreator, onRemove }: {
  r: SeoRankBoardEntry; isCreator: boolean; onRemove: (q: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const leader = r.above[0];
  return (
    <Fragment>
      <tr className="deep-row" onClick={() => setOpen((v) => !v)}>
        <td>{r.query}</td>
        <td className="num">{r.position != null ? `#${r.position}` : "not in top 20"}</td>
        <td className="deep-url">{r.url ? <code>{short(r.url)}</code> : "—"}</td>
        <td>
          {leader
            ? <>
                <strong>{leader.domain}</strong> #{leader.position}
                {r.above.length > 1 && <span className="deep-sub">+{r.above.length - 1} more above us</span>}
              </>
            : <span className="seo-brief__good">nobody — we lead</span>}
        </td>
        <td className="num"><Delta7 value={r.delta_7d} dropped={r.dropped} /></td>
        <td className="deep-caret">{open ? "▾" : "▸"}</td>
      </tr>
      {open && (
        <tr><td colSpan={6} className="deep-expand">
          {r.above.length === 0 ? (
            <p className="seo-note">No one ranks above this query&apos;s page.</p>
          ) : (
            <ul className="deep-evlist">
              {r.above.map((e) => (
                <li key={e.url}>
                  #{e.position} — <strong>{e.domain}</strong>{" "}
                  <a href={e.url} target="_blank" rel="noreferrer">{e.title || short(e.url)}</a>
                </li>
              ))}
            </ul>
          )}
          {isCreator && (
            <button type="button" className="seo-btn"
                    onClick={(ev) => { ev.stopPropagation(); onRemove(r.query); }}>
              Stop tracking this query
            </button>
          )}
        </td></tr>
      )}
    </Fragment>
  );
}

export function RankBoardView({ brandId, isCreator, onToast }: {
  brandId: string; isCreator: boolean; onToast: ToastFn;
}) {
  const [doc, setDoc] = useState<SeoRankBoardDoc | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    seoRankBoard(brandId)
      .then((d) => { setDoc(d); setLoaded(true); })
      .catch((exc) => {
        onToast(describeFailure(exc, "Could not load the rank board"), "error");
        setLoaded(true);
      });
  }, [brandId, onToast]);

  useEffect(() => { setLoaded(false); load(); }, [load]);

  async function add() {
    const query = draft.trim();
    if (!query) return;
    setBusy(true);
    try {
      await seoAddCustomQuery(brandId, query);
      setDraft("");
      onToast(`Tracking “${query}” — it will rank on the next sweep`, "ok");
      load();
    } catch (exc) {
      onToast(describeFailure(exc, "Could not add the query"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function remove(query: string) {
    try {
      await seoRemoveCustomQuery(brandId, query);
      onToast(`Stopped tracking “${query}” — its history is kept`, "ok");
      load();
    } catch (exc) {
      onToast(describeFailure(exc, "Could not remove the query"), "error");
    }
  }

  if (!loaded) return <p className="seo-note">Loading the rank board…</p>;
  if (!doc) return <p className="seo-empty">Could not load the rank board — try again.</p>;

  return (
    <div className="mr-section">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">
          Rank board — {doc.custom_queries.length} expert quer{doc.custom_queries.length === 1 ? "y" : "ies"}
          {doc.last_sweep?.at ? ` · checked ${doc.last_sweep.at.slice(0, 16).replace("T", " ")}` : ""}
        </h3>
      </div>
      <p className="seo-note">
        Queries on this board are written by the team, never generated. Each row shows where{" "}
        the brand ranks and everyone above it — open a row for the full list.
      </p>

      {isCreator && (
        <form className="seo-poolbar" onSubmit={(e) => { e.preventDefault(); void add(); }}>
          <input className="seo-input" value={draft} placeholder="Add a query the team wants to watch"
                 onChange={(e) => setDraft(e.target.value)} aria-label="New expert query" />
          <button className="seo-btn seo-btn--primary" type="submit" disabled={busy || !draft.trim()}>
            <Icon name="plus" size={13} /> Track it
          </button>
        </form>
      )}

      {doc.rows.length === 0 && doc.pending.length === 0 ? (
        <div className="seo-empty">
          No expert queries yet{isCreator ? " — add the first one above." : " — a creator can add them here."}
        </div>
      ) : (
        <div className="deep-table-wrap">
          <table className="deep-table">
            <thead><tr>
              <th>Query</th><th className="num">Our rank</th><th>Our page</th>
              <th>Best above us</th><th className="num">7d</th><th />
            </tr></thead>
            <tbody>
              {doc.rows.map((r) => (
                <Row key={r.query} r={r} isCreator={isCreator} onRemove={(q) => void remove(q)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {doc.pending.length > 0 && (
        <p className="seo-note">
          Waiting for the next sweep: {doc.pending.map((q) => `“${q}”`).join(", ")}
        </p>
      )}
    </div>
  );
}
