"use client";

/* Cross-source "what to do next" list. Reads the priorities snapshot that
 * Task 5's backend endpoint already ranks (critical first) and reduces to
 * source notes plus flat list of items. */

import { useCallback, useEffect, useState } from "react";
import { seoPriorities, seoPrioritiesRefresh, type SeoPrioritiesDoc } from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";

const SEVERITY_LABEL: Record<string, string> = {
  critical: "Critical",
  warning: "Warning",
  suggestion: "Suggestion",
};

export function InsightsView({ brandId, onToast, onNavigate }: {
  brandId: string;
  onToast: ToastFn;
  /** Take the user to the console section that owns this item. The backend
   *  writes `action_link` as a `#`-prefixed hash (`"#vitals"`), which is what
   *  it looked like when the console was one scrolling page; the console is a
   *  sidebar now, so the hash is stripped and handed over as a section id. */
  onNavigate: (sectionId: string) => void;
}) {
  const [doc, setDoc] = useState<SeoPrioritiesDoc | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let live = true;
    setLoaded(false);
    seoPriorities(brandId)
      .then((r) => { if (live) { setDoc(r.priorities); setLoaded(true); } })
      .catch((exc) => {
        if (live) {
          onToast(describeFailure(exc, "Could not load insights"), "error");
          setLoaded(true);
        }
      });
    return () => { live = false; };
  }, [brandId, onToast]);

  const refresh = useCallback(() => {
    setBusy(true);
    seoPrioritiesRefresh(brandId)
      .then((r) => setDoc(r.priorities))
      .catch((exc) => onToast(describeFailure(exc, "Could not rebuild insights"), "error"))
      .finally(() => setBusy(false));
  }, [brandId, onToast]);

  if (!loaded) {
    return <p className="seo-note">Loading insights…</p>;
  }

  return (
    <div className="seo-insights-panel">
      <div className="seo-insights-panel__head">
        <h2 className="mr-section__title">What to do next</h2>
        <button className="seo-btn seo-btn--primary" onClick={refresh} disabled={busy}>
          {busy ? "Rebuilding…" : "Rebuild"}
        </button>
      </div>

      {!doc || doc.items.length === 0 ? (
        <p className="seo-empty">Nothing urgent right now — every connected source is clean.</p>
      ) : (
        <ul className="seo-insights-panel__list">
          {doc.items.map((item) => (
            <li key={item.id} className={`seo-insights-panel__item seo-insights-panel__item--${item.severity}`}>
              <span className={`seo-chip seo-chip--${item.severity}`}>{SEVERITY_LABEL[item.severity]}</span>
              <div className="seo-insights-panel__body">
                <strong>{item.title}</strong>
                <p className="seo-note">{item.why_it_matters}</p>
              </div>
              <button
                type="button"
                className="seo-insights-panel__link"
                onClick={() => onNavigate(item.action_link.replace(/^#/, ""))}
              >
                View →
              </button>
            </li>
          ))}
        </ul>
      )}

      {doc && doc.notes.length > 0 && (
        <ul className="seo-insights-panel__notes">
          {doc.notes.map((n) => <li key={n} className="seo-note">{n}</li>)}
        </ul>
      )}
    </div>
  );
}
