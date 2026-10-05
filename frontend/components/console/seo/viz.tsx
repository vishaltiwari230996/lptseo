"use client";

/* Hand-rolled SVG dials and rings — the SEO console already draws its own
 * Core Web Vitals bar instead of pulling in a charting library, so these
 * follow the same convention: no new dependency, full control over the
 * zero/empty states that a generic chart library gets wrong by default.
 */

const TAU = 2 * Math.PI;

/* ------------------------------- radial gauge ------------------------------ */

export interface RadialGaugeProps {
  /** 0-100. Values outside the range are clamped, never drawn off-ring. */
  value: number | null;
  /** Big text in the center. Defaults to `${value}`. */
  display?: string;
  label: string;
  size?: number;
  strokeWidth?: number;
  /** "auto" colors the ring from `goodAt`/`warnAt` thresholds (health-style
   *  scores, where higher is better). "neutral" always draws brand-colored —
   *  for stats like visibility % that aren't a pass/fail judgment. */
  tone?: "auto" | "neutral";
  goodAt?: number;
  warnAt?: number;
}

export function RadialGauge({
  value, display, label, size = 72, strokeWidth = 7, tone = "auto", goodAt = 85, warnAt = 60,
}: RadialGaugeProps) {
  const known = value != null && !Number.isNaN(value);
  const pct = known ? Math.max(0, Math.min(100, value as number)) : 0;
  const r = (size - strokeWidth) / 2;
  const c = size / 2;
  const circumference = TAU * r;
  const dash = (pct / 100) * circumference;

  const colorClass = !known
    ? "seo-gauge--none"
    : tone === "neutral"
    ? "seo-gauge--neutral"
    : pct >= goodAt ? "seo-gauge--good" : pct >= warnAt ? "seo-gauge--warn" : "seo-gauge--bad";

  return (
    <div className="seo-gauge" role="img" aria-label={`${label}: ${known ? display ?? pct : "no data"}`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={c} cy={c} r={r} className="seo-gauge__track" strokeWidth={strokeWidth} fill="none" />
        {known && (
          <circle
            cx={c} cy={c} r={r} className={`seo-gauge__arc ${colorClass}`} strokeWidth={strokeWidth} fill="none"
            strokeDasharray={`${dash} ${circumference - dash}`}
            strokeLinecap="round"
            transform={`rotate(-90 ${c} ${c})`}
          />
        )}
      </svg>
      <div className="seo-gauge__center">
        <span className={`seo-gauge__value ${colorClass}`}>{known ? display ?? pct : "—"}</span>
      </div>
      <span className="seo-gauge__label">{label}</span>
    </div>
  );
}

/* ---------------------------------- donut ---------------------------------- */

export interface DonutSegment {
  key: string;
  label: string;
  value: number;
  /** One of the --seo-donut-* CSS custom properties defined in seo.css,
   *  e.g. "good" | "mid" | "warn" | "bad" | "flat". Keeps every donut on the
   *  same palette instead of each caller picking its own colors. */
  tone: "good" | "mid" | "warn" | "bad" | "flat";
}

export function Donut({
  segments, centerValue, centerLabel, size = 96, strokeWidth = 14, showLegend = true,
}: {
  segments: DonutSegment[];
  centerValue: string;
  centerLabel: string;
  size?: number;
  strokeWidth?: number;
  showLegend?: boolean;
}) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  const r = (size - strokeWidth) / 2;
  const c = size / 2;
  const circumference = TAU * r;

  let offset = 0;
  const arcs = segments
    .filter((s) => s.value > 0)
    .map((s) => {
      const frac = total > 0 ? s.value / total : 0;
      const dash = frac * circumference;
      const arc = (
        <circle
          key={s.key} cx={c} cy={c} r={r} className={`seo-donut__arc seo-donut__arc--${s.tone}`}
          strokeWidth={strokeWidth} fill="none"
          strokeDasharray={`${dash} ${circumference - dash}`}
          strokeDashoffset={-offset}
          transform={`rotate(-90 ${c} ${c})`}
        />
      );
      offset += dash;
      return arc;
    });

  return (
    <div className="seo-donut-wrap">
      <div
        className="seo-donut" role="img"
        aria-label={`${centerLabel}: ${centerValue}. ${segments.map((s) => `${s.label} ${s.value}`).join(", ")}`}
      >
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          <circle cx={c} cy={c} r={r} className="seo-donut__track" strokeWidth={strokeWidth} fill="none" />
          {total > 0 ? arcs : null}
        </svg>
        <div className="seo-donut__center">
          <span className="seo-donut__value">{centerValue}</span>
          <span className="seo-donut__sub">{centerLabel}</span>
        </div>
      </div>
      {showLegend && (
        <ul className="seo-donut__legend">
          {segments.map((s) => (
            <li key={s.key} className="seo-donut__legend-row">
              <span className={`seo-donut__swatch seo-donut__swatch--${s.tone}`} />
              <span className="seo-donut__legend-label">{s.label}</span>
              <span className="seo-donut__legend-num">{s.value}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
