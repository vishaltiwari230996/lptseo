"use client";

/* The employee's daily brief, in the order a person actually reads it:
 * where do we rank → what is broken → what is fine → fix now → fix next.
 *
 * Every line is deterministic (backend brief.py reads persisted digests —
 * no LLM anywhere near this panel) and carries a `link` to the console
 * section holding the evidence. The brief is built fresh on every read, so
 * there is no refresh button: re-opening the section IS the refresh.
 */

import { useEffect, useState } from "react";
import { seoBrief, type SeoBriefDoc, type SeoBriefLine } from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";

function Block({ title, tone, lines, empty, onNavigate }: {
  title: string;
  tone: "good" | "bad" | "fix" | "later";
  lines: SeoBriefLine[];
  empty: string;
  onNavigate: (sectionId: string) => void;
}) {
  return (
    <section className={`seo-brief__block seo-brief__block--${tone}`}>
      <h3 className="seo-brief__title">{title}</h3>
      {lines.length === 0 ? (
        <p className="seo-note">{empty}</p>
      ) : (
        <ul className="seo-brief__list">
          {lines.map((line, i) => (
            <li key={i}>
              <span>{line.text}</span>
              <button type="button" className="seo-insights-panel__link"
                      onClick={() => onNavigate(line.link)}>
                View
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function InsightsView({ brandId, onToast, onNavigate }: {
  brandId: string;
  onToast: ToastFn;
  /** Take the user to the console section that owns a line — section ids,
   *  routed across both workspaces by resolveSection. */
  onNavigate: (sectionId: string) => void;
}) {
  const [doc, setDoc] = useState<SeoBriefDoc | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let live = true;
    setLoaded(false);
    seoBrief(brandId)
      .then((r) => { if (live) { setDoc(r.brief); setLoaded(true); } })
      .catch((exc) => {
        if (live) {
          onToast(describeFailure(exc, "Could not load the daily brief"), "error");
          setLoaded(true);
        }
      });
    return () => { live = false; };
  }, [brandId, onToast]);

  if (!loaded) return <p className="seo-note">Loading the daily brief…</p>;
  if (!doc) return <p className="seo-empty">Could not load the daily brief — try again.</p>;

  const rank = doc.current_rank;

  return (
    <div className="seo-brief">
      <section className="seo-brief__block seo-brief__block--rank">
        <h3 className="seo-brief__title">Current rank</h3>
        {rank ? (
          <div className="seo-brief__rank">
            <div className="seo-brief__rankstats">
              <span><strong>{rank.top3}</strong> in the top 3</span>
              <span><strong>{rank.page1}</strong> on page 1</span>
              <span><strong>{rank.striking}</strong> in striking distance</span>
              <span><strong>{rank.unranked}</strong> not ranking</span>
              {rank.moved_down > 0 && <span className="seo-brief__bad">▼ {rank.moved_down} fell this week</span>}
              {rank.dropouts > 0 && <span className="seo-brief__bad">{rank.dropouts} dropped out</span>}
              {rank.moved_up > 0 && <span className="seo-brief__good">▲ {rank.moved_up} climbed</span>}
            </div>
            {rank.best.length > 0 && (
              <p className="seo-note">
                Best: {rank.best.map((b) => `'${b.query}' #${b.position}`).join(" · ")}
              </p>
            )}
            <button type="button" className="seo-btn" onClick={() => onNavigate("rank-board")}>
              Open the rank board
            </button>
          </div>
        ) : (
          <p className="seo-note">
            No rank data yet — add the team&apos;s queries on the Rank board and run a sweep.
          </p>
        )}
      </section>

      <Block title="What is not working" tone="bad" lines={doc.not_working}
             empty="Nothing broken that the connected sources can see." onNavigate={onNavigate} />
      <Block title="What is working" tone="good" lines={doc.working}
             empty="No confirmed wins yet — they show up here as data accumulates." onNavigate={onNavigate} />
      <Block title="Immediate fixes" tone="fix" lines={doc.immediate}
             empty="Nothing urgent — the immediate queue is clear." onNavigate={onNavigate} />
      <Block title="Secondary fixes" tone="later" lines={doc.secondary}
             empty="No queued content work right now." onNavigate={onNavigate} />

      {doc.notes.length > 0 && (
        <ul className="seo-insights-panel__notes">
          {doc.notes.map((n) => <li key={n} className="seo-note">{n}</li>)}
        </ul>
      )}
    </div>
  );
}
