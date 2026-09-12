/** Fetch-state for a view: the phase, the mount guard, and supersession.
 *
 *  Every screen in this console loads something, and until this module each one
 *  re-derived the same three decisions by hand — badly, and differently:
 *
 *  1. **The mount guard.** Spelled four ways across the tree (`let cancelled`,
 *     `let alive`, `let live`, `mounted = useRef(true)`) and simply omitted in
 *     two places, where a resolved fetch wrote state into an unmounted tree.
 *  2. **The failure phase.** `.catch(() => undefined)` leaves state at the
 *     empty value it started from, so the view renders "you have no data" for
 *     what was actually "we never found out" — and invites the user into a
 *     second action that fails identically. `failed` is a third answer that
 *     neither an empty array nor `null` can express.
 *  3. **The abort/timeout distinction.** `lib/requestPolicy.ts` goes to real
 *     trouble to keep "the user superseded this" apart from "our deadline
 *     fired", and then 66 call sites threw the distinction away with
 *     `e instanceof Error ? e.message : "Failed to load"`. A switched brand
 *     showed an error toast the policy was explicitly written not to show, and
 *     a timeout lost the sentence `RequestTimeoutError` wrote for a
 *     non-engineer in favour of whatever fallback string that call site
 *     invented.
 *
 *  All three are decisions rather than I/O, so they live here as pure code with
 *  no React and no DOM, provable in `load.test.ts` the same way
 *  `requestPolicy.ts` and `geo/pollLoop.ts` keep their rules provable. The one
 *  React-facing export is `useLoadSession` at the bottom, which does nothing
 *  but tie a session's lifetime to a component's.
 */

import { isAbortError, isTimeoutError, RequestSequence } from "./requestPolicy";
import { useEffect, useRef } from "react";

/* --------------------------------- State ---------------------------------- */

export type LoadPhase = "loading" | "ready" | "failed";

export interface Load<T> {
  readonly phase: LoadPhase;
  /** The last value actually received. Survives a later failure so a working
   *  screen does not blank out when a refresh fails. */
  readonly data: T | null;
  /** Why the last attempt failed. Non-null only while `phase === "failed"`. */
  readonly error: string | null;
}

/** Shared because it is immutable — every load starts here. */
export const loadPending: Load<never> = { phase: "loading", data: null, error: null };

export const loadReady = <T,>(data: T): Load<T> => ({ phase: "ready", data, error: null });

/** The tone argument matches `ToastFn` structurally, so a component's `onToast`
 *  is accepted here without this module importing anything from `components/`
 *  (which would drag JSX back in — the reason this module had to move out of
 *  `mr/MarketingResearch.tsx`). */
export type LoadToast = (message: string, tone?: "ok" | "warn" | "error") => void;

/** A `useState` setter, structurally. Same reason: no React types in the
 *  decision layer. */
export type Setter<T> = (update: T | ((prev: T) => T)) => void;

/** The message a user sees. `fallback` covers a rejection that is not an
 *  `Error` (or an `Error` with an empty message) — never show them "[object
 *  Object]" or a blank line where the reason should be. */
export const loadMessage = (e: unknown, fallback: string): string =>
  e instanceof Error && e.message.trim() ? e.message : fallback;

/** What to say about a failure that is worth saying anything about.
 *
 *  A timeout keeps `RequestTimeoutError`'s own words — it names the wait, says
 *  the job may still be finishing on the backend, and tells the user to try
 *  again. Every hand-rolled site replaced that with "Failed to load". */
export const describeFailure = (e: unknown, fallback: string): string =>
  isTimeoutError(e) ? e.message : loadMessage(e, fallback);

/** `keepStale` is for unattended refreshes only: numbers already on screen
 *  really were received, so a missed 3-minute poll should leave them alone
 *  rather than replace true data with an error. With nothing on screen there is
 *  nothing to protect and the failure must show, or the poll silently restores
 *  the false-empty this whole module exists to remove. */
export function loadFailed<T>(
  prev: Load<T>,
  message: string,
  opts: { keepStale?: boolean } = {},
): Load<T> {
  if (opts.keepStale && prev.data !== null) return prev;
  return { phase: "failed", data: prev.data, error: message };
}

/** The failed loads out of a set, in the order given, ready to render. */
export function failuresOf(
  entries: readonly { label: string; load: Load<unknown> }[],
): { label: string; error: string }[] {
  return entries
    .filter((e) => e.load.phase === "failed")
    .map((e) => ({ label: e.label, error: e.load.error ?? "the load failed" }));
}

/** One line for a batch of failures, or null when nothing failed. Error toasts
 *  never auto-dismiss and stack only four deep, so a dead backend must produce
 *  one sentence the user can act on instead of five that bury each other. */
export function loadFailureToast(
  failures: readonly { label: string; message: string }[],
  subject = "this screen",
): string | null {
  const first = failures[0];
  if (!first) return null;
  if (failures.length === 1) return `${first.label} didn't load — ${first.message}`;
  const labels = failures.map((f) => f.label).join(", ");
  return `${failures.length} parts of ${subject} didn't load (${labels}) — ${first.message}`;
}

/** How often an open console silently re-reads a live board. Matches the cloud
 *  pull's own cadence, so the screen tracks the sheet without ever being ahead
 *  of it. Was copied into three files with three different disciplines — one of
 *  which wiped good numbers on a failed poll. Background refreshes pass
 *  `keepStale`; that pairing is the whole discipline and it lives here. */
export const LIVE_REFRESH_MS = 180_000;

/* -------------------------------- Attempts -------------------------------- */

/** One try at filling one slot. Handed to the caller so that the two questions
 *  every load has to answer — "may I still write?" and "is this worth saying
 *  out loud?" — are answered by the session rather than re-invented. */
export interface LoadAttempt {
  /** Pass to any API function that accepts `RequestOptions`, so a superseded
   *  request stops costing us a reply we will discard. Not every function does;
   *  `current()` is what actually blocks the stale write, per the note in
   *  `requestPolicy.ts`. */
  readonly signal: AbortSignal;
  /** True only while this is still the newest attempt for its slot and the
   *  session is still mounted. Every state write goes behind this. */
  current(): boolean;
  /** The message to show the user, or `null` when the right thing to say is
   *  nothing: the attempt was superseded, the view unmounted, or the caller
   *  aborted. A timeout is never silent — it is a real failure with its own
   *  human-readable sentence. */
  failure(e: unknown, fallback: string): string | null;
}

/** A load in a batch. Built by `loadJob` so the element type stays concrete —
 *  a group holds loads of different shapes and each one closes over its own. */
export interface LoadJob {
  /** How this load is named to the user when it fails. */
  readonly label: string;
  readonly run: (attempt: LoadAttempt, keepStale: boolean) => Promise<string | null>;
}

/** Fetch, guard, and record — the body every one of these loads shares.
 *  Resolves to the failure message, or null when there is nothing to announce
 *  (success, or a silence the classifier asked for). */
function execute<T>(
  attempt: LoadAttempt,
  fetch: (signal: AbortSignal) => Promise<T>,
  set: Setter<Load<T>>,
  fallback: string,
  keepStale: boolean,
): Promise<string | null> {
  return fetch(attempt.signal).then(
    (value) => {
      if (!attempt.current()) return null;
      set(loadReady(value));
      return null;
    },
    (e: unknown) => {
      const message = attempt.failure(e, fallback);
      if (message === null) return null;
      set((prev) => loadFailed(prev, message, { keepStale }));
      return message;
    },
  );
}

export function loadJob<T>(
  label: string,
  fetch: (signal: AbortSignal) => Promise<T>,
  set: Setter<Load<T>>,
  fallback: string,
): LoadJob {
  return { label, run: (attempt, keepStale) => execute(attempt, fetch, set, fallback, keepStale) };
}

/* --------------------------------- Session -------------------------------- */

/** The loads of one view, for as long as that view is on screen.
 *
 *  A **slot** is a destination: one piece of state, or one group of state a
 *  single user action fills together. Loads in different slots run
 *  concurrently; a second load into the *same* slot supersedes the first, which
 *  is exactly right for a brand switch and exactly wrong for two unrelated
 *  panels. Naming the slot is the only thing the caller has to get right —
 *  everything downstream of it (the ticket, the guard, the classification, the
 *  silence) follows from it.
 *
 *  `close()` on unmount makes every outstanding ticket stale at once, so the
 *  mount guard is not a flag anyone can forget to check: a resolved fetch
 *  simply has nowhere to write.
 */
export class LoadSession {
  private slots = new Map<string, RequestSequence>();

  private sequence(slot: string): RequestSequence {
    let seq = this.slots.get(slot);
    if (!seq) {
      seq = new RequestSequence();
      this.slots.set(slot, seq);
    }
    return seq;
  }

  /** Take the slot, superseding whatever was filling it. For state that is not
   *  a `Load<T>` — a plain value, a busy flag, a mutation's reply — where the
   *  guard is still needed but the phase machinery is not. */
  begin(slot: string): LoadAttempt {
    const seq = this.sequence(slot);
    const ticket = seq.start();
    const current = () => this.slots.get(slot) === seq && seq.isCurrent(ticket);
    return {
      signal: ticket.signal,
      current,
      failure: (e, fallback) => {
        // A supersession, an unmount, or an explicit cancel. Expected, asked
        // for, and never the user's problem — say nothing at all.
        if (!current() || isAbortError(e)) return null;
        return describeFailure(e, fallback);
      },
    };
  }

  /** One load into one `Load<T>` state. Resolves to the failure message, or
   *  null, so a caller running several can announce the batch instead of each
   *  call — which is what `runGroup` does. */
  async run<T>(
    slot: string,
    fetch: (signal: AbortSignal) => Promise<T>,
    set: Setter<Load<T>>,
    fallback: string,
    opts: { keepStale?: boolean; toast?: LoadToast } = {},
  ): Promise<string | null> {
    const message = await execute(
      this.begin(slot), fetch, set, fallback, opts.keepStale === true,
    );
    if (message) opts.toast?.(message, "error");
    return message;
  }

  /** Run a batch together, under one ticket, and report its failures once.
   *
   *  A background run stays silent — the user did not ask for it and the next
   *  attempt is minutes away — but it still records the failure in state, so a
   *  screen with nothing on it says so instead of reverting to a false empty.
   */
  async runGroup(
    slot: string,
    jobs: readonly LoadJob[],
    opts: { background?: boolean; toast?: LoadToast; subject?: string } = {},
  ): Promise<void> {
    const attempt = this.begin(slot);
    const background = opts.background === true;
    const results = await Promise.all(jobs.map((j) => j.run(attempt, background)));
    if (background) return;
    const failures: { label: string; message: string }[] = [];
    results.forEach((message, i) => {
      if (message) failures.push({ label: jobs[i].label, message });
    });
    const line = loadFailureToast(failures, opts.subject);
    if (line) opts.toast?.(line, "error");
  }

  /** Leaving the view. Aborts what is in flight and makes every ticket already
   *  out stale, so nothing that lands afterwards can write state or toast.
   *  Reusable: a StrictMode remount re-runs the effect and the next `begin`
   *  starts clean. */
  close(): void {
    for (const seq of this.slots.values()) seq.cancel();
    this.slots.clear();
  }
}

/** A session for the lifetime of this component. The cleanup is the mount
 *  guard, so no component needs a `cancelled` flag, and there is no fourth
 *  spelling of one to invent. */
export function useLoadSession(): LoadSession {
  const ref = useRef<LoadSession | null>(null);
  if (ref.current === null) ref.current = new LoadSession();
  const session = ref.current;
  useEffect(() => () => session.close(), [session]);
  return session;
}
