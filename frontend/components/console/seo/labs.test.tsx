// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CompetitorsView } from "./labs";
import * as api from "@/lib/api";

// This project doesn't run Vitest with `globals: true`, so
// @testing-library/react's automatic afterEach cleanup (which relies on
// detecting a global `afterEach`) never registers — without this, each
// test's render leaks into the next one's DOM.
afterEach(cleanup);

describe("CompetitorsView custom queries", () => {
  beforeEach(() => {
    // vi.spyOn on an already-spied method reuses the same spy instance, so
    // its call history accumulates across tests unless cleared here —
    // without this, test 2's "first call" assertion sees test 1's leftover.
    vi.clearAllMocks();
    vi.spyOn(api, "seoCompetitors").mockResolvedValue({
      tracked: [], suggested: [], shifts: [], feed: {},
      custom_queries: ["existing query"], pool_size: 16, pool_cap: 50,
    });
    vi.spyOn(api, "seoCompetitorProfiles").mockResolvedValue({ profiles: null });
  });

  it("shows the pool size against the cap", async () => {
    render(<CompetitorsView brandId="b1" isCreator={true} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/16.*50/)).toBeInTheDocument());
  });

  it("shows existing custom queries and adds a new one", async () => {
    const addSpy = vi.spyOn(api, "seoAddCustomQuery").mockResolvedValue({
      custom_queries: ["existing query", "new query"], pool_size: 17,
    });
    render(<CompetitorsView brandId="b1" isCreator={true} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("existing query")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/add the exact queries you want tracked/i), { target: { value: "new query" } });
    fireEvent.click(screen.getByRole("button", { name: /add queries/i }));

    await waitFor(() => expect(addSpy).toHaveBeenCalledWith("b1", "new query"));
  });

  it("adds a whole pasted list of queries, one per line, in one click", async () => {
    const addSpy = vi.spyOn(api, "seoAddCustomQuery")
      .mockResolvedValueOnce({ custom_queries: ["existing query", "query one"], pool_size: 17 })
      .mockResolvedValueOnce({ custom_queries: ["existing query", "query one", "query two"], pool_size: 18 });
    render(<CompetitorsView brandId="b1" isCreator={true} onToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("existing query")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/add the exact queries you want tracked/i), {
      target: { value: "query one\nquery two" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add queries/i }));

    await waitFor(() => expect(addSpy).toHaveBeenCalledTimes(2));
    expect(addSpy).toHaveBeenNthCalledWith(1, "b1", "query one");
    expect(addSpy).toHaveBeenNthCalledWith(2, "b1", "query two");
  });
});
