import { describe, expect, it } from "vitest";
import { compareWindows, monthlyBuckets, weeklyBuckets, type SeoDailyPoint } from "./series";

/** Daily points from `start` for `n` days; clicks ramp by day index. */
function days(start: string, n: number, clicksAt: (i: number) => number): SeoDailyPoint[] {
  const base = new Date(`${start}T00:00:00Z`).getTime();
  return Array.from({ length: n }, (_, i) => ({
    date: new Date(base + i * 86_400_000).toISOString().slice(0, 10),
    clicks: clicksAt(i),
    impressions: clicksAt(i) * 20,
    ctr: 0.05,
    position: 8,
  }));
}

describe("weeklyBuckets", () => {
  // 2026-09-07 is a Monday. 3 complete weeks + 3 stray days.
  const series = days("2026-09-07", 24, (i) => 10 + Math.floor(i / 7) * 10);

  it("keeps only complete Mon-Sun weeks", () => {
    const buckets = weeklyBuckets(series);
    expect(buckets).toHaveLength(3);
    expect(buckets[0].full).toMatch(/Week of 7 Sep/);
    expect(buckets.every((b) => !b.partial)).toBe(true);
  });

  it("sums the week and computes week-on-week deltas", () => {
    const buckets = weeklyBuckets(series);
    expect(buckets[0].clicks).toBe(70);        // 7 × 10
    expect(buckets[1].clicks).toBe(140);       // 7 × 20
    expect(buckets[0].delta_clicks).toBeNull(); // nothing before it
    expect(buckets[1].delta_clicks).toBe(100); // doubled
    expect(buckets[2].delta_clicks).toBe(50);  // 140 → 210
  });

  it("caps to the trailing max", () => {
    const long = days("2026-06-01", 7 * 14, () => 10); // 14 weeks (Mon start)
    expect(weeklyBuckets(long, 8)).toHaveLength(8);
  });
});

describe("monthlyBuckets", () => {
  const series = [
    ...days("2026-08-01", 31, () => 10),  // Aug: 310
    ...days("2026-09-01", 30, () => 20),  // Sep: 600
    ...days("2026-10-01", 7, () => 30),   // Oct so far: 210
  ];

  it("marks the month in progress as partial and labels it", () => {
    const buckets = monthlyBuckets(series, 4, "2026-10-07");
    expect(buckets.map((b) => b.label)).toEqual(["Aug", "Sep", "Oct*"]);
    expect(buckets[2].partial).toBe(true);
    expect(buckets[2].full).toMatch(/month so far/);
  });

  it("computes month-on-month deltas between complete months only", () => {
    const buckets = monthlyBuckets(series, 4, "2026-10-07");
    expect(buckets[1].delta_clicks).toBe(94);   // 310 → 600
    expect(buckets[2].delta_clicks).toBeNull(); // partial month: no honest delta
  });
});

describe("compareWindows", () => {
  it("splits the tail into two aligned windows", () => {
    const series = days("2026-08-01", 60, (i) => i);
    const { current, previous } = compareWindows(series, 28);
    expect(current).toHaveLength(28);
    expect(previous).toHaveLength(28);
    expect(previous[previous.length - 1].date < current[0].date).toBe(true);
  });
});
