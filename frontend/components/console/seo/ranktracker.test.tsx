// @vitest-environment jsdom
/** The panel's job is to make 200 rows actionable. These tests pin the four
 *  things that decide whether it does: the worklist leads, the filters select,
 *  a blocked budget is visible rather than silent, and a missing Search
 *  Console connection is explained rather than shown as an empty table. Two
 *  more pin a backend-side bug fix that is easy to undo in the UI: a day we
 *  didn't rank, and a gap metric we couldn't read, must never render as 0.
 *
 *  Two corrections from the brief, verified against this repo:
 *  1. `vi.mock("@/lib/api", ...)` rather than `vi.spyOn(api, ...)` — the api
 *     module's named exports are non-extensible under Vite, so `vi.spyOn`
 *     throws at runtime.
 *  2. `fireEvent`, not `@testing-library/user-event` — that package isn't a
 *     dependency here (see every other *.test.tsx in this directory) and may
 *     not be added.
 */
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RankTrackerView } from "./ranktracker";
import type {
  RankGap, RankHistory, RankTrackerDoc,
} from "@/lib/api";

const seoRankTracker = vi.fn();
const seoRankHistory = vi.fn();
const seoRankSweep = vi.fn();
const seoRankPoolRebuild = vi.fn();
const seoRankGap = vi.fn();
const seoBuildBrief = vi.fn();
const seoAddCustomQuery = vi.fn();
const seoRemoveCustomQuery = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    seoRankTracker: (...args: unknown[]) => seoRankTracker(...args),
    seoRankHistory: (...args: unknown[]) => seoRankHistory(...args),
    seoRankSweep: (...args: unknown[]) => seoRankSweep(...args),
    seoRankPoolRebuild: (...args: unknown[]) => seoRankPoolRebuild(...args),
    seoRankGap: (...args: unknown[]) => seoRankGap(...args),
    seoBuildBrief: (...args: unknown[]) => seoBuildBrief(...args),
    seoAddCustomQuery: (...args: unknown[]) => seoAddCustomQuery(...args),
    seoRemoveCustomQuery: (...args: unknown[]) => seoRemoveCustomQuery(...args),
  };
});

/** 2026-10-02 09:00 UTC as an epoch HOUR — the unit `RankPoint.h` and
 *  `RankDaily.at` both use (seconds // 3600), not seconds. */
const HOUR_2026_10_02_09 = Math.floor(Date.UTC(2026, 9, 2, 9) / 3600_000);

afterEach(cleanup);
// These module-level vi.fn() mocks persist across every test in this file —
// without clearing, a later test's "Nth call" assertion sees an earlier
// test's leftover call history.
afterEach(() => vi.clearAllMocks());

function doc(over: Partial<RankTrackerDoc> = {}): RankTrackerDoc {
  return {
    rows: [
      // Annotated fields (impressions/delta_7d/dropped/leader*) mirror the
      // worklist entry below for the same query — `annotate_rows` on the
      // backend computes these for every row, worklist() only filters/sorts.
      { query: "clat coaching", position: 8, url: "", checked_at: "", error: null,
        top: [{ position: 1, domain: "rival.com", url: "https://rival.com/a", title: "" }],
        impressions: 5000, delta_7d: -3, dropped: false,
        leader: "rival.com", leader_position: 1, leader_url: "https://rival.com/a" },
      // Position 2, nobody above us — leader* is genuinely null (not "no
      // data"), and not on the worklist, yet still carries a real Δ7d from
      // annotate_rows — the full table must show it directly, no `top[0]`
      // fallback and no dash standing in for data that exists.
      { query: "clat syllabus", position: 2, url: "", checked_at: "", error: null, top: [],
        impressions: 800, delta_7d: 1, dropped: false,
        leader: null, leader_position: null, leader_url: null },
      // Unranked (position null) — the backend only ever reports delta_7d for
      // a currently-ranked row, so this one is null, same invariant as a real
      // `annotate_rows` row. `leader` can still be set: when we don't rank at
      // all, whoever sits at #1 is "above us" by definition.
      { query: "clat fees", position: null, url: "", checked_at: "", error: null, top: [],
        impressions: 200, delta_7d: null, dropped: false,
        leader: "other.com", leader_position: 1, leader_url: "https://other.com/x" },
    ],
    meta: { at: "2026-10-05T09:00:00+00:00", ranked: 3, errors: 0, rivals: ["rival.com"], count: 3 },
    worklist: [{ query: "clat coaching", position: 8, impressions: 5000, leader: "rival.com",
                 leader_position: 1, leader_url: "https://rival.com/a", tracked_rival: true,
                 delta_7d: -3, score: 12.1, reason: "#8 — striking distance", dropped: false }],
    pool: { size: 3, cap: 200, built_at: "2026-10-05T06:00:00+00:00",
            sources_used: ["custom", "gsc"], notes: [] },
    budget: { date: "2026-10-05", searches: 36, cap: 3000, remaining: 2964 },
    competitors: ["rival.com"], custom_queries: [], enabled: true, job: null, last_sweep: null,
    ...over,
  };
}

describe("RankTrackerView", () => {
  it("leads with the worklist and explains why the top row is there", async () => {
    seoRankTracker.mockResolvedValue(doc());
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    // The filter bar's own button is labelled "Striking distance" (required
    // by the brief's render spec, step 5) and would also match a bare
    // /striking distance/i — matching the worklist row's full reason string
    // instead pins "explains why" to the actual sentence, not just the words.
    await waitFor(() => expect(screen.getByText(/#8 — striking distance/i)).toBeInTheDocument());
    // "rival.com" also appears in the full table below (same query, same
    // leader) — scope to the worklist card so this pins the worklist's own
    // explanation, not a coincidental match further down the page.
    const worklist = screen.getByText("Fix these next").closest(".seo-rank__worklist") as HTMLElement;
    expect(within(worklist).getByText(/rival\.com/)).toBeInTheDocument();
  });

  it("filters the table down to striking-distance queries", async () => {
    seoRankTracker.mockResolvedValue(doc());
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /striking distance/i }));

    const table = screen.getByRole("table");
    expect(table).toHaveTextContent("clat coaching");
    expect(table).not.toHaveTextContent("clat syllabus"); // #2, already won
  });

  it("says the daily budget is exhausted instead of failing silently", async () => {
    seoRankTracker.mockResolvedValue(
      doc({ budget: { date: "2026-10-05", searches: 3000, cap: 3000, remaining: 0 } }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/daily search budget/i)).toBeInTheDocument());
  });

  it("explains a thin pool when Search Console is not connected", async () => {
    seoRankTracker.mockResolvedValue(
      doc({ pool: { size: 4, cap: 200, built_at: "2026-10-05T06:00:00+00:00",
                    sources_used: ["custom", "seed"],
                    notes: ["Search Console: no access"] } }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/search console/i)).toBeInTheDocument());
  });

  // Backend Critical fix: a day we ranked nowhere carries best/worst/last as
  // null. Plotting that as 0 would read as "rank 0" — the best possible
  // position, the exact opposite of the truth. The drawer must call it out
  // as a gap in the chart, never silently draw a point at the bottom.
  it("renders a null daily point as a gap, not as rank zero", async () => {
    seoRankTracker.mockResolvedValue(doc());
    const history: RankHistory = {
      query: "clat coaching",
      // A real history always has BOTH series — `daily` for days past the
      // 7-day raw window, `raw` for the days inside it. The earlier version
      // of this test seeded `daily` alone with `raw: []`, a state the
      // backend never produces, and so could not have caught the drawer
      // plotting only one of the two.
      raw: [
        { h: HOUR_2026_10_02_09, p: 5 },
        { h: HOUR_2026_10_02_09 + 2, p: 4 },
      ],
      daily: [
        { d: "2026-09-29", best: 6, worst: 9, last: 8, at: 1759190400 / 3600 },
        { d: "2026-09-30", best: null, worst: null, last: null, at: 1759276800 / 3600 },
        { d: "2026-10-01", best: 5, worst: 7, last: 5, at: 1759363200 / 3600 },
      ],
      rivals: {},
    };
    seoRankHistory.mockResolvedValue({ history });

    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "clat coaching" }));

    // The filter bar's own button is labelled "Not ranking" (step 5), so the
    // gap annotation is matched by its fuller sentence, not the bare phrase.
    await waitFor(() => expect(screen.getByText(/not ranking on:/i)).toBeInTheDocument());
    // The gap day's date must be named as not-ranking, not plotted as a value.
    expect(screen.getByText(/not ranking on:/i)).toHaveTextContent("2026-09-30");
  });

  // Backend Critical fix: a gap metric is null when the page could not be
  // read at all, distinct from a real value of 0. The UI must say so, never
  // print "0 words" for a page it never managed to fetch.
  it("renders a null gap metric as unreadable, not as zero", async () => {
    seoRankTracker.mockResolvedValue(doc());
    const gap: RankGap = {
      query: "clat coaching",
      our_url: "https://us.example.com/clat-coaching",
      our_position: 8,
      their_url: "https://rival.com/a",
      their_domain: "rival.com",
      their_position: 1,
      metrics: {
        words: { ours: null, theirs: 2400 },
        headings: { ours: null, theirs: 14 },
        schema: { ours: null, theirs: ["FAQPage"] },
        questions: { ours: null, theirs: 6 },
      },
      narrative: "We could not read our own page to compare it.",
      notes: ["Our page returned a 500 when fetched"],
      at: "2026-10-05T09:00:00+00:00",
      cached: false,
    };
    seoRankGap.mockResolvedValue({ gap });

    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/#8 — striking distance/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^why\?$/i }));

    await waitFor(() => expect(screen.getByText(/could not read our own page/i)).toBeInTheDocument());
    // "0" must never stand in for "we couldn't read it".
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/couldn.t read/i).length).toBeGreaterThan(0);
    expect(screen.getByText("2400")).toBeInTheDocument();
  });

  // Review Finding 1 (Important): loadHistory had no out-of-order guard —
  // open a slow row, then a fast one, and the slow reply used to land last
  // and silently repaint the drawer with the wrong query's chart under the
  // fast query's still-showing title.
  it("discards a stale history response for a row the drawer has moved on from", async () => {
    seoRankTracker.mockResolvedValue(doc());
    let resolveSlow!: (v: unknown) => void;
    const slow = new Promise((res) => { resolveSlow = res; });
    seoRankHistory.mockImplementation((_id: string, query: string) =>
      query === "clat coaching"
        ? slow
        : Promise.resolve({ history: { query, raw: [], daily: [], rivals: {} } }));

    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "clat coaching" })); // slow — still in flight
    fireEvent.click(screen.getByRole("button", { name: "clat fees" }));     // fast — resolves immediately

    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("clat fees"));
    await waitFor(() => expect(screen.getByText(/no history yet/i)).toBeInTheDocument());

    // The row the user left behind now resolves...
    resolveSlow({
      history: {
        query: "clat coaching", raw: [],
        daily: [{ d: "2026-09-01", best: 1, worst: 1, last: 1, at: 1 }], rivals: {},
      },
    });
    // ...and must be discarded: still "clat fees", still the empty state —
    // never silently repainted with "clat coaching"'s chart underneath it.
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("clat fees"));
    expect(screen.queryByText(/2026-09-01/)).not.toBeInTheDocument();
    expect(screen.getByText(/no history yet/i)).toBeInTheDocument();
  });

  // Review Finding 2 (Important): build_pool()/rebuild_rank_pool() never call
  // charge() — rebuilding the pool costs zero Serper credits. The budget-
  // exhausted disable belongs to Run now alone.
  it("does not disable Rebuild pool when the search budget is exhausted", async () => {
    seoRankTracker.mockResolvedValue(
      doc({ budget: { date: "2026-10-05", searches: 3000, cap: 3000, remaining: 0 } }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/daily search budget/i)).toBeInTheDocument());

    expect(screen.getByRole("button", { name: /rebuild pool/i })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: /run now/i })).toBeDisabled();
  });

  // Review Finding 3 (Important): `annotate_rows` now computes delta_7d,
  // impressions and leader* for every row, not just the worklist's top 10 —
  // the full table must read them directly, never fall back to a dash for a
  // row that simply isn't on the worklist.
  it("renders a real Δ7d value for a row that isn't on the worklist, not a dash", async () => {
    seoRankTracker.mockResolvedValue(doc());
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    const syllabusRow = screen.getByText("clat syllabus").closest("tr") as HTMLElement;
    expect(within(syllabusRow).getByText("+1")).toBeInTheDocument();
    // And the row's own Leader column — nobody is above it, which is a real
    // answer now, not a `top[0]` guess.
    expect(within(syllabusRow).getByText("—")).toBeInTheDocument();
  });

  // Review round 2, Finding (Important): `r.impressions ? ... : "—"` was a
  // truthy check, not a nullish one — a row with a real `impressions: 0`
  // (every custom/harvested/seed query `build_pool` seeds at exactly 0,
  // which is most of the ~190 rows this task newly surfaced) rendered as a
  // dash, indistinguishable from missing data. Same anti-pattern this file
  // already guards against for `RankDaily` and `RankGap`'s metrics — it
  // just reappeared here, in a third place, on this round's new field.
  it("renders a real impressions value of 0, not a dash", async () => {
    seoRankTracker.mockResolvedValue(doc({
      rows: [
        { query: "clat coaching", position: 8, url: "", checked_at: "", error: null,
          top: [{ position: 1, domain: "rival.com", url: "https://rival.com/a", title: "" }],
          impressions: 5000, delta_7d: -3, dropped: false,
          leader: "rival.com", leader_position: 1, leader_url: "https://rival.com/a" },
        // A custom/harvested/seed query build_pool seeded at impressions: 0 —
        // a genuine, informative zero, not an unknown.
        { query: "clat admission form", position: 15, url: "", checked_at: "", error: null, top: [],
          impressions: 0, delta_7d: null, dropped: false,
          leader: null, leader_position: null, leader_url: null },
      ],
    }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    const row = screen.getByText("clat admission form").closest("tr") as HTMLElement;
    expect(within(row).getByText("0")).toBeInTheDocument();   // impressions: 0 — not "—"
  });

  // Review Finding 4 (Minor): gapBusy was a single shared boolean, so loading
  // one row's gap disabled every other row's "Why?" button. briefBusy in the
  // same file is already keyed per query — this matches it.
  it("disables only the clicked row's Why? button while its gap loads", async () => {
    let resolveGap!: (v: unknown) => void;
    seoRankTracker.mockResolvedValue(doc({
      worklist: [
        { query: "clat coaching", position: 8, impressions: 5000, leader: "rival.com",
          leader_position: 1, leader_url: "https://rival.com/a", tracked_rival: true,
          delta_7d: -3, score: 12.1, reason: "#8 — striking distance", dropped: false },
        { query: "clat fees", position: null, impressions: 200, leader: "other.com",
          leader_position: 1, leader_url: "https://other.com/x", tracked_rival: false,
          delta_7d: null, score: 4, reason: "not ranking", dropped: false },
      ],
    }));
    seoRankGap.mockImplementation(() => new Promise((res) => { resolveGap = res; }));

    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/#8 — striking distance/i)).toBeInTheDocument());

    const whyButtons = screen.getAllByRole("button", { name: /^why\?$/i });
    expect(whyButtons).toHaveLength(2);
    fireEvent.click(whyButtons[0]);

    await waitFor(() => expect(whyButtons[0]).toBeDisabled());
    expect(whyButtons[1]).not.toBeDisabled();

    resolveGap({
      gap: {
        query: "clat coaching", our_url: "", our_position: 8, their_url: "https://rival.com/a",
        their_domain: "rival.com", their_position: 1,
        metrics: {
          words: { ours: 1, theirs: 1 }, headings: { ours: 1, theirs: 1 },
          schema: { ours: [], theirs: [] }, questions: { ours: 1, theirs: 1 },
        },
        narrative: "x", notes: [], at: "2026-10-05T09:00:00+00:00", cached: false,
      },
    });
    await waitFor(() => expect(whyButtons[0]).not.toBeDisabled());
  });

  // Review Finding 4 (Minor): the full table needs a real <table> with
  // <th scope="col"> on every header cell for screen-reader navigation.
  it("marks every table header cell with scope=\"col\"", async () => {
    seoRankTracker.mockResolvedValue(doc());
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    const headers = screen.getAllByRole("columnheader");
    expect(headers.length).toBeGreaterThan(0);
    headers.forEach((h) => expect(h).toHaveAttribute("scope", "col"));
  });
  // === Final whole-branch review fixes ==================================== //

  // I3 (Important): `_roll_series` only rolls points older than 7 days, so
  // `daily` is empty until day 8 and from then on always ends a week in the
  // past. The drawer plotted `daily` alone, so the chart was blank for the
  // first week and permanently missing the most recent one — the window
  // delta_7d and the dropout flag are actually computed over.
  it("plots the recent raw window, not only the week-stale daily series", async () => {
    seoRankTracker.mockResolvedValue(doc());
    const history: RankHistory = {
      query: "clat coaching",
      raw: [
        { h: HOUR_2026_10_02_09, p: 11 },
        { h: HOUR_2026_10_02_09 + 2, p: null },   // fell out this morning
      ],
      daily: [],                                   // week one: nothing rolled yet
      rivals: {},
    };
    seoRankHistory.mockResolvedValue({ history });

    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "clat coaching" }));

    // There IS a chart — the drawer used to show "No history yet" here, for
    // every query, for the tracker's entire first week.
    await waitFor(() => expect(screen.getByRole("img", { name: /rank history/i })).toBeInTheDocument());
    expect(screen.queryByText(/no history yet/i)).not.toBeInTheDocument();
    // And the raw dropout is drawn as a gap, not as a point at rank zero.
    expect(screen.getByText(/not ranking on:/i)).toHaveTextContent("2026-10-02");
  });

  it("joins the daily and raw series onto one timeline", async () => {
    seoRankTracker.mockResolvedValue(doc());
    const history: RankHistory = {
      query: "clat coaching",
      raw: [{ h: HOUR_2026_10_02_09, p: 4 }],
      daily: [{ d: "2026-09-29", best: 6, worst: 9, last: 8, at: 1759190400 / 3600 }],
      rivals: {},
    };
    seoRankHistory.mockResolvedValue({ history });

    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "clat coaching" }));

    const chart = await screen.findByRole("img", { name: /rank history/i });
    // Two points from two series, one polyline through both.
    expect(chart.querySelectorAll("circle")).toHaveLength(2);
    // And the resolution change is named rather than left for the reader to
    // infer from a line that suddenly gets denser.
    expect(screen.getByText(/daily to 2026-09-29, then every sweep/i)).toBeInTheDocument();
  });

  // I2 (Important): the panel only ever branched on status === "running", so
  // `job.error`, "failed" and "interrupted" were typed and never rendered.
  // A sweep that died showed as nothing at all — which is what would have
  // hidden the Firestore encoding bug from the owner indefinitely.
  it("reports a failed sweep with its error instead of showing nothing", async () => {
    seoRankTracker.mockResolvedValue(doc({
      job: { kind: "rank-sweep", brand_id: "b1", status: "failed", phase: "failed",
             done: 12, total: 200, started_at: "", finished_at: "",
             error: "InvalidArgument: 400 Property array contains an invalid nested entity.",
             log: [], alive: false },
    }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/last sweep failed/i)).toBeInTheDocument());
    expect(screen.getByText(/invalid nested entity/i)).toBeInTheDocument();
  });

  it("reports a sweep interrupted by a server restart", async () => {
    seoRankTracker.mockResolvedValue(doc({
      job: { kind: "rank-sweep", brand_id: "b1", status: "interrupted",
             phase: "checking 200 queries", done: 61, total: 200, started_at: "",
             finished_at: null, error: null, log: [], alive: false },
    }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/interrupted by a server restart/i)).toBeInTheDocument());
    expect(screen.getByText(/checking 200 queries/i)).toBeInTheDocument();
  });

  // I4 (Important): sweep() returns blocked: "disabled" | "credentials" and
  // writes nothing; jobs.start then marks the job done. "Run now" stayed
  // enabled and toasted "Sweep started" over a tracker doing nothing at all.
  it("disables Run now and says why when rank tracking is switched off", async () => {
    seoRankTracker.mockResolvedValue(doc({
      enabled: false,
      last_sweep: { checked: 0, ranked: 0, errors: 0, blocked: "disabled",
                    at: "2026-10-05T09:00:00+00:00",
                    notes: ["Rank tracking is switched off for this brand"] },
    }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /run now/i })).toBeDisabled());
    expect(screen.getByText(/switched off for this brand/i)).toBeInTheDocument();
  });

  it("explains a key-less sweep without disabling the button that fixes it", async () => {
    seoRankTracker.mockResolvedValue(doc({
      last_sweep: { checked: 0, ranked: 0, errors: 0, blocked: "credentials",
                    at: "2026-10-05T09:00:00+00:00",
                    notes: ["SEO_SERPER_API_KEY not set — rank tracking needs live SERPs"] },
    }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/no serper api key/i)).toBeInTheDocument());
    // `last_sweep` is a record of the PAST. Disabling Run now on it would
    // leave no way to run the first sweep after the key was added.
    expect(screen.getByRole("button", { name: /run now/i })).not.toBeDisabled();
  });

  // I1 (Important): an all-errored sweep no longer overwrites rank-latest,
  // so the panel keeps showing the previous real rankings — which makes it
  // all the more important that it says the last sweep failed.
  it("surfaces a total outage rather than rendering the stale table as all clear", async () => {
    seoRankTracker.mockResolvedValue(doc({
      last_sweep: { checked: 200, ranked: 0, errors: 200, blocked: null,
                    at: "2026-10-05T09:00:00+00:00",
                    notes: ["Every one of the 200 queries checked failed — keeping the previous results rather than overwriting them"] },
    }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/200 queries checked failed/i)).toBeInTheDocument());
  });

  it("says nothing extra when the last sweep ran cleanly", async () => {
    seoRankTracker.mockResolvedValue(doc({
      last_sweep: { checked: 200, ranked: 200, errors: 0, blocked: null,
                    at: "2026-10-05T09:00:00+00:00", notes: [] },
      job: { kind: "rank-sweep", brand_id: "b1", status: "done", phase: "done",
             done: 200, total: 200, started_at: "", finished_at: "",
             error: null, log: [], alive: false },
    }));
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    expect(screen.queryByText(/last sweep failed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/switched off/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run now/i })).not.toBeDisabled();
  });

  it("lets the user add a query to track and shows it in the list", async () => {
    seoRankTracker.mockResolvedValue(doc({ custom_queries: ["existing query"] }));
    seoAddCustomQuery.mockResolvedValue({ custom_queries: ["existing query", "new query"] });
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("existing query")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/you decide what to monitor/i), { target: { value: "new query" } });
    fireEvent.click(screen.getByRole("button", { name: /add queries/i }));

    await waitFor(() => expect(seoAddCustomQuery).toHaveBeenCalledWith("b1", "new query"));
    await waitFor(() => expect(screen.getByText("new query")).toBeInTheDocument());
  });

  it("adds a whole pasted list of queries, one per line, in one click", async () => {
    seoRankTracker.mockResolvedValue(doc({ custom_queries: [] }));
    seoAddCustomQuery
      .mockResolvedValueOnce({ custom_queries: ["query one"] })
      .mockResolvedValueOnce({ custom_queries: ["query one", "query two"] });
    render(<RankTrackerView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/you decide what to monitor/i), {
      target: { value: "query one\nquery two" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add queries/i }));

    await waitFor(() => expect(seoAddCustomQuery).toHaveBeenCalledTimes(2));
    expect(seoAddCustomQuery).toHaveBeenNthCalledWith(1, "b1", "query one");
    expect(seoAddCustomQuery).toHaveBeenNthCalledWith(2, "b1", "query two");
  });
});
