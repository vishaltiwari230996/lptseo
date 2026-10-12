/* Bucketing for the traffic charts: GSC's daily rows → weekly / monthly
 * totals with period-over-period deltas. Pure functions, UTC date math only —
 * charts must not drift by a day depending on the viewer's timezone.
 *
 * Honesty rules the charts rely on:
 * - Weekly buckets are COMPLETE Mon-Sun weeks only. A half-week bar reads as
 *   a crash, not as "the week isn't over".
 * - The current month IS shown (the owner asks "is mahine kaisa chal raha
 *   hai"), but flagged `partial` so the label can say so.
 */

export interface SeoDailyPoint {
  date: string; // YYYY-MM-DD
  clicks: number;
  impressions: number;
  ctr: number;
  position: number;
}

export interface SeriesBucket {
  /** Short axis label ("6 Oct", "Sep"). */
  label: string;
  /** Full label for tooltips ("Week of 6–12 Oct", "September 2026"). */
  full: string;
  clicks: number;
  impressions: number;
  /** % change vs the previous bucket, null for the first (or vs a partial). */
  delta_clicks: number | null;
  delta_impressions: number | null;
  partial: boolean;
}

const DAY = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const utc = (iso: string) => new Date(`${iso}T00:00:00Z`);

/** The Monday starting this date's ISO week, as a UTC ms timestamp. */
function mondayOf(iso: string): number {
  const d = utc(iso);
  const dow = (d.getUTCDay() + 6) % 7; // Mon=0 … Sun=6
  return d.getTime() - dow * DAY;
}

function pct(now: number, prev: number): number | null {
  if (!prev) return null;
  return Math.round(((now - prev) / prev) * 100);
}

function attachDeltas(buckets: SeriesBucket[]): SeriesBucket[] {
  return buckets.map((b, i) => {
    const prev = i > 0 ? buckets[i - 1] : null;
    const comparable = prev && !prev.partial && !b.partial;
    return {
      ...b,
      delta_clicks: comparable ? pct(b.clicks, prev.clicks) : null,
      delta_impressions: comparable ? pct(b.impressions, prev.impressions) : null,
    };
  });
}

/** Complete Mon-Sun weeks, oldest first, capped to the trailing `max`. */
export function weeklyBuckets(series: SeoDailyPoint[], max = 8): SeriesBucket[] {
  if (!series.length) return [];
  const byWeek = new Map<number, { clicks: number; impressions: number; days: number }>();
  for (const p of series) {
    const key = mondayOf(p.date);
    const slot = byWeek.get(key) ?? { clicks: 0, impressions: 0, days: 0 };
    slot.clicks += p.clicks;
    slot.impressions += p.impressions;
    slot.days += 1;
    byWeek.set(key, slot);
  }
  const buckets = [...byWeek.entries()]
    .filter(([, v]) => v.days === 7) // complete weeks only
    .sort(([a], [b]) => a - b)
    .map(([monday, v]): SeriesBucket => {
      const start = new Date(monday);
      const end = new Date(monday + 6 * DAY);
      const label = `${start.getUTCDate()} ${MONTHS[start.getUTCMonth()]}`;
      const endLabel = `${end.getUTCDate()} ${MONTHS[end.getUTCMonth()]}`;
      return {
        label,
        full: `Week of ${start.getUTCDate()} ${MONTHS[start.getUTCMonth()]} – ${endLabel}`,
        clicks: v.clicks, impressions: v.impressions,
        delta_clicks: null, delta_impressions: null, partial: false,
      };
    });
  return attachDeltas(buckets.slice(-max));
}

/** Calendar months, oldest first; the month still in progress is `partial`.
 * `today` exists for tests — defaults to the series' own last day, so the
 * chart never depends on the viewer's clock. */
export function monthlyBuckets(series: SeoDailyPoint[], max = 4, today?: string): SeriesBucket[] {
  if (!series.length) return [];
  const last = today ?? series[series.length - 1].date;
  const byMonth = new Map<string, { clicks: number; impressions: number }>();
  for (const p of series) {
    const key = p.date.slice(0, 7); // YYYY-MM
    const slot = byMonth.get(key) ?? { clicks: 0, impressions: 0 };
    slot.clicks += p.clicks;
    slot.impressions += p.impressions;
    byMonth.set(key, slot);
  }
  const currentKey = last.slice(0, 7);
  const buckets = [...byMonth.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([key, v]): SeriesBucket => {
      const [y, m] = key.split("-").map(Number);
      const partial = key === currentKey;
      return {
        label: MONTHS[m - 1] + (partial ? "*" : ""),
        full: `${MONTHS[m - 1]} ${y}${partial ? " (month so far)" : ""}`,
        clicks: v.clicks, impressions: v.impressions,
        delta_clicks: null, delta_impressions: null, partial,
      };
    });
  return attachDeltas(buckets.slice(-max));
}

/** The daily-compare pair the trend chart draws: the trailing `days` window
 * and the window of the same length before it, index-aligned. */
export function compareWindows(series: SeoDailyPoint[], days = 28): {
  current: SeoDailyPoint[]; previous: SeoDailyPoint[];
} {
  const current = series.slice(-days);
  const previous = series.slice(-(days * 2), -days);
  return { current, previous };
}
