/** Request discipline for the API client: deadlines and supersession.
 *
 *  Two bugs share this one root — the client had no `AbortController` anywhere:
 *
 *  1. **No request could ever time out.** `request()` passed no signal, so a
 *     backend blocked on an untimed upstream call left the promise pending for
 *     as long as the tab lived. Every `finally { setBusy(false) }` in the app
 *     is dead code in that state: the UI reads "Working…" forever, with no
 *     cancel and no error.
 *  2. **Races displayed one entity's data under another's heading.** Pick
 *     vendor A, pick vendor B before A lands, and A's response writes into
 *     state under B's name. It never throws, so nobody reports it — the user
 *     simply reads A's spend as B's.
 *
 *  Both are decisions, not I/O, so they live here and are tested without a
 *  network, a component or a real clock (see `requestPolicy.test.ts`), the same
 *  way `geo/pollLoop.ts` keeps the poll's stop rules provable.
 *
 *  Note on supersession: aborting a fetch whose response has already arrived
 *  does *nothing* — the `.then` still runs and still writes state. So the
 *  abort alone cannot fix bug 2; `RequestSequence.isCurrent()` is the part that
 *  actually blocks the stale write, and the abort is what stops us paying for
 *  the reply we no longer want.
 */

export interface RequestOptions {
  /** Caller's cancellation signal — supersession, unmount, an explicit Cancel. */
  signal?: AbortSignal;
  /** Override the deadline in ms for this one call. `0` disables it. */
  timeoutMs?: number;
}

/* -------------------------------- Deadlines ------------------------------- */

/** Database-shaped work: a Firestore read or write behind a FastAPI handler.
 *  Deliberately generous rather than snappy — the goal is "cannot hang for
 *  ever", not "fails fast". It also sits *above* the backend's own upstream
 *  timeouts on purpose: when something upstream is slow we want the server's
 *  honest error, which says what broke, rather than our own blunt one. */
export const DEFAULT_TIMEOUT_MS = 90_000;

/** Work that legitimately holds the connection open: a model call, a site
 *  crawl, a SERP sweep, a server-rendered PDF, a bulk ingest. These return only
 *  when the job is done, so this deadline is the whole job — long enough that a
 *  healthy run is never cut off, short enough that a wedged one still ends. */
export const SLOW_TIMEOUT_MS = 600_000;

/** Pass as `timeoutMs` to opt a call out of deadlines entirely. */
export const NO_TIMEOUT = 0;

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface DeadlineRule {
  pattern: RegExp;
  /** When set, the rule applies only to these methods — `POST /briefs/:id`
   *  builds a brief off live SERP data while `GET` on the same path just lists
   *  what is already stored. */
  methods?: readonly Method[];
  ms: number;
}

/** Paths that do real work before answering. Anything not listed gets
 *  `DEFAULT_TIMEOUT_MS`; adding a new long-running endpoint means adding it
 *  here, and the cost of forgetting is a premature timeout, not a hang. */
export const DEADLINE_RULES: readonly DeadlineRule[] = [
  // Graphic Designer: the 4-stage image pipeline (planning, generation, 4K).
  { pattern: /^\/api\/gd\/runs\/[^/]+\/(plan|generate|suggest|suggest-placement|text-preview|tweak|stage4)$/, ms: SLOW_TIMEOUT_MS },
  // Creative rail: brochure / carousel / PPTX rendering.
  { pattern: /^\/api\/creative\/runs\/[^/]+\/(plan|generate|autonomous)$/, ms: SLOW_TIMEOUT_MS },
  // Blog writer: deep research, drafting, visual prompts, site inventory.
  { pattern: /^\/api\/blog\/runs\/[^/]+\/(research\/step|draft|visuals|export)$/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/blog\/brands\/[^/]+\/(inventory|voice)$/, ms: SLOW_TIMEOUT_MS },
  // SEO: analysis runs, crawls, SERP-backed research.
  { pattern: /^\/api\/seo-geo\/(run|site-review)\//, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/seo-geo\/(audit|keywords)\/[^/]+\/run$/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/seo-geo\/competitors\/[^/]+\/(track|profiles\/refresh)$/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/seo-geo\/pages\/[^/]+\/refresh$/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/seo-geo\/(ask|draft-score)\//, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/seo-geo\/briefs\//, methods: ["POST"], ms: SLOW_TIMEOUT_MS },
  // GEO: each poll step is a batch of paid engine calls; reports and strategy
  // are model work.
  { pattern: /^\/api\/geo\/brands\/[^/]+\/(poll\/step|report|prompts\/generate|strategy\/generate)$/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/geo\/optimizer\/(analyze|rescore)$/, ms: SLOW_TIMEOUT_MS },
  // Marketing research: sheet/PDF ingest, snapshot capture, ask, PDF renders.
  { pattern: /^\/api\/mr\/(ingest|ingest-pdf|ingest-sheet|ask)$/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/mr\/(snapshots\/capture|workbook\/scan)$/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/mr\/reports\//, ms: SLOW_TIMEOUT_MS },
  // The board report writes no narrative, so it makes no model call — but it
  // loads the whole dataset like the campaign kinds do and then scans this
  // workspace's runs for one already keyed to the same capture. Same budget,
  // for the same reason: the cost of being wrong here is a premature timeout
  // on work the backend is still doing.
  { pattern: /^\/api\/mr\/board-report$/, methods: ["POST"], ms: SLOW_TIMEOUT_MS },
  { pattern: /\/pdf$/, ms: SLOW_TIMEOUT_MS },
  // Bulk / external-service admin work.
  { pattern: /^\/api\/ref-library\/sync-drive/, ms: SLOW_TIMEOUT_MS },
  { pattern: /^\/api\/admin\/(db\/purge-telemetry|settings\/test)$/, ms: SLOW_TIMEOUT_MS },
];

const methodOf = (method?: string): Method =>
  (method ? (method.toUpperCase() as Method) : "GET");

/** The deadline for one call, in ms. `overrideMs` always wins (including `0`,
 *  which means no deadline) so a caller that knows better than the table can
 *  say so at the call site. */
export function deadlineFor(path: string, method?: string, overrideMs?: number): number {
  if (overrideMs !== undefined) return Math.max(0, overrideMs);
  const route = path.split("?")[0];
  const verb = methodOf(method);
  for (const rule of DEADLINE_RULES) {
    if (!rule.pattern.test(route)) continue;
    if (rule.methods && !rule.methods.includes(verb)) continue;
    return rule.ms;
  }
  return DEFAULT_TIMEOUT_MS;
}

/** "90 seconds" / "10 minutes" — for a message a non-engineer has to act on.
 *  Stays in seconds up to two minutes so the number matches the wait. */
export function humanDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 120) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.round(seconds / 60);
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

/** Thrown when *our* deadline fired. Distinct from a caller-initiated abort:
 *  this one is a real failure the user must see, so it must never be swallowed
 *  by the "superseded request" branch. */
export class RequestTimeoutError extends Error {
  readonly name = "RequestTimeoutError";
  readonly timeoutMs: number;
  constructor(timeoutMs: number) {
    super(
      `The server didn't respond within ${humanDuration(timeoutMs)}, so the request was cancelled. ` +
        `It may still be finishing on the backend — wait a moment and try again.`,
    );
    this.timeoutMs = timeoutMs;
  }
}

export const isTimeoutError = (e: unknown): e is RequestTimeoutError =>
  e instanceof RequestTimeoutError;

/** True for a cancellation the app itself asked for — a superseded request, an
 *  unmount, a user Cancel. These are expected and must stay silent: they are
 *  not "loading failed" and never warrant a toast or an error panel.
 *  `DOMException`/`name` are checked structurally because the abort reason
 *  crosses realms (and jsdom-free tests) more reliably than `instanceof`. */
export function isAbortError(e: unknown): boolean {
  if (isTimeoutError(e)) return false;
  if (typeof e !== "object" || e === null) return false;
  return (e as { name?: unknown }).name === "AbortError";
}

export interface Deadline {
  /** Signal to hand to `fetch`. `undefined` when nothing can cancel this call. */
  readonly signal: AbortSignal | undefined;
  /** True once *our* timer fired (as opposed to the caller cancelling). */
  readonly expired: boolean;
  /** Release the timer and the listener. Safe to call more than once. */
  clear(): void;
}

/** Combine the caller's signal with a timer into the one signal `fetch` gets.
 *  Written by hand rather than with `AbortSignal.any`/`AbortSignal.timeout` so
 *  the two cancellation causes stay distinguishable — the UI must apologise for
 *  a timeout and say nothing at all for a supersession. */
export function createDeadline(
  path: string,
  method?: string,
  opts: RequestOptions = {},
): Deadline {
  const ms = deadlineFor(path, method, opts.timeoutMs);
  const outer = opts.signal;
  if (ms <= 0 && !outer) {
    return { signal: undefined, expired: false, clear() {} };
  }

  const controller = new AbortController();
  let expired = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const relay = () => controller.abort();

  if (outer) {
    if (outer.aborted) controller.abort();
    else outer.addEventListener("abort", relay, { once: true });
  }
  if (ms > 0 && !controller.signal.aborted) {
    timer = setTimeout(() => {
      expired = true;
      controller.abort();
    }, ms);
  }

  return {
    signal: controller.signal,
    get expired() {
      return expired;
    },
    clear() {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      outer?.removeEventListener("abort", relay);
    },
  };
}

/* ------------------------------ Supersession ------------------------------ */

export interface RequestTicket {
  readonly id: number;
  readonly signal: AbortSignal;
}

/** One in-flight slot for a view that loads different entities into the same
 *  state — a vendor dossier, a brand detail, a collection's rows.
 *
 *  `start()` supersedes whatever was in flight; `isCurrent(ticket)` is the
 *  guard every state write must pass. Both are needed: the abort stops us
 *  waiting on a reply we no longer want, and the ticket check stops a reply
 *  that had already arrived from landing under the wrong heading.
 */
export class RequestSequence {
  private id = 0;
  private inFlight: AbortController | null = null;

  /** Supersede the request in flight and take a ticket for the new one. */
  start(): RequestTicket {
    this.inFlight?.abort();
    const controller = new AbortController();
    this.inFlight = controller;
    this.id += 1;
    return { id: this.id, signal: controller.signal };
  }

  /** True only while `ticket` is still the newest attempt. Every `setState`
   *  from a response must be behind this. */
  isCurrent(ticket: RequestTicket): boolean {
    return ticket.id === this.id;
  }

  /** Cancel without starting anything — unmount, or leaving the view. Nothing
   *  is current afterwards, so late replies are dropped rather than written. */
  cancel(): void {
    this.inFlight?.abort();
    this.inFlight = null;
    this.id += 1;
  }
}
