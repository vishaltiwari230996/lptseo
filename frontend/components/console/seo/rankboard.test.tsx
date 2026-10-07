// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RankBoardView } from "./rankboard";
import * as api from "@/lib/api";

afterEach(cleanup);

const BOARD: api.SeoRankBoardDoc = {
  rows: [
    {
      query: "clat coaching", position: 4, url: "https://lawpreptutorial.com/clat",
      delta_7d: -2, dropped: false, impressions: 900, checked_at: "2026-10-07T06:00:00",
      above: [
        { position: 1, domain: "rival.com", url: "https://rival.com/a", title: "Rival CLAT" },
        { position: 2, domain: "other.com", url: "https://other.com/b", title: "Other" },
      ],
    },
    {
      query: "judiciary prep", position: 1, url: "https://lawpreptutorial.com/jud",
      delta_7d: 0, dropped: false, impressions: 500, checked_at: "2026-10-07T06:00:00",
      above: [],
    },
  ],
  pending: ["new expert query"],
  custom_queries: ["clat coaching", "judiciary prep", "new expert query"],
  last_sweep: { at: "2026-10-07T06:00:00", checked: 3, ranked: 2, errors: 0, blocked: null, notes: [] },
};

describe("RankBoardView", () => {
  beforeEach(() => {
    vi.spyOn(api, "seoRankBoard").mockResolvedValue(BOARD);
  });

  it("shows each expert query with our rank and the best competitor above us", async () => {
    render(<RankBoardView brandId="b1" isCreator={false} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("clat coaching")).toBeInTheDocument());
    expect(screen.getByText("#4")).toBeInTheDocument();
    expect(screen.getByText("rival.com")).toBeInTheDocument();
    expect(screen.getByText("+1 more above us")).toBeInTheDocument();
    expect(screen.getByText(/nobody — we lead/)).toBeInTheDocument();
    expect(screen.getByText(/waiting for the next sweep/i)).toBeInTheDocument();
  });

  it("expands a row to the full list of everyone above us", async () => {
    render(<RankBoardView brandId="b1" isCreator={false} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("clat coaching")).toBeInTheDocument());
    fireEvent.click(screen.getByText("clat coaching"));
    expect(screen.getByText("Rival CLAT")).toBeInTheDocument();
    expect(screen.getByText("other.com")).toBeInTheDocument();
    // Read-only users get no remove button.
    expect(screen.queryByRole("button", { name: /stop tracking/i })).not.toBeInTheDocument();
  });

  it("lets a creator add a query", async () => {
    const add = vi.spyOn(api, "seoAddCustomQuery").mockResolvedValue({ custom_queries: [], pool_size: 1 } as any);
    render(<RankBoardView brandId="b1" isCreator onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("clat coaching")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("New expert query"), { target: { value: "best clat books" } });
    fireEvent.click(screen.getByRole("button", { name: /track it/i }));
    await waitFor(() => expect(add).toHaveBeenCalledWith("b1", "best clat books"));
  });

  it("hides the add form from non-creators", async () => {
    render(<RankBoardView brandId="b1" isCreator={false} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("clat coaching")).toBeInTheDocument());
    expect(screen.queryByLabelText("New expert query")).not.toBeInTheDocument();
  });
});
