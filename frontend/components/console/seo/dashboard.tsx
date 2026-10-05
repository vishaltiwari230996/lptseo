"use client";

/* The technical-health half of the console: Core Web Vitals and the keyword
 * pool — plus the tile row that summarises them, and the sitemap score, above
 * the fold. The sitemap itself is diagnosed by the deep audit (deep.tsx), which
 * crawls every URL; the tile reads the score that audit leaves behind.
 *
 * Each of these is a read of what the last run persisted, with its own refresh.
 * They are split from the main panel deliberately: a CrUX pull makes several
 * API calls, so it cannot sit on something that renders every time the page
 * loads.
 *
 * Every view here has to be correct with no data at all — that is the state a
 * new brand is in, and for CrUX it is the state a low-traffic site stays in
 * permanently, because Google will not publish a record it cannot anonymise.
 * So "no data" is written as a sentence explaining why, never as a zero.
 */

import { useCallback, useState } from "react";
import {
  seoKeywordPool, seoKeywordPoolRefresh, seoOauthStart,
  seoVitals, seoVitalsRefresh,
  type SeoKeywordPoolDoc, type SeoPoolKeyword,
  type SeoSitemapDoc, type SeoVitalMetric, type SeoVitalsDoc, type SeoVitalsSlice,
} from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { describeFailure } from "@/lib/load";
import { Icon } from "@/lib/kit-ui";
import { Donut, RadialGauge } from "./viz";

const fmt = (n: number) => n.toLocaleString("en-IN");

/* ------------------------------ summary tiles ----------------------------- */

export interface DashboardTilesProps {
  sitemap: SeoSitemapDoc | null;
  vitals: SeoVitalsDoc | null;
  pool: SeoKeywordPoolDoc | null;
  healthFindings: number | null;
}

/** Tone drives colour only. A tile with no data is never green — an unknown
 *  and a pass must not look alike at a glance, which is the whole job of a
 *  tile row. */
function Tile({ label, value, sub, tone = "none" }: {
  label: string;
  value: string;
  sub: string;
  tone?: "good" | "warn" | "bad" | "none";
}) {
  return (
    <div className={`seo-tile seo-tile--${tone}`}>
      <span className="seo-tile__label">{label}</span>
      <span className="seo-tile__value">{value}</span>
      <span className="seo-tile__sub">{sub}</span>
    </div>
  );
}

export function DashboardTiles({ sitemap, vitals, pool, healthFindings }: DashboardTilesProps) {
  const mobile = vitals?.origin_vitals?.mobile;
  const cwv = mobile?.assessment;

  return (
    <div className="seo-tiles">
      <div className="seo-tile seo-tile--gauge">
        <RadialGauge
          value={sitemap ? sitemap.score : null}
          label="Sitemap score"
          size={68}
          strokeWidth={6}
        />
        <span className="seo-tile__sub">
          {sitemap ? `${fmt(sitemap.url_count)} URLs` : "not audited yet"}
        </span>
      </div>
      <Tile
        label="Core Web Vitals"
        value={
          cwv === "passing" ? "Pass"
          : cwv === "failing" ? "Fail"
          : cwv === "needs-improvement" ? "Close"
          : "—"
        }
        sub={
          cwv === "insufficient-data" || !mobile
            ? "no field data yet"
            : `mobile · ${mobile.metrics.largest_contentful_paint?.p75 ?? "?"}ms LCP`
        }
        tone={
          cwv === "passing" ? "good"
          : cwv === "needs-improvement" ? "warn"
          : cwv === "failing" ? "bad"
          : "none"
        }
      />
      {pool ? (
        <div className="seo-tile seo-tile--donut">
          <span className="seo-tile__label">Keywords tracked</span>
          <Donut
            size={64} strokeWidth={8} showLegend={false}
            centerValue={fmt(pool.totals.keywords)} centerLabel="keywords"
            segments={[
              { key: "top3", label: "Top 3", value: pool.bands.top3, tone: "good" },
              { key: "page1", label: "Page 1", value: pool.bands.page1, tone: "mid" },
              { key: "page2", label: "Page 2", value: pool.bands.page2, tone: "warn" },
              { key: "beyond", label: "Beyond 20", value: pool.bands.beyond, tone: "flat" },
              { key: "unranked", label: "Not ranking", value: pool.bands.unranked, tone: "bad" },
            ]}
          />
          <span className="seo-tile__sub">{fmt(pool.bands.top3)} in top 3</span>
        </div>
      ) : (
        <Tile label="Keywords tracked" value="—" sub="not pooled yet" tone="none" />
      )}
      <Tile
        label="Opportunity"
        value={pool ? `+${fmt(pool.totals.opportunity)}` : "—"}
        sub={pool ? "est. clicks/mo within reach" : "needs Search Console"}
        tone={pool && pool.totals.opportunity > 0 ? "good" : "none"}
      />
      <Tile
        label="Site findings"
        value={healthFindings == null ? "—" : String(healthFindings)}
        sub={healthFindings == null ? "not analysed yet" : "from the expert review"}
        tone={healthFindings == null ? "none" : healthFindings === 0 ? "good" : healthFindings > 6 ? "bad" : "warn"}
      />
    </div>
  );
}

/* --------------------------- Core Web Vitals ------------------------------ */

function VitalBar({ m }: { m: SeoVitalMetric }) {
  const shown = m.unit === "cls" ? (m.p75 / 100).toFixed(2) : `${Math.round(m.p75)}ms`;
  const limit = m.poor_threshold * 1.35;
  const pct = Math.min(100, (m.p75 / limit) * 100);
  const goodAt = (m.good_threshold / limit) * 100;
  const poorAt = (m.poor_threshold / limit) * 100;

  return (
    <div className="seo-vital">
      <div className="seo-vital__head">
        <span className="seo-vital__name">{m.metric}</span>
        <span className={`seo-vital__value seo-vital__value--${m.category}`}>{shown}</span>
      </div>
      <div className="seo-vital__label">{m.label}</div>
      {/* The scale is Google's own boundaries, drawn in place — a bare bar with
          no thresholds on it says nothing about whether the number is good. */}
      <div className="seo-vital__track" role="img"
           aria-label={`${m.metric} 75th percentile ${shown}, rated ${m.category}`}>
        <span className="seo-vital__zone seo-vital__zone--good" style={{ width: `${goodAt}%` }} />
        <span className="seo-vital__zone seo-vital__zone--ni" style={{ width: `${poorAt - goodAt}%` }} />
        <span className="seo-vital__zone seo-vital__zone--poor" style={{ width: `${100 - poorAt}%` }} />
        <span className={`seo-vital__pin seo-vital__pin--${m.category}`} style={{ left: `${pct}%` }} />
      </div>
      {m.good_pct != null && (
        <div className="seo-vital__split">{m.good_pct}% of visits were good{m.poor_pct ? ` · ${m.poor_pct}% poor` : ""}</div>
      )}
    </div>
  );
}

function VitalsSlice({ title, slice }: { title: string; slice: SeoVitalsSlice }) {
  const order = [
    "largest_contentful_paint",
    "interaction_to_next_paint",
    "cumulative_layout_shift",
    "first_contentful_paint",
    "experimental_time_to_first_byte",
  ];
  return (
    <div className="seo-vitals__slice">
      <div className="seo-vitals__sliceHead">
        <span className="mr-section__title">{title}</span>
        <span className={`seo-chip seo-chip--cwv-${slice.assessment}`}>
          {slice.assessment === "passing" ? "Passing"
            : slice.assessment === "failing" ? "Failing"
            : slice.assessment === "needs-improvement" ? "Needs improvement"
            : "Not enough data"}
        </span>
      </div>
      <div className="seo-vitals__grid">
        {order.filter((k) => slice.metrics[k]).map((k) => <VitalBar key={k} m={slice.metrics[k]} />)}
      </div>
    </div>
  );
}

export function VitalsView({ brandId, doc, available, onLoaded, onToast }: {
  brandId: string;
  doc: SeoVitalsDoc | null;
  available: boolean;
  onLoaded: (d: SeoVitalsDoc) => void;
  onToast: ToastFn;
}) {
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const { vitals } = await seoVitalsRefresh(brandId);
      onLoaded(vitals);
      onToast("Core Web Vitals updated", "ok");
    } catch (e) {
      onToast(describeFailure(e, "Could not read Core Web Vitals"), "error");
    } finally {
      setBusy(false);
    }
  }, [brandId, onLoaded, onToast]);

  const mobile = doc?.origin_vitals?.mobile;
  const desktop = doc?.origin_vitals?.desktop;

  return (
    <div className="mr-section">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">
          Core Web Vitals — real Chrome visits{doc ? ` · ${doc.at}` : ""}
        </h3>
        <button className="seo-btn seo-btn--primary" disabled={busy || !available}
                onClick={() => void refresh()}>
          <Icon name="refresh-cw" size={13} /> {doc ? "Refresh" : "Pull report"}
        </button>
      </div>

      {!available && (
        <div className="seo-degraded">
          <Icon name="alert-triangle" size={14} />
          <div>
            Chrome UX Report is not configured. Create a free API key at
            console.cloud.google.com, enable “Chrome UX Report API” on that
            project, then set <code>SEO_CRUX_API_KEY</code> in backend/.env and
            restart. Nothing else here needs it.
          </div>
        </div>
      )}

      {available && !doc && (
        <div className="seo-empty">
          Nothing pulled yet. This is field data — the 75th percentile of what
          real Chrome visitors experienced over the trailing 28 days, which is
          the same measurement Search Console reports against. It is not a lab
          score, so it can only exist once the site has enough traffic for
          Google to report it anonymously.
        </div>
      )}

      {doc && (
        <>
          {mobile && <VitalsSlice title="Mobile" slice={mobile} />}
          {desktop && <VitalsSlice title="Desktop" slice={desktop} />}
          {!mobile && !desktop && (
            <div className="seo-empty">
              Chrome has no field data for {doc.origin} yet. That is normal for a
              site below the reporting threshold — it is not a fault, and there
              is nothing to fix.
            </div>
          )}
          {doc.pages.some((p) => p.has_data) && (
            <div className="seo-vitals__pages">
              <span className="mr-section__title">Busiest pages</span>
              {doc.pages.filter((p) => p.has_data).map((p) => (
                <div key={p.url} className="seo-vitals__pageRow">
                  <span className="seo-vitals__pageUrl">{p.url.replace(/^https?:\/\/[^/]+/, "") || "/"}</span>
                  <span className={`seo-chip seo-chip--cwv-${p.assessment}`}>
                    {p.assessment === "passing" ? "Passing" : p.assessment === "failing" ? "Failing" : "Mixed"}
                  </span>
                  {p.metrics?.largest_contentful_paint && (
                    <span className="seo-vitals__pageLcp">
                      LCP {Math.round(p.metrics.largest_contentful_paint.p75)}ms
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
          {doc.notes.map((n, i) => <p key={i} className="seo-note">{n}</p>)}
        </>
      )}
    </div>
  );
}

/* ------------------------------ keyword pool ------------------------------ */

const BAND_LABEL: Record<string, string> = {
  top3: "Top 3",
  page1: "Page 1",
  page2: "Page 2",
  beyond: "Beyond 20",
  unranked: "Not ranking",
};

export function KeywordPoolView({ brandId, doc, gscConnected, keywordLabRun, onLoaded, onToast }: {
  brandId: string;
  doc: SeoKeywordPoolDoc | null;
  gscConnected?: boolean;
  keywordLabRun?: boolean;
  onLoaded: (d: SeoKeywordPoolDoc) => void;
  onToast: ToastFn;
}) {
  const [busy, setBusy] = useState(false);
  const [band, setBand] = useState<string>("all");
  const [cluster, setCluster] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const { pool } = await seoKeywordPoolRefresh(brandId);
      onLoaded(pool);
      onToast(`Pooled ${pool.totals.keywords} keywords`, "ok");
    } catch (e) {
      onToast(describeFailure(e, "Could not rebuild the keyword pool"), "error");
    } finally {
      setBusy(false);
    }
  }, [brandId, onLoaded, onToast]);

  const connectGsc = useCallback(async () => {
    try {
      const { url } = await seoOauthStart(brandId);
      window.open(url, "_blank", "width=540,height=680");
      onToast("Choose the Google account that owns the site's Search Console, press Allow, then hit Refresh data here.");
    } catch (e) {
      onToast(describeFailure(e, "Could not start the Google connect"), "error");
    }
  }, [brandId, onToast]);

  // Keyword Lab is an LLM-cost operation, so it never auto-triggers from here —
  // this only points the user at the real control, which sits directly below
  // this panel in the console's Keywords section ("Keyword map" → "Map
  // keywords" in SeoAgent.tsx), matching the exact heading/button labels.
  const missingSources: { key: string; label: string; hint: string; cta: string; onClick: () => void }[] = [];
  if (!gscConnected) {
    missingSources.push({
      key: "gsc",
      label: "Search Console not connected",
      hint: "This is usually the largest source of real keyword volume.",
      cta: "Connect Search Console",
      onClick: () => void connectGsc(),
    });
  }
  if (!keywordLabRun) {
    missingSources.push({
      key: "lab",
      label: "Keyword Lab has not been run yet",
      hint: "Clustering adds every mapped keyword to this pool.",
      cta: "Run Keyword Lab",
      onClick: () => onToast("Scroll down to “Keyword map”, just below this table, and click “Map keywords”."),
    });
  }

  const rows: SeoPoolKeyword[] = (doc?.keywords ?? []).filter((k) =>
    (band === "all" || k.band === band) &&
    (cluster === "all" || k.cluster === cluster) &&
    (!query.trim() || k.keyword.includes(query.trim().toLowerCase())),
  );
  const shown = showAll ? rows : rows.slice(0, 40);

  return (
    <div className="mr-section">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">
          Keyword pool{doc ? ` · ${doc.totals.keywords} keywords` : ""}
        </h3>
        <button className="seo-btn seo-btn--primary" disabled={busy} onClick={() => void refresh()}>
          <Icon name="refresh-cw" size={13} /> {doc ? "Rebuild" : "Build pool"}
        </button>
      </div>

      {missingSources.map((s) => (
        <div key={s.key} className="seo-degraded">
          <Icon name="alert-triangle" size={14} />
          <div>
            <div><strong>{s.label}.</strong> {s.hint}</div>
            <button className="seo-btn" onClick={s.onClick}>
              {s.cta}
            </button>
          </div>
        </div>
      ))}

      {!doc ? (
        <div className="seo-empty">
          Nothing pooled yet. This joins every keyword the agent knows about —
          Search Console queries, the brand’s seeds, keyword-lab clusters and the
          blog plan — into one list, and scores each on the clicks a realistic
          ranking improvement would win.
        </div>
      ) : (
        <>
          <div className="seo-bands">
            {(Object.keys(BAND_LABEL) as (keyof typeof BAND_LABEL)[]).map((b) => (
              <button
                key={b}
                className={`seo-band${band === b ? " seo-band--on" : ""}`}
                onClick={() => setBand(band === b ? "all" : b)}
              >
                <span className="seo-band__n">{doc.bands[b as keyof typeof doc.bands]}</span>
                <span className="seo-band__label">{BAND_LABEL[b]}</span>
              </button>
            ))}
          </div>

          <div className="seo-poolbar">
            <input
              className="seo-input"
              placeholder="Filter keywords"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Filter keywords"
            />
            {doc.clusters.length > 0 && (
              <select className="seo-input" value={cluster} onChange={(e) => setCluster(e.target.value)}
                      aria-label="Filter by cluster">
                <option value="all">All clusters</option>
                {doc.clusters.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
            {(band !== "all" || cluster !== "all" || query) && (
              <button className="seo-btn" onClick={() => { setBand("all"); setCluster("all"); setQuery(""); }}>
                Clear filters
              </button>
            )}
          </div>

          {rows.length === 0 ? (
            <div className="seo-empty">No keywords match these filters.</div>
          ) : (
            <div className="seo-pool">
              <div className="seo-pool__row seo-pool__row--head">
                <span>Keyword</span>
                <span>Intent</span>
                <span className="seo-pool__num">Position</span>
                <span className="seo-pool__num">Impressions</span>
                <span className="seo-pool__num">Clicks</span>
                <span className="seo-pool__num">Opportunity</span>
              </div>
              {shown.map((k) => (
                <div key={k.keyword} className="seo-pool__row">
                  <span className="seo-pool__kw">
                    {k.keyword}
                    {k.cluster && <span className="seo-pool__cluster">{k.cluster}</span>}
                  </span>
                  <span className="seo-chip">{k.intent}</span>
                  <span className="seo-pool__num">{k.ranked ? k.position.toFixed(1) : "—"}</span>
                  <span className="seo-pool__num">{k.impressions ? fmt(k.impressions) : "—"}</span>
                  <span className="seo-pool__num">{k.clicks ? fmt(k.clicks) : "—"}</span>
                  <span className="seo-pool__num seo-pool__opp">
                    {k.opportunity ? `+${fmt(k.opportunity)}` : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}

          {rows.length > 40 && (
            <button className="seo-btn seo-todo__more" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show first 40 only" : `Show all ${rows.length}`}
            </button>
          )}
          {doc.notes.map((n, i) => <p key={i} className="seo-note">{n}</p>)}
        </>
      )}
    </div>
  );
}
