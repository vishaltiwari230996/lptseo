// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InsightsView } from "./insights";
import * as api from "@/lib/api";

// This project doesn't run Vitest with `globals: true`, so
// @testing-library/react's automatic afterEach cleanup (which relies on
// detecting a global `afterEach`) never registers — without this, each
// test's render leaks into the next one's DOM.
afterEach(cleanup);

const BRIEF: api.SeoBriefDoc = {
  brand_id: "b1",
  at: "2026-10-07",
  current_rank: {
    at: "2026-10-07T06:00:00", tracked: 12, top3: 3, page1: 7, striking: 4,
    unranked: 2, moved_up: 1, moved_down: 2, dropouts: 1,
    best: [{ query: "judiciary prep", position: 2 }],
  },
  not_working: [
    { text: "'clat coaching' fell #1→#4 — rival.com is above at #1.", link: "rank-board" },
    { text: "Core Web Vitals are failing on mobile.", link: "vitals" },
  ],
  working: [{ text: "Organic clicks grew 1,000 → 1,200 over 28 days.", link: "traffic" }],
  immediate: [{ text: "Rewrite the title for 'clat 2027' (≈+90 clicks/mo).", link: "traffic" }],
  secondary: [{ text: "Refresh the CLAT syllabus page (≈+40 clicks/mo).", link: "traffic" }],
  notes: ["Deep audit: not run yet — the technical blocks are blind until it runs"],
};

describe("InsightsView (daily brief)", () => {
  beforeEach(() => {
    vi.spyOn(api, "seoBrief").mockResolvedValue({ brief: BRIEF });
  });

  it("renders all five blocks in reading order", async () => {
    render(<InsightsView brandId="b1" onToast={vi.fn()} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Current rank")).toBeInTheDocument());
    const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(titles).toEqual([
      "Current rank", "What is not working", "What is working", "Immediate fixes", "Secondary fixes",
    ]);
    expect(screen.getByText(/in the top 3/)).toBeInTheDocument();
    expect(screen.getByText(/'clat coaching' fell #1→#4/)).toBeInTheDocument();
    expect(screen.getByText(/Organic clicks grew/)).toBeInTheDocument();
    expect(screen.getByText(/Deep audit: not run yet/)).toBeInTheDocument();
  });

  it("navigates each line to its owning section, and the rank card to the board", async () => {
    const onNavigate = vi.fn();
    render(<InsightsView brandId="b1" onToast={vi.fn()} onNavigate={onNavigate} />);
    await waitFor(() => expect(screen.getByText("Current rank")).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("button", { name: "View" })[0]);
    expect(onNavigate).toHaveBeenCalledWith("rank-board");
    fireEvent.click(screen.getByRole("button", { name: /open the rank board/i }));
    expect(onNavigate).toHaveBeenLastCalledWith("rank-board");
  });

  it("is honest when rank tracking has never run", async () => {
    (api.seoBrief as any).mockResolvedValue({
      brief: { ...BRIEF, current_rank: null, not_working: [], working: [], immediate: [], secondary: [] },
    });
    render(<InsightsView brandId="b1" onToast={vi.fn()} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/no rank data yet/i)).toBeInTheDocument());
    expect(screen.getByText(/nothing broken/i)).toBeInTheDocument();
  });
});
