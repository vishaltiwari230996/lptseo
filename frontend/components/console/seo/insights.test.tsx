// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { InsightsView } from "./insights";
import * as api from "@/lib/api";

// This project doesn't run Vitest with `globals: true`, so
// @testing-library/react's automatic afterEach cleanup (which relies on
// detecting a global `afterEach`) never registers — without this, each
// test's render leaks into the next one's DOM.
afterEach(cleanup);

describe("InsightsView", () => {
  beforeEach(() => {
    vi.spyOn(api, "seoPriorities").mockResolvedValue({
      priorities: {
        brand_id: "b1",
        at: "2026-09-21",
        items: [
          { id: "vitals-mobile", title: "Core Web Vitals are failing on mobile", why_it_matters: "x", severity: "critical", source: "vitals", action_link: "#vitals" },
          { id: "keyword-pool-thin", title: "Keyword pool has very few tracked keywords", why_it_matters: "y", severity: "warning", source: "keyword_pool", action_link: "#keywords" },
        ],
        notes: ["Competitors: no report yet"],
      },
    });
  });

  it("renders items sorted with critical first and shows source notes", async () => {
    const { container } = render(<InsightsView brandId="b1" onToast={vi.fn()} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/Core Web Vitals are failing on mobile/)).toBeInTheDocument());
    // Scoped to the action list, not the severity donut's own legend <li>s.
    const list = container.querySelector(".seo-insights-panel__list") as HTMLElement;
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Core Web Vitals are failing on mobile");
    expect(screen.getByText(/Competitors: no report yet/)).toBeInTheDocument();
  });

  it("shows an empty state when there are zero items", async () => {
    (api.seoPriorities as any).mockResolvedValue({ priorities: { brand_id: "b1", at: "2026-09-21", items: [], notes: [] } });
    render(<InsightsView brandId="b1" onToast={vi.fn()} onNavigate={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/nothing urgent/i)).toBeInTheDocument());
  });

  // The console is a sidebar, not one scrolling page, so an `<a href="#vitals">`
  // would do nothing. Each item navigates by section id, hash stripped.
  it("navigates to the item's section id, without the leading #", async () => {
    const onNavigate = vi.fn();
    render(<InsightsView brandId="b1" onToast={vi.fn()} onNavigate={onNavigate} />);
    await waitFor(() => expect(screen.getByText(/Core Web Vitals are failing on mobile/)).toBeInTheDocument());
    const views = screen.getAllByRole("button", { name: /View/ });
    fireEvent.click(views[0]);
    expect(onNavigate).toHaveBeenCalledWith("vitals");
    fireEvent.click(views[1]);
    expect(onNavigate).toHaveBeenLastCalledWith("keywords");
  });
});
