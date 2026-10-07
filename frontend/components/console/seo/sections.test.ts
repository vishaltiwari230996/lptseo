import { describe, expect, it } from "vitest";
import { DAILY_SECTIONS, DEEP_SECTIONS, resolveSection } from "./sections";

describe("workspace section lists", () => {
  it("keeps the day-to-day surfaces in Daily", () => {
    const ids = DAILY_SECTIONS.map((s) => s.id);
    expect(ids).toEqual(["insights", "rank-board", "traffic", "keywords", "competitors", "rank-tracker", "pages", "tools"]);
  });

  it("gives Deep analysis one entry per diagnostic, plus vitals and the expert review", () => {
    const ids = DEEP_SECTIONS.map((s) => s.id);
    expect(ids).toEqual(["overview", "landing", "sitemap", "cannibal", "density", "speed", "vitals", "health"]);
  });

  it("never reuses an id across the two workspaces", () => {
    const daily = new Set(DAILY_SECTIONS.map((s) => s.id));
    for (const s of DEEP_SECTIONS) expect(daily.has(s.id)).toBe(false);
  });
});

describe("resolveSection", () => {
  // The backend's priorities.py writes action_link as "#vitals", "#keywords",
  // "#deep-audit" and "#traffic"; InsightsView strips the "#" before calling
  // onNavigate, so these four bare ids are a contract with persisted data.
  it("routes #vitals into the Deep workspace", () => {
    expect(resolveSection("vitals")).toEqual({ workspace: "deep", section: "vitals" });
  });

  it("routes the legacy #deep-audit link to the Deep overview", () => {
    expect(resolveSection("deep-audit")).toEqual({ workspace: "deep", section: "overview" });
  });

  it("routes #traffic and #keywords into Daily", () => {
    expect(resolveSection("traffic")).toEqual({ workspace: "daily", section: "traffic" });
    expect(resolveSection("keywords")).toEqual({ workspace: "daily", section: "keywords" });
  });

  it("sends the expert review (old 'health' section) to Deep", () => {
    expect(resolveSection("health")).toEqual({ workspace: "deep", section: "health" });
  });

  it("falls back to Daily insights for anything unknown or stale", () => {
    expect(resolveSection("no-such-id")).toEqual({ workspace: "daily", section: "insights" });
    expect(resolveSection("")).toEqual({ workspace: "daily", section: "insights" });
  });

  it("resolves every listed section to its own workspace", () => {
    for (const s of DAILY_SECTIONS) expect(resolveSection(s.id)).toEqual({ workspace: "daily", section: s.id });
    for (const s of DEEP_SECTIONS) expect(resolveSection(s.id)).toEqual({ workspace: "deep", section: s.id });
  });
});
