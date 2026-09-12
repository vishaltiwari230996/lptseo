import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createDeadline,
  deadlineFor,
  humanDuration,
  isAbortError,
  isTimeoutError,
  DEFAULT_TIMEOUT_MS,
  NO_TIMEOUT,
  RequestSequence,
  RequestTimeoutError,
  SLOW_TIMEOUT_MS,
} from "./requestPolicy";

afterEach(() => {
  vi.useRealTimers();
});

describe("deadlineFor", () => {
  it("gives database-shaped reads the default deadline", () => {
    expect(deadlineFor("/api/mr/snapshots/vendor/lifted?date_iso=2026-08-14")).toBe(DEFAULT_TIMEOUT_MS);
    expect(deadlineFor("/api/admin/db/collections/runs?limit=50")).toBe(DEFAULT_TIMEOUT_MS);
    expect(deadlineFor("/api/seo-geo/brands/abc")).toBe(DEFAULT_TIMEOUT_MS);
  });

  it("gives model, crawl and render work the slow deadline", () => {
    expect(deadlineFor("/api/gd/runs/r1/generate", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/seo-geo/site-review/b1", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/seo-geo/keywords/b1/run", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/seo-geo/competitors/b1/profiles/refresh", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/blog/runs/r1/research/step", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/geo/brands/b1/poll/step", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/mr/snapshots/capture", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/mr/snapshots/vendor/lifted/pdf?date_iso=2026-08-14")).toBe(SLOW_TIMEOUT_MS);
    // The board report writes no narrative and makes no model call, but it
    // loads the whole dataset and scans this workspace's runs behind it; its
    // documents render server-side.
    expect(deadlineFor("/api/mr/board-report", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/mr/board-report/run_1/pdf")).toBe(SLOW_TIMEOUT_MS);
  });

  it("separates the two methods that share the briefs path", () => {
    // POST builds a brief off live SERP data; GET just lists what is stored.
    expect(deadlineFor("/api/seo-geo/briefs/b1", "POST")).toBe(SLOW_TIMEOUT_MS);
    expect(deadlineFor("/api/seo-geo/briefs/b1", "GET")).toBe(DEFAULT_TIMEOUT_MS);
    expect(deadlineFor("/api/seo-geo/briefs/b1")).toBe(DEFAULT_TIMEOUT_MS);
  });

  it("lets a call site override the table, including opting out entirely", () => {
    expect(deadlineFor("/api/mr/overview", "GET", 5_000)).toBe(5_000);
    expect(deadlineFor("/api/gd/runs/r1/generate", "POST", NO_TIMEOUT)).toBe(0);
    expect(deadlineFor("/api/mr/overview", "GET", -1)).toBe(0);
  });

  it("matches on the route, not the query string", () => {
    expect(deadlineFor("/api/mr/lead-analysis/pdf?month=2026-08")).toBe(SLOW_TIMEOUT_MS);
  });
});

describe("humanDuration", () => {
  it("reads as something a non-engineer can act on", () => {
    expect(humanDuration(DEFAULT_TIMEOUT_MS)).toBe("90 seconds");
    expect(humanDuration(SLOW_TIMEOUT_MS)).toBe("10 minutes");
    expect(humanDuration(1_000)).toBe("1 second");
    expect(humanDuration(30_000)).toBe("30 seconds");
  });
});

describe("error classification", () => {
  it("tells a timeout apart from a cancellation", () => {
    const timeout = new RequestTimeoutError(DEFAULT_TIMEOUT_MS);
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });

    expect(isTimeoutError(timeout)).toBe(true);
    expect(isAbortError(timeout)).toBe(false); // a timeout is a real failure to show

    expect(isAbortError(abort)).toBe(true); // supersession: stay silent
    expect(isTimeoutError(abort)).toBe(false);

    expect(isAbortError(new Error("Request failed (500)"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
    expect(isAbortError("AbortError")).toBe(false);
  });

  it("says how long it waited, in the message the user sees", () => {
    expect(new RequestTimeoutError(SLOW_TIMEOUT_MS).message).toContain("10 minutes");
    expect(new RequestTimeoutError(DEFAULT_TIMEOUT_MS).message).toContain("cancelled");
  });
});

describe("createDeadline", () => {
  it("aborts once the deadline passes and reports it as expired", () => {
    vi.useFakeTimers();
    const deadline = createDeadline("/api/mr/overview", "GET");

    expect(deadline.signal?.aborted).toBe(false);
    expect(deadline.expired).toBe(false);

    vi.advanceTimersByTime(DEFAULT_TIMEOUT_MS);

    expect(deadline.signal?.aborted).toBe(true);
    expect(deadline.expired).toBe(true);
  });

  it("does not cut off work that legitimately takes minutes", () => {
    vi.useFakeTimers();
    const deadline = createDeadline("/api/gd/runs/r1/generate", "POST");

    vi.advanceTimersByTime(DEFAULT_TIMEOUT_MS * 2);
    expect(deadline.signal?.aborted).toBe(false);

    vi.advanceTimersByTime(SLOW_TIMEOUT_MS);
    expect(deadline.expired).toBe(true);
  });

  it("clear() stops the timer, so a settled request is never aborted late", () => {
    vi.useFakeTimers();
    const deadline = createDeadline("/api/mr/overview", "GET");
    deadline.clear();

    vi.advanceTimersByTime(DEFAULT_TIMEOUT_MS * 10);
    expect(deadline.signal?.aborted).toBe(false);
    expect(deadline.expired).toBe(false);
  });

  it("relays the caller's cancellation without calling it a timeout", () => {
    const outer = new AbortController();
    const deadline = createDeadline("/api/mr/overview", "GET", { signal: outer.signal });

    outer.abort();

    expect(deadline.signal?.aborted).toBe(true);
    expect(deadline.expired).toBe(false); // superseded, not slow — stay silent
  });

  it("starts already-aborted when the caller's signal is", () => {
    const outer = new AbortController();
    outer.abort();
    const deadline = createDeadline("/api/mr/overview", "GET", { signal: outer.signal });

    expect(deadline.signal?.aborted).toBe(true);
    expect(deadline.expired).toBe(false);
  });

  it("hands fetch no signal only when nothing could ever cancel the call", () => {
    expect(createDeadline("/api/mr/overview", "GET", { timeoutMs: NO_TIMEOUT }).signal).toBeUndefined();
    expect(createDeadline("/api/mr/overview", "GET").signal).toBeInstanceOf(AbortSignal);
  });
});

describe("RequestSequence", () => {
  it("aborts the previous request when a newer one starts", () => {
    const seq = new RequestSequence();
    const first = seq.start();
    const second = seq.start();

    expect(first.signal.aborted).toBe(true);
    expect(second.signal.aborted).toBe(false);
  });

  it("blocks the stale write even when the old response already arrived", () => {
    // The bug this exists for: aborting a fetch whose response has landed does
    // nothing — the `.then` still runs. Only the ticket check stops vendor A's
    // numbers rendering under vendor B's name.
    const seq = new RequestSequence();
    const vendorA = seq.start();
    const vendorB = seq.start();

    expect(seq.isCurrent(vendorA)).toBe(false);
    expect(seq.isCurrent(vendorB)).toBe(true);
  });

  it("keeps the newest ticket current across many rapid switches", () => {
    const seq = new RequestSequence();
    const tickets = [seq.start(), seq.start(), seq.start(), seq.start()];
    const newest = tickets[tickets.length - 1];

    for (const t of tickets.slice(0, -1)) {
      expect(t.signal.aborted).toBe(true);
      expect(seq.isCurrent(t)).toBe(false);
    }
    expect(seq.isCurrent(newest)).toBe(true);
    expect(newest.signal.aborted).toBe(false);
  });

  it("cancel() leaves nothing current, so a late reply is dropped", () => {
    const seq = new RequestSequence();
    const ticket = seq.start();

    seq.cancel();

    expect(ticket.signal.aborted).toBe(true);
    expect(seq.isCurrent(ticket)).toBe(false);
  });

  it("survives cancel() with nothing in flight", () => {
    const seq = new RequestSequence();
    expect(() => seq.cancel()).not.toThrow();
    const ticket = seq.start();
    expect(seq.isCurrent(ticket)).toBe(true);
  });
});
