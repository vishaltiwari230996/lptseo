"use client";

/* The signed-out landing page.
 *
 * One idea carries it: rank is the language this business already speaks. Law
 * coaching sells All India Ranks, cut-offs and merit lists; search is ranks
 * too. So the hero is a merit list resolving — the queries that bring students
 * to lawpreptutorial.com climbing into place while their rank numerals count
 * down — and nothing else on the page moves unless somebody asks it to.
 *
 * Sign-in is the same `LoginScreen` the app has always used, laid over this
 * page rather than replacing it, so the local-dev door and the Google button
 * both keep working untouched.
 */

import { useEffect, useRef, useState } from "react";
import HeroMedia from "@/components/HeroMedia";
import LoginScreen from "@/components/LoginScreen";
import { BrandMark } from "@/lib/kit-ui";

/** The board's rows: a real query, where it started, where it sits now.
 *
 *  These are illustrative — the console shows live Search Console figures once
 *  a brand is connected — but they are the actual query shapes this business
 *  competes on (entrance exams, judiciary, NLU admissions), not lorem. A
 *  landing page that demos a product with invented vocabulary teaches the
 *  reader the wrong words for the thing they are about to use. */
const ROWS: { q: string; from: number; to: number }[] = [
  { q: "clat coaching", from: 14, to: 3 },
  { q: "judiciary exam preparation", from: 9, to: 2 },
  { q: "law entrance exam after 12th", from: 22, to: 6 },
  { q: "ailet vs clat difficulty", from: 17, to: 4 },
  { q: "best nlu in india", from: 31, to: 8 },
];

const CLIMB_MS = 1100;
const ROW_STAGGER_MS = 110;
const ROW_RISE_MS = 620;

/** Ease-out cubic — the count decelerates into its final rank instead of
 *  arriving at a constant rate, which is what makes it read as settling. */
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function MeritBoard() {
  // Server-render the settled state. The board is real content, so it must be
  // correct with no JS and correct for anyone who has asked motion to stop;
  // the count is an enhancement layered on afterwards.
  const [ranks, setRanks] = useState<number[]>(() => ROWS.map((r) => r.to));
  const [movesShown, setMovesShown] = useState(false);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setMovesShown(true);
      return;
    }
    setRanks(ROWS.map((r) => r.from));
    const startAt = performance.now() + ROW_STAGGER_MS * ROWS.length + ROW_RISE_MS * 0.45;

    const tick = (now: number) => {
      const t = Math.min(1, Math.max(0, (now - startAt) / CLIMB_MS));
      const eased = easeOut(t);
      setRanks(ROWS.map((r) => Math.round(r.from + (r.to - r.from) * eased)));
      if (t < 1) {
        frame.current = requestAnimationFrame(tick);
      } else {
        setMovesShown(true);
      }
    };
    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, []);

  return (
    <div className="lp-board">
      <div className="lp-board__head">
        <span className="lp-board__title">Where lawpreptutorial.com ranks</span>
        <span className="lp-board__meta">Google · India</span>
      </div>
      {ROWS.map((row, i) => (
        <div
          key={row.q}
          className="lp-row"
          style={{ ["--lp-delay" as string]: `${i * ROW_STAGGER_MS}ms` }}
        >
          {/* The live count is announced once it settles, not on every frame —
              a rank ticking through 30 values would flood a screen reader. */}
          <span className="lp-row__rank" aria-hidden="true">{ranks[i]}</span>
          <span className="lp-row__q">
            {row.q}
            <span className="sr"> — now ranked {row.to}, up from {row.from}</span>
          </span>
          <span className="lp-row__move" data-shown={movesShown ? "1" : "0"} aria-hidden="true">
            +{row.from - row.to}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Landing() {
  const [authOpen, setAuthOpen] = useState(false);

  // Escape closes the sign-in layer, and the page underneath must not scroll
  // while it is open.
  useEffect(() => {
    if (!authOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAuthOpen(false);
    };
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [authOpen]);

  return (
    <div className="lp">
      <section className="lp-hero">
        {/* Background first in the DOM, and inert: it is scenery, and a screen
            reader landing on the page should meet the headline, not a canvas. */}
        <HeroMedia />
        <div className="lp-scrim" aria-hidden="true" />

        <header className="lp-top">
          <div className="lp-top__brand">
            <BrandMark size={32} title="Law Prep Tutorial" />
            <span className="lp-top__name">Law Prep <em>Tutorial</em></span>
          </div>
          <button type="button" className="lp-signin" onClick={() => setAuthOpen(true)}>
            Sign in
          </button>
        </header>

        <div className="lp-hero__inner">
          <div className="lp-hero__copy">
            <h1>
              Every aspirant searches
              <br />
              <span>before they enrol.</span>
            </h1>
            <p className="lp-hero__sub">
              This console tracks where lawpreptutorial.com stands for the
              queries that bring students in — which pages earn them, which are
              slipping, and what to fix next.
            </p>
            <div className="lp-actions">
              <button type="button" className="lp-cta" onClick={() => setAuthOpen(true)}>
                Open the console
              </button>
            </div>
            <p className="lp-hero__note">
              For the Law Prep Tutorial marketing team.
            </p>
          </div>
          <MeritBoard />
        </div>
      </section>

      <section className="lp-what">
        <div className="lp-what__item">
          <h2>See what students actually search</h2>
          <p>
            Live Search Console queries and SERP positions per page, so you are
            reading demand rather than guessing at it.
          </p>
        </div>
        <div className="lp-what__item">
          <h2>Work a list that is ordered by impact</h2>
          <p>
            Every fix is scored on the traffic it stands to win, so the top of
            the list is the thing worth doing on Monday morning.
          </p>
        </div>
        <div className="lp-what__item">
          <h2>Brief the next article before you write it</h2>
          <p>
            Topic, angle and outline drawn from what already ranks for the
            keyword, with the competitors you are writing against named.
          </p>
        </div>
      </section>

      <footer className="lp-foot">
        <span>Law Prep Tutorial — law &amp; judiciary entrance coaching since 2001</span>
        <span>lawpreptutorial.com</span>
      </footer>

      {authOpen && (
        <>
          <div className="lp-authlayer" onClick={() => setAuthOpen(false)} />
          <button
            type="button"
            className="lp-authclose"
            onClick={() => setAuthOpen(false)}
            aria-label="Close sign-in"
          >
            ✕
          </button>
          <LoginScreen overlay />
        </>
      )}
    </div>
  );
}
