"use client";

/* The chat dock — the SEO strategist one keypress away from every panel.
 *
 * The advisor always existed (Tools → Ask the analyst) but lived three
 * clicks deep, so nobody talked to it. This is the same grounded brain —
 * the backend rebuilds the brand's digest DATA on every question and the
 * model may not invent numbers — surfaced as a floating dock: a pill at the
 * bottom right, a drawer with the running conversation. Multi-turn: the
 * last few turns ride along so "aur uske baad?" keeps its thread.
 */

import { useEffect, useRef, useState } from "react";
import { seoAsk, type SeoAskTurn } from "@/lib/api";
import { describeFailure } from "@/lib/load";
import { Icon } from "@/lib/kit-ui";

const SUGGESTIONS = [
  "What should we do first?",
  "Why did our ranks move this week?",
  "Which content gaps should we write first?",
  "Where is the biggest competitor beating us?",
];

export function ChatDock({ brandId, brandName }: { brandId: string; brandName: string }) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<SeoAskTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // New brand → new conversation. The thread is the brand's, not the tab's.
  useEffect(() => { setMsgs([]); setOpen(false); }, [brandId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, busy, open]);

  async function send(text?: string) {
    const question = (text ?? draft).trim();
    if (!question || busy) return;
    const history = msgs;
    setMsgs((m) => [...m, { role: "user", text: question }]);
    setDraft("");
    setBusy(true);
    try {
      const res = await seoAsk(brandId, question, history.slice(-6));
      setMsgs((m) => [...m, { role: "agent", text: res.answer }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "agent", text: describeFailure(e, "The strategist is unavailable right now.") }]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="seo-dock__pill" onClick={() => setOpen(true)}
              aria-label={`Ask about ${brandName}`}>
        <Icon name="search" size={14} />
        Ask about {brandName}
      </button>
    );
  }

  return (
    <div className="seo-dock" role="dialog" aria-label={`SEO strategist chat for ${brandName}`}>
      <div className="seo-dock__head">
        <strong>SEO strategist</strong>
        <span className="seo-dock__sub">answers from {brandName}&apos;s own data</span>
        <button type="button" className="seo-dock__close" aria-label="Close chat"
                onClick={() => setOpen(false)}>×</button>
      </div>
      <div className="seo-dock__thread" ref={scrollRef}>
        {msgs.length === 0 && (
          <div className="seo-dock__hello">
            <p className="seo-note">
              Grounded in the live digests — ranks, audit, keywords, fan-out. It will say
              so when the data does not have your answer.
            </p>
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="seo-chip" onClick={() => void send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`seo-dock__msg seo-dock__msg--${m.role}`}>{m.text}</div>
        ))}
        {busy && <div className="seo-dock__msg seo-dock__msg--agent seo-dock__msg--busy">thinking…</div>}
      </div>
      <form className="seo-dock__bar" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <input className="seo-input" value={draft} placeholder="Ask anything about this brand…"
               onChange={(e) => setDraft(e.target.value)} aria-label="Your question" />
        <button className="seo-btn seo-btn--primary" type="submit" disabled={busy || !draft.trim()}>
          <Icon name="arrow-left" size={14} style={{ transform: "rotate(180deg)" }} />
        </button>
      </form>
    </div>
  );
}
