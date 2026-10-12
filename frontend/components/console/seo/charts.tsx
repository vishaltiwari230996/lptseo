"use client";

/* Traffic charts — hand-rolled SVG like the rest of viz.tsx (no chart
 * library), built to the dataviz method:
 *
 * - TrendCompare: the last 28 days as a 2px line + soft area, with the 28
 *   days BEFORE them as a dashed context line in muted ink — emphasis form,
 *   one hue + gray, so "are we up or down vs last month" is read at a glance.
 * - PeriodBars: week-on-week / month-on-month columns. Complete periods
 *   only get deltas; a partial month is drawn hollow and says "so far".
 * - One metric on screen at a time (Clicks | Impressions) — one axis, never
 *   two scales on one chart.
 *
 * Color: the charts' blue is its own token pair (--viz-accent), validated
 * for both surfaces — deliberately NOT --brand (LPT red) and NOT the
 * misleadingly named --blue-* tokens (which hold the brand red/yellow).
 * Text wears text tokens; color carries identity only on marks.
 */

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  compareWindows, monthlyBuckets, weeklyBuckets,
  type SeoDailyPoint, type SeriesBucket,
} from "@/lib/series";

type Metric = "clicks" | "impressions";
type Granularity = "weekly" | "monthly";

const fmt = (n: number) => n.toLocaleString("en-US");
const short = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 10_000 ? `${Math.round(n / 1000)}k`
    : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

function useMeasuredWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(640);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setWidth(Math.max(280, w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

interface Tip { x: number; y: number; lines: string[] }

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="seo-viz__tip" style={{ left: tip.x, top: tip.y }} role="status">
      {tip.lines.map((l, i) => <div key={i} className={i === 0 ? "seo-viz__tip-head" : undefined}>{l}</div>)}
    </div>
  );
}

/* Nice round gridline steps: 1/2/5 × 10^k covering the max. */
function gridSteps(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 3;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const top = Math.ceil(max / step) * step;
  return [0, top / 3, (2 * top) / 3, top].map(Math.round);
}

/* ------------------------------ trend compare ------------------------------ */

export function TrendCompare({ series, metric }: { series: SeoDailyPoint[]; metric: Metric }) {
  const [wrapRef, width] = useMeasuredWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const [hoverI, setHoverI] = useState<number | null>(null);

  const { current, previous } = useMemo(() => compareWindows(series, 28), [series]);
  const height = 180;
  const pad = { l: 44, r: 12, t: 12, b: 22 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;

  const values = current.map((p) => p[metric]);
  const prevValues = previous.map((p) => p[metric]);
  const steps = gridSteps(Math.max(1, ...values, ...prevValues));
  const top = steps[steps.length - 1];
  const x = (i: number, n: number) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * iw);
  const y = (v: number) => pad.t + ih - (v / top) * ih;

  if (current.length < 2) {
    return <p className="seo-note">Not enough daily data yet — connect Search Console and refresh.</p>;
  }

  const path = (pts: number[]) =>
    pts.map((v, i) => `${i ? "L" : "M"}${x(i, pts.length).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${path(values)} L${x(values.length - 1, values.length).toFixed(1)},${y(0)} L${pad.l},${y(0)} Z`;

  function onMove(ev: React.MouseEvent<SVGSVGElement>) {
    const box = ev.currentTarget.getBoundingClientRect();
    const px = ev.clientX - box.left;
    const i = Math.round(((px - pad.l) / Math.max(1, iw)) * (current.length - 1));
    if (i < 0 || i >= current.length) { setTip(null); setHoverI(null); return; }
    setHoverI(i);
    const cur = current[i];
    const prev = previous[i];
    const d = new Date(`${cur.date}T00:00:00Z`);
    setTip({
      x: Math.min(px + 14, width - 150), y: 8,
      lines: [
        d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" }),
        `This period: ${fmt(cur[metric])}`,
        ...(prev ? [`Previous: ${fmt(prev[metric])}`] : []),
      ],
    });
  }

  const totalNow = values.reduce((a, b) => a + b, 0);
  const totalPrev = prevValues.reduce((a, b) => a + b, 0);
  const delta = totalPrev ? Math.round(((totalNow - totalPrev) / totalPrev) * 100) : null;

  return (
    <div ref={wrapRef} className="seo-viz" onMouseLeave={() => { setTip(null); setHoverI(null); }}>
      <div className="seo-viz__head">
        <span className="seo-viz__big">{fmt(totalNow)}</span>
        <span className="seo-viz__cap">{metric} · last 28 days</span>
        {delta != null && (
          <span className={`seo-viz__delta seo-viz__delta--${delta >= 0 ? "up" : "down"}`}>
            {delta >= 0 ? "▲" : "▼"} {Math.abs(delta)}% vs previous 28
          </span>
        )}
      </div>
      <svg width={width} height={height} onMouseMove={onMove} role="img"
           aria-label={`Daily ${metric}, last 28 days against the previous 28`}>
        {steps.map((s) => (
          <g key={s}>
            <line x1={pad.l} x2={width - pad.r} y1={y(s)} y2={y(s)} className="seo-viz__grid" />
            <text x={pad.l - 8} y={y(s) + 3.5} className="seo-viz__axis" textAnchor="end">{short(s)}</text>
          </g>
        ))}
        {prevValues.length > 1 && <path d={path(prevValues)} className="seo-viz__prev" />}
        <path d={area} className="seo-viz__area" />
        <path d={path(values)} className="seo-viz__line" />
        {hoverI != null && (
          <g>
            <line x1={x(hoverI, values.length)} x2={x(hoverI, values.length)}
                  y1={pad.t} y2={pad.t + ih} className="seo-viz__cross" />
            <circle cx={x(hoverI, values.length)} cy={y(values[hoverI])} r={4.5} className="seo-viz__dot" />
          </g>
        )}
        <text x={pad.l} y={height - 6} className="seo-viz__axis">
          {new Date(`${current[0].date}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" })}
        </text>
        <text x={width - pad.r} y={height - 6} className="seo-viz__axis" textAnchor="end">
          {new Date(`${current[current.length - 1].date}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" })}
        </text>
      </svg>
      <div className="seo-viz__legend">
        <span><i className="seo-viz__swatch" /> this period</span>
        <span><i className="seo-viz__swatch seo-viz__swatch--prev" /> previous period</span>
      </div>
      <Tooltip tip={tip} />
    </div>
  );
}

/* ------------------------------- period bars ------------------------------- */

export function PeriodBars({ buckets, metric }: { buckets: SeriesBucket[]; metric: Metric }) {
  const [wrapRef, width] = useMeasuredWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);

  const height = 190;
  const pad = { l: 44, r: 12, t: 20, b: 26 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const steps = gridSteps(Math.max(1, ...buckets.map((b) => b[metric])));
  const top = steps[steps.length - 1];
  const slot = iw / Math.max(1, buckets.length);
  const barW = Math.min(56, Math.max(14, slot - 14));
  const y = (v: number) => pad.t + ih - (v / top) * ih;

  if (!buckets.length) {
    return <p className="seo-note">No complete periods yet — the chart fills in as daily data accumulates.</p>;
  }
  const deltaKey = metric === "clicks" ? "delta_clicks" : "delta_impressions";
  const last = buckets[buckets.length - 1];

  return (
    <div ref={wrapRef} className="seo-viz" onMouseLeave={() => setTip(null)}>
      <svg width={width} height={height} role="img" aria-label={`${metric} per period`}>
        {steps.map((s) => (
          <g key={s}>
            <line x1={pad.l} x2={width - pad.r} y1={y(s)} y2={y(s)} className="seo-viz__grid" />
            <text x={pad.l - 8} y={y(s) + 3.5} className="seo-viz__axis" textAnchor="end">{short(s)}</text>
          </g>
        ))}
        {buckets.map((b, i) => {
          const cx = pad.l + slot * i + slot / 2;
          const v = b[metric];
          const h = Math.max(2, (v / top) * ih);
          const delta = b[deltaKey];
          return (
            <g key={b.full}
               onMouseEnter={() => setTip({
                 x: Math.min(cx - 40, width - 170), y: 0,
                 lines: [b.full, `${metric}: ${fmt(v)}`,
                         ...(delta != null ? [`${delta >= 0 ? "▲" : "▼"} ${Math.abs(delta)}% vs previous`] : [])],
               })}>
              {/* generous invisible hit target behind the thin mark */}
              <rect x={cx - slot / 2} y={pad.t} width={slot} height={ih + pad.b} fill="transparent" />
              <rect x={cx - barW / 2} y={y(v)} width={barW} height={h} rx={4}
                    className={`seo-viz__bar${b.partial ? " seo-viz__bar--partial" : ""}${b === last && !b.partial ? " seo-viz__bar--now" : ""}`} />
              {/* selective direct labels: only the latest complete bar speaks */}
              {b === last && (
                <text x={cx} y={y(v) - 7} textAnchor="middle" className="seo-viz__label">
                  {short(v)}{delta != null ? `  ${delta >= 0 ? "▲" : "▼"}${Math.abs(delta)}%` : ""}
                </text>
              )}
              <text x={cx} y={height - 8} textAnchor="middle" className="seo-viz__axis">{b.label}</text>
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

/* -------------------------------- the panel -------------------------------- */

export function TrafficCharts({ series }: { series: SeoDailyPoint[] }) {
  const [metric, setMetric] = useState<Metric>("clicks");
  const [granularity, setGranularity] = useState<Granularity>("weekly");

  const buckets = useMemo(
    () => (granularity === "weekly" ? weeklyBuckets(series, 8) : monthlyBuckets(series, 4)),
    [series, granularity],
  );

  if (!series.length) {
    return (
      <div className="mr-section">
        <h3 className="mr-section__title">Traffic trend</h3>
        <p className="seo-empty">
          No daily data yet — connect Search Console and hit Refresh data; the
          week-on-week and month-on-month charts fill in from there.
        </p>
      </div>
    );
  }

  return (
    <div className="mr-section seo-viz__panel">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">Traffic trend</h3>
        <div className="seo-viz__controls" role="group" aria-label="Chart controls">
          {(["clicks", "impressions"] as const).map((m) => (
            <button key={m} type="button"
                    className={`seo-chip${metric === m ? " seo-chip--viz-on" : ""}`}
                    onClick={() => setMetric(m)}>
              {m === "clicks" ? "Clicks" : "Impressions"}
            </button>
          ))}
          <span className="seo-viz__sep" aria-hidden="true" />
          {(["weekly", "monthly"] as const).map((g) => (
            <button key={g} type="button"
                    className={`seo-chip${granularity === g ? " seo-chip--viz-on" : ""}`}
                    onClick={() => setGranularity(g)}>
              {g === "weekly" ? "Week on week" : "Month on month"}
            </button>
          ))}
        </div>
      </div>
      <TrendCompare series={series} metric={metric} />
      <h4 className="deep-h4">{granularity === "weekly" ? "Week on week" : "Month on month"} · {metric}</h4>
      <PeriodBars buckets={buckets} metric={metric} />
    </div>
  );
}
