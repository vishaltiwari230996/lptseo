/** The four things a load has to get right, proved without a component.
 *
 *  Each `describe` here is a bug this codebase actually shipped: a failed read
 *  rendering as "you have no data", a 3-minute background poll wiping good
 *  numbers, a superseded brand switch shouting at the user, and a timeout
 *  losing the one sentence written for a non-engineer. There are no component
 *  tests in this repo, so these — and `tsc` — are the whole safety net for the
 *  migration off the hand-rolled versions.
 *
 *  `fetch` is stubbed the way `api.timeout.test.ts` does it: a promise that
 *  settles only when its signal aborts, which is how a browser behaves against
 *  a wedged server. That keeps the timeout test end-to-end — a real deadline
 *  firing inside the real API client — rather than a hand-made error object
 *  that proves only that we can construct one.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { getDbCollections } from "./api";
import { DEFAULT_TIMEOUT_MS } from "./requestPolicy";
import {
  loadJob, loadPending, loadReady, LoadSession,
  type Load, type LoadToast, type Setter,
} from "./load";

/* --------------------------------- Helpers -------------------------------- */

/** A `useState` pair without React: same contract, including the updater form
 *  that `keepStale` relies on. */
function cell<T>(initial: Load<T>) {
  let value = initial;
  const set: Setter<Load<T>> = (update) => {
    value = typeof update === "function" ? (update as (prev: Load<T>) => Load<T>)(value) : update;
  };
  return { set, get: () => value };
}

/** A fetch the test settles by hand, so "still in flight" is a state we can
 *  hold a request in while another one supersedes it. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const toastSpy = () => vi.fn<LoadToast>();

/** A server that accepts the connection and then never answers. */
function stubHangingFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal;
          if (!signal) return;
          if (signal.aborted) reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          signal.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        }),
    ),
  );
}

const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/* ---------------------------------- Tests --------------------------------- */

describe("a failed refresh does not wipe what is already on screen", () => {
  it("keeps the last good data when a background refresh fails", async () => {
    const session = new LoadSession();
    const state = cell(loadReady(["january", "february"]));

    await session.run(
      "trends",
      () => Promise.reject(new Error("Sheets is down")),
      state.set,
      "Couldn't load the trends",
      { keepStale: true },
    );

    // The numbers on the board really were received. A missed poll is not a
    // reason to replace them with an error — OverviewView used to do exactly
    // that via `.catch(() => setTrends(null))`.
    expect(state.get()).toEqual(loadReady(["january", "february"]));
  });

  it("still records the failure when there is nothing on screen to protect", async () => {
    const session = new LoadSession();
    const state = cell<string[]>(loadPending);

    await session.run(
      "trends",
      () => Promise.reject(new Error("Sheets is down")),
      state.set,
      "Couldn't load the trends",
      { keepStale: true },
    );

    // Otherwise `keepStale` quietly re-creates the false empty: a screen that
    // never loaded would sit in `loading` for ever.
    expect(state.get()).toEqual({ phase: "failed", data: null, error: "Sheets is down" });
  });

  it("replaces the data when a foreground retry fails, so the user is not lied to", async () => {
    const session = new LoadSession();
    const state = cell(loadReady(["january"]));

    await session.run(
      "trends",
      () => Promise.reject(new Error("Sheets is down")),
      state.set,
      "Couldn't load the trends",
    );

    // The user asked for this one, so it answers: stale data stays visible but
    // the phase says it is stale.
    expect(state.get()).toEqual({ phase: "failed", data: ["january"], error: "Sheets is down" });
  });
});

describe("a failure is never an empty", () => {
  it("lands in `failed`, not in the empty value it started from", async () => {
    const session = new LoadSession();
    const state = cell<string[]>(loadPending);
    const toast = toastSpy();

    await session.run(
      "snapshots",
      () => Promise.reject(new Error("permission denied")),
      state.set,
      "Couldn't load the vendor snapshots",
      { toast },
    );

    const load = state.get();
    expect(load.phase).toBe("failed");
    expect(load.data).toBeNull();
    expect(load.error).toBe("permission denied");
    // `.catch(() => undefined)` left `phase` at "loading" and `data` at `[]`,
    // and the view read that as "there are no snapshots yet — take one",
    // sending the user into a second action that failed the same way.
    expect(load.phase).not.toBe("ready");
    expect(toast).toHaveBeenCalledWith("permission denied", "error");
  });

  it("falls back to the caller's words when the rejection is not an Error", async () => {
    const session = new LoadSession();
    const state = cell<string[]>(loadPending);

    await session.run("snapshots", () => Promise.reject("nope"), state.set, "Couldn't load the snapshots");

    expect(state.get().error).toBe("Couldn't load the snapshots");
  });
});

describe("a superseded load resolves in silence", () => {
  it("does not write state when a newer load has taken the slot", async () => {
    const session = new LoadSession();
    const state = cell<string>(loadPending);
    const toast = toastSpy();

    const brandA = deferred<string>();
    const brandB = deferred<string>();

    const first = session.run("brand", () => brandA.promise, state.set, "Failed to load brand", { toast });
    const second = session.run("brand", () => brandB.promise, state.set, "Failed to load brand", { toast });

    // B answers first, then A finally lands. Without the ticket, A's report
    // renders under B's name — the exact bug `RequestSequence` exists for.
    brandB.resolve("brand B report");
    await second;
    brandA.resolve("brand A report");
    await first;

    expect(state.get()).toEqual(loadReady("brand B report"));
    expect(toast).not.toHaveBeenCalled();
  });

  it("says nothing when the superseded request fails", async () => {
    const session = new LoadSession();
    const state = cell<string>(loadPending);
    const toast = toastSpy();

    const brandA = deferred<string>();
    const brandB = deferred<string>();

    const first = session.run("brand", () => brandA.promise, state.set, "Failed to load brand", { toast });
    const second = session.run("brand", () => brandB.promise, state.set, "Failed to load brand", { toast });

    brandB.resolve("brand B report");
    await second;
    // A was cancelled the moment B started. An error toast here is the console
    // apologising for something the user themselves asked for.
    brandA.reject(abortError());
    await expect(first).resolves.toBeNull();

    expect(state.get()).toEqual(loadReady("brand B report"));
    expect(toast).not.toHaveBeenCalled();
  });

  it("leaves unrelated slots alone — two panels are not each other's supersession", async () => {
    const session = new LoadSession();
    const left = cell<string>(loadPending);
    const right = cell<string>(loadPending);

    await Promise.all([
      session.run("history", () => Promise.resolve("history"), left.set, "no history"),
      session.run("portfolio", () => Promise.resolve("portfolio"), right.set, "no portfolio"),
    ]);

    expect(left.get()).toEqual(loadReady("history"));
    expect(right.get()).toEqual(loadReady("portfolio"));
  });

  it("writes nothing after the view has gone", async () => {
    const session = new LoadSession();
    const state = cell<string>(loadPending);
    const toast = toastSpy();

    const late = deferred<string>();
    const pending = session.run("brand", () => late.promise, state.set, "Failed to load brand", { toast });

    session.close(); // unmount
    late.resolve("a report nobody is looking at");
    await pending;

    expect(state.get()).toEqual(loadPending);
    expect(toast).not.toHaveBeenCalled();
  });
});

describe("a timeout keeps the sentence written for the user", () => {
  it("surfaces RequestTimeoutError's own message instead of the call site's fallback", async () => {
    vi.useFakeTimers();
    stubHangingFetch();
    const session = new LoadSession();
    const state = cell<unknown>(loadPending);
    const toast = toastSpy();

    const pending = session.run(
      "collections",
      () => getDbCollections(),
      state.set,
      "Failed to load",
      { toast },
    );

    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS);
    await pending;

    const load = state.get();
    expect(load.phase).toBe("failed");
    // The generic fallback is what 66 call sites showed instead. This says how
    // long we waited and that the job may still be finishing on the backend.
    expect(load.error).not.toBe("Failed to load");
    expect(load.error).toMatch(/90 seconds/);
    expect(load.error).toMatch(/try again/);
    expect(toast).toHaveBeenCalledWith(load.error, "error");
  });
});

describe("a batch of failures is one sentence, not five", () => {
  it("names every part that failed and gives the first reason", async () => {
    const session = new LoadSession();
    const overview = cell<string>(loadPending);
    const runs = cell<string>(loadPending);
    const snapshots = cell<string>(loadPending);
    const toast = toastSpy();

    await session.runGroup(
      "core",
      [
        loadJob("Dashboard", () => Promise.reject(new Error("backend unreachable")), overview.set, "no dashboard"),
        loadJob("Report history", () => Promise.resolve("ok"), runs.set, "no history"),
        loadJob("Vendor snapshots", () => Promise.reject(new Error("backend unreachable")), snapshots.set, "no snapshots"),
      ],
      { toast, subject: "Marketing Research" },
    );

    // Error toasts never auto-dismiss and stack only four deep, so a dead
    // backend has to produce one line the user can act on.
    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenCalledWith(
      "2 parts of Marketing Research didn't load (Dashboard, Vendor snapshots) — backend unreachable",
      "error",
    );
    expect(runs.get()).toEqual(loadReady("ok"));
    expect(snapshots.get().phase).toBe("failed");
  });

  it("uses the plain form for a single failure", async () => {
    const session = new LoadSession();
    const state = cell<string>(loadPending);
    const toast = toastSpy();

    await session.runGroup(
      "core",
      [loadJob("Dashboard", () => Promise.reject(new Error("backend unreachable")), state.set, "no dashboard")],
      { toast, subject: "Marketing Research" },
    );

    expect(toast).toHaveBeenCalledWith("Dashboard didn't load — backend unreachable", "error");
  });

  it("stays quiet on a background run but still records the failure", async () => {
    const session = new LoadSession();
    const state = cell<string>(loadPending);
    const toast = toastSpy();

    await session.runGroup(
      "core",
      [loadJob("Dashboard", () => Promise.reject(new Error("backend unreachable")), state.set, "no dashboard")],
      { toast, background: true, subject: "Marketing Research" },
    );

    // Nobody asked for this poll, so nobody gets shouted at…
    expect(toast).not.toHaveBeenCalled();
    // …but a screen with nothing on it must not go back to looking empty.
    expect(state.get()).toEqual({ phase: "failed", data: null, error: "backend unreachable" });
  });

  it("says nothing at all when everything worked", async () => {
    const session = new LoadSession();
    const state = cell<string>(loadPending);
    const toast = toastSpy();

    await session.runGroup(
      "core",
      [loadJob("Dashboard", () => Promise.resolve("ok"), state.set, "no dashboard")],
      { toast },
    );

    expect(toast).not.toHaveBeenCalled();
  });
});

describe("the low-level guard, for state that is not a Load<T>", () => {
  it("reports a real failure and stays silent about a superseded one", () => {
    const session = new LoadSession();

    const first = session.begin("brand");
    expect(first.current()).toBe(true);

    const second = session.begin("brand");
    expect(first.current()).toBe(false);
    expect(second.current()).toBe(true);

    expect(first.failure(new Error("boom"), "Failed to load brand")).toBeNull();
    expect(second.failure(new Error("boom"), "Failed to load brand")).toBe("boom");
    expect(second.failure(abortError(), "Failed to load brand")).toBeNull();
    expect(second.failure({}, "Failed to load brand")).toBe("Failed to load brand");
  });
});
