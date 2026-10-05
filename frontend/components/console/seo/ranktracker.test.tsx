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
  };
});

afterEach(cleanup);

function doc(over: Partial<RankTrackerDoc> = {}): RankTrackerDoc {
  return {
    rows: [
      { query: "clat coaching", position: 8, url: "", checked_at: "", error: null,
        top: [{ position: 1, domain: "rival.com", url: "https://rival.com/a", title: "" }] },
      { query: "clat syllabus", position: 2, url: "", checked_at: "", error: null, top: [] },
      { query: "clat fees", position: null, url: "", checked_at: "", error: null, top: [] },
    ],
    meta: { at: "2026-10-05T09:00:00+00:00", ranked: 3, errors: 0, rivals: ["rival.com"], count: 3 },
    worklist: [{ query: "clat coaching", position: 8, impressions: 5000, leader: "rival.com",
                 leader_position: 1, leader_url: "https://rival.com/a", tracked_rival: true,
                 delta_7d: -3, score: 12.1, reason: "#8 — striking distance", dropped: false }],
    pool: { size: 3, cap: 200, built_at: "2026-10-05T06:00:00+00:00",
            sources_used: ["custom", "gsc"], notes: [] },
    budget: { date: "2026-10-05", searches: 36, cap: 3000, remaining: 2964 },
    competitors: ["rival.com"], enabled: true, job: null,
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
      raw: [],
      daily: [
        ["2026-09-29", { best: 6, worst: 9, last: 8, at: 1759190400 }],
        ["2026-09-30", { best: null, worst: null, last: null, at: 1759276800 }],
        ["2026-10-01", { best: 5, worst: 7, last: 5, at: 1759363200 }],
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
});
