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

    fireEvent.change(screen.getByPlaceholderText(/add a query/i), { target: { value: "new query" } });
    fireEvent.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() => expect(addSpy).toHaveBeenCalledWith("b1", "new query"));
  });
});
