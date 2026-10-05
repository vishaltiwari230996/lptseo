// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { Shell } from "./shell";

// This project doesn't run Vitest with `globals: true`, so
// @testing-library/react's automatic afterEach cleanup (which relies on
// detecting a global `afterEach`) never registers — without this, each
// test's render leaks into the next one's DOM.
afterEach(cleanup);

const SECTIONS = [
  { id: "insights", label: "Insights" },
  { id: "traffic", label: "Traffic & rankings" },
];

describe("Shell", () => {
  it("renders every section label in the sidebar", () => {
    render(<Shell sections={SECTIONS} activeId="insights" onSelect={vi.fn()}>content</Shell>);
    expect(screen.getByText("Insights")).toBeInTheDocument();
    expect(screen.getByText("Traffic & rankings")).toBeInTheDocument();
  });

  it("marks the active section and calls onSelect when another is clicked", () => {
    const onSelect = vi.fn();
    render(<Shell sections={SECTIONS} activeId="insights" onSelect={onSelect}>content</Shell>);
    const activeButton = screen.getByRole("button", { name: "Insights" });
    expect(activeButton.className).toMatch(/seo-sidebar__item--active/);
    fireEvent.click(screen.getByRole("button", { name: "Traffic & rankings" }));
    expect(onSelect).toHaveBeenCalledWith("traffic");
  });

  it("renders the children as the main content area", () => {
    render(<Shell sections={SECTIONS} activeId="insights" onSelect={vi.fn()}>hello-content</Shell>);
    expect(screen.getByText("hello-content")).toBeInTheDocument();
  });

  it("offers a Rank tracker section", () => {
    render(
      <Shell
        sections={[{ id: "insights", label: "Insights" },
                   { id: "rank-tracker", label: "Rank tracker" }]}
        activeId="rank-tracker"
        onSelect={vi.fn()}
      >
        <div>panel</div>
      </Shell>,
    );
    expect(screen.getByRole("button", { name: /rank tracker/i }))
      .toHaveAttribute("aria-current", "page");
  });
});
