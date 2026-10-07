/* The console's two workspaces and their left rails.
 *
 * "Daily" is the run-the-week surface: insights, traffic, keywords,
 * competitors, rank tracking, pages, tools. "Deep analysis" is everything
 * produced by auditing the site itself — the five deep-audit diagnostics
 * (which used to be tabs nested inside one "Deep audit" section), Core Web
 * Vitals, and the expert site review. The split exists because the two kinds
 * of result kept rendering on one screen and readers could not tell which
 * audit a number came from.
 *
 * Ids are a contract: the backend's priority items (priorities.py) link to
 * `#vitals`, `#keywords`, `#deep-audit` and `#traffic`, and those stored
 * documents outlive any UI rename. `resolveSection` is the single place that
 * turns an id — fresh or stale — into a workspace + section pair.
 */

import type { SidebarSection } from "./shell";

export type Workspace = "daily" | "deep";

export const WORKSPACES: { id: Workspace; label: string }[] = [
  { id: "daily", label: "Daily" },
  { id: "deep", label: "Deep analysis" },
];

export const DAILY_SECTIONS: SidebarSection[] = [
  { id: "insights", label: "Insights" },
  { id: "rank-board", label: "Rank board" },
  { id: "traffic", label: "Traffic & rankings" },
  { id: "keywords", label: "Keywords" },
  { id: "competitors", label: "Competitors" },
  { id: "rank-tracker", label: "Rank tracker" },
  { id: "pages", label: "Pages" },
  { id: "tools", label: "Tools" },
];

export const DEEP_SECTIONS: SidebarSection[] = [
  { id: "overview", label: "Overview" },
  { id: "landing", label: "Landing pages" },
  { id: "sitemap", label: "Sitemap" },
  { id: "cannibal", label: "Cannibalization" },
  { id: "density", label: "Blog keyword density" },
  { id: "speed", label: "Page speed" },
  { id: "vitals", label: "Core Web Vitals" },
  { id: "health", label: "Website health" },
];

export type DailySectionId =
  | "insights" | "rank-board" | "traffic" | "keywords" | "competitors" | "rank-tracker" | "pages" | "tools";
export type DeepSectionId =
  | "overview" | "landing" | "sitemap" | "cannibal" | "density" | "speed" | "vitals" | "health";

const DAILY_IDS = new Set(DAILY_SECTIONS.map((s) => s.id));
const DEEP_IDS = new Set(DEEP_SECTIONS.map((s) => s.id));

/** Ids that no sidebar renders any more but that persisted documents (or a
 *  user's muscle memory) may still carry. Each maps to where that content
 *  lives now. */
const LEGACY: Record<string, { workspace: Workspace; section: string }> = {
  // The old single "Deep audit" section — its summary strip is now Overview.
  "deep-audit": { workspace: "deep", section: "overview" },
};

export function resolveSection(id: string):
  | { workspace: "daily"; section: DailySectionId }
  | { workspace: "deep"; section: DeepSectionId } {
  if (DAILY_IDS.has(id)) return { workspace: "daily", section: id as DailySectionId };
  if (DEEP_IDS.has(id)) return { workspace: "deep", section: id as DeepSectionId };
  const legacy = LEGACY[id];
  if (legacy) return resolveSection(legacy.section);
  return { workspace: "daily", section: "insights" };
}
