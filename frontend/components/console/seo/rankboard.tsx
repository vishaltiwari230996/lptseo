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
  seoAddCustomQuery, seoRankBoard, seoRemoveCustomQuery, seoSetBrandEvents,
  type SeoBrandEvent, type SeoRankBoardDoc, type SeoRankBoardEntry,
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

/* The exam calendar — the dates this business actually revolves around
 * (registration windows, exam days). Each feeds the hero's countdown nudge
 * with where we rank for its query as the day approaches. */
function EventsCard({ brandId, events, isCreator, onSaved, onToast }: {
  brandId: string; events: SeoBrandEvent[]; isCreator: boolean;
  onSaved: (events: SeoBrandEvent[]) => void; onToast: ToastFn;
}) {
  const [name, setName] = useState("");
  const [when, setWhen] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(next: SeoBrandEvent[]) {
    setBusy(true);
    try {
      const r = await seoSetBrandEvents(brandId, next);
      onSaved(r.events);
      setName(""); setWhen(""); setQuery("");
    } catch (exc) {
      onToast(describeFailure(exc, "Could not save the calendar"), "error");
    } finally {
      setBusy(false);
    }
  }

  const daysLeft = (d: string) =>
    Math.ceil((new Date(`${d}T00:00:00Z`).getTime() - Date.now()) / 86_400_000);

  if (!isCreator && events.length === 0) return null;
  return (
    <div className="mr-section">
      <h3 className="mr-section__title">Exam calendar</h3>
      <p className="seo-note" style={{ maxWidth: "42rem" }}>
        The dates this market moves around. The nearest one shows on the brand hero as a
        countdown, with where we rank for its query.
      </p>
      {events.length > 0 && (
        <div className="seo-cluster__kws">
          {events.map((ev) => {
            const d = daysLeft(ev.date);
            return (
              <span key={ev.name + ev.date}
                    className={`seo-chip seo-fanout__chip${d >= 0 && d <= 45 ? " seo-chip--warn" : ""}`}>
                <span>{ev.name} · {ev.date}{d >= 0 ? ` (${d}d)` : " (past)"}
                  {ev.query ? ` · “${ev.query}”` : ""}</span>
                {isCreator && (
                  <button type="button" className="seo-chip__x" aria-label={`Remove ${ev.name}`}
                          onClick={() => void save(events.filter((x) => x !== ev))}>×</button>
                )}
              </span>
            );
          })}
        </div>
      )}
      {isCreator && (
        <form className="seo-poolbar" onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && when) void save([...events, { name: name.trim(), date: when, query: query.trim() }]);
        }}>
          <input className="seo-input" value={name} placeholder="Event — e.g. CLAT 2027 registration opens"
                 onChange={(e) => setName(e.target.value)} aria-label="Event name" />
          <input className="seo-input" type="date" value={when}
                 onChange={(e) => setWhen(e.target.value)} aria-label="Event date" />
          <input className="seo-input" value={query} placeholder="Query to watch (optional)"
                 onChange={(e) => setQuery(e.target.value)} aria-label="Query to watch" />
          <button className="seo-btn seo-btn--primary" type="submit" disabled={busy || !name.trim() || !when}>
            <Icon name="plus" size={13} /> Add
          </button>
        </form>
      )}
    </div>
  );
}

export function RankBoardView({ brandId, isCreator, onToast, events = [], onEventsSaved }: {
  brandId: string; isCreator: boolean; onToast: ToastFn;
  events?: SeoBrandEvent[]; onEventsSaved?: (events: SeoBrandEvent[]) => void;
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
    <>
    <EventsCard brandId={brandId} events={events} isCreator={isCreator}
                onSaved={(ev) => onEventsSaved?.(ev)} onToast={onToast} />
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
    </>
  );
}
