"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  isAbortError, RequestSequence,
  seoAsk, seoAuditReport, seoBriefs, seoBuildBrief, seoCompetitorProfiles, seoCompetitorProfilesRefresh,
  seoCompetitors, seoDraftScore, seoKeywordLab, seoRunAudit, seoRunKeywordLab, seoSetCompetitors,
  seoTrackCompetitors, seoUpdatePlan,
  type SeoAuditReport, type SeoBrief, type SeoCompetitorProfilesDoc, type SeoCompetitors,
  type SeoDraftScore, type SeoKeywordLab, type SeoUpdatePlan,
} from "@/lib/api";
import type { ToastFn } from "@/components/console/ConsoleApp";
import { Icon } from "@/lib/kit-ui";
import { describeFailure } from "@/lib/load";

/** Researcher-layer tabs for the SEO agent: Keywords, Competitors, Briefs, Audit. */

const fmt = (n: number) => n.toLocaleString("en-US");
/** Was a local re-implementation of the same two lines every other file had.
 *  The shared one adds the part none of them had: a timeout keeps
 *  `RequestTimeoutError`'s own message instead of the fallback. */
const errMsg = describeFailure;

/** Every load in this file used to end in `.catch(() => {})`, so a failure was
 *  indistinguishable from "you have no data yet" — and the empty state then
 *  invited the user into a second action that was never the problem. This is
 *  the honest version of that region. */
function LoadError({ what, error, onRetry }: { what: string; error: string; onRetry: () => void }) {
  return (
    <div className="seo-degraded">
      <Icon name="alert-triangle" size={14} />
      <div>
        <div><strong>Couldn&apos;t load {what}.</strong> This is a load failure, not an empty result.</div>
        <div>{error}</div>
        <button className="seo-btn" onClick={onRetry}>
          <Icon name="refresh-cw" size={13} /> Try again
        </button>
      </div>
    </div>
  );
}

function CoverageChip({ coverage }: { coverage: "gap" | "weak" | "ranking" }) {
  const text = { gap: "not covered — opportunity", weak: "below the fold", ranking: "ranking" }[coverage];
  return <span className={`seo-chip seo-chip--cov-${coverage}`}>{text}</span>;
}

function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <div className="seo-degraded">
      <Icon name="alert-triangle" size={14} />
      <div>{notes.map((n, i) => <div key={i}>{n}</div>)}</div>
    </div>
  );
}

/* ------------------------------- Keywords -------------------------------- */

export function KeywordsView({ brandId, onToast }: { brandId: string; onToast: ToastFn }) {
  const [lab, setLab] = useState<SeoKeywordLab | null>(null);
  const [labError, setLabError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [briefBusy, setBriefBusy] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    // Clearing first matters as much as the abort: leaving the previous brand's
    // map on screen while the new one loads shows brand A's keywords under
    // brand B's name.
    const ctrl = new AbortController();
    setLab(null);
    setLabError(null);
    seoKeywordLab(brandId, { signal: ctrl.signal })
      .then((r) => setLab(r.lab))
      .catch((e: unknown) => {
        if (isAbortError(e)) return; // brand switched — the newer load owns this
        setLabError(errMsg(e, "The keyword map could not be read"));
      });
    return () => ctrl.abort();
  }, [brandId, retry]);

  async function runLab() {
    setBusy(true);
    onToast("Mapping keywords — expanding seeds, clustering by intent…");
    try {
      setLab(await seoRunKeywordLab(brandId));
      setLabError(null);
      onToast("Keyword map ready");
    } catch (e) {
      onToast(errMsg(e, "Keyword mapping failed"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function brief(keyword: string) {
    setBriefBusy(keyword);
    onToast(`Building a brief for “${keyword}”…`);
    try {
      await seoBuildBrief(brandId, keyword);
      onToast("Brief ready — open the Briefs tab");
    } catch (e) {
      onToast(errMsg(e, "Brief failed"), "error");
    } finally {
      setBriefBusy(null);
    }
  }

  return (
    <div className="mr-section">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">Keyword map — clusters ranked by opportunity</h3>
        <button className="seo-btn seo-btn--primary" disabled={busy} onClick={() => void runLab()}>
          <Icon name="refresh-cw" size={13} /> {lab ? "Re-map keywords" : "Map keywords"}
        </button>
      </div>
      {lab && <Notes notes={lab.degraded} />}
      {labError && (
        <LoadError what="the keyword map" error={labError} onRetry={() => setRetry((n) => n + 1)} />
      )}
      {!lab && !labError && <div className="seo-empty">No keyword map yet — hit “Map keywords”. It expands your seed terms into long-tail keywords, groups them by intent, and shows where you're not covered.</div>}
      {lab && (
        <div className="seo-lab__meta">
          {fmt(lab.keyword_count)} keywords · {lab.clusters.length} clusters · {lab.gaps.length} content gaps · mapped {lab.at}
        </div>
      )}
      {lab && ([
        { key: "high", label: "High priority", hint: "buyer searches you don't own — act now" },
        { key: "medium", label: "Medium priority", hint: "plan these into the next content cycle" },
        { key: "watch", label: "Worth watching", hint: "already ranking — defend, don't rebuild" },
      ] as const).map(({ key, label, hint }) => {
        const group = lab.clusters.filter((c) => c.tier === key);
        if (!group.length) return null;
        return (
          <div key={key} className="seo-tier">
            <div className="seo-tier__head">
              <span className={`seo-chip seo-chip--tier-${key}`}>{label}</span>
              <span className="seo-lab__meta">{hint}</span>
            </div>
            {group.map((c) => (
              <details key={c.name} className="seo-cluster">
                <summary>
                  <span className="seo-cluster__name">{c.name}</span>
                  <span className="seo-cluster__chips">
                    <span className="seo-chip">{c.intent}</span>
                    <CoverageChip coverage={c.coverage} />
                    {c.aio_present && <span className="seo-chip seo-chip--trend-new">AI Overview</span>}
                  </span>
                  <span className="seo-cluster__nums">
                    {c.volume_est ? `~${fmt(c.volume_est)}/mo` : ""}
                    {c.best_position ? ` best pos ${c.best_position}` : ""}
                  </span>
                  <button className="seo-btn" disabled={briefBusy === c.name}
                          onClick={(e) => { e.preventDefault(); void brief(c.name); }}>
                    Brief
                  </button>
                  <span className="seo-cluster__rec">{c.recommendation}</span>
                  {!!c.owned_by?.length && (
                    <span className="seo-cluster__owners">
                      Owned today by: {c.owned_by.join(" · ")}
                    </span>
                  )}
                </summary>
                <div className="seo-cluster__kws">
                  {c.keywords.map((k) => <span key={k} className="seo-chip">{k}</span>)}
                </div>
              </details>
            ))}
          </div>
        );
      })}
    </div>
  );
}

/* ---------------------------------- Ask ----------------------------------- */

export function AskView({ brandId, brandName }: { brandId: string; brandName: string }) {
  const [msgs, setMsgs] = useState<{ role: "user" | "agent"; text: string }[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(text?: string) {
    const question = (text ?? q).trim();
    if (!question || busy) return;
    setMsgs((m) => [...m, { role: "user", text: question }]);
    setQ("");
    setBusy(true);
    try {
      const res = await seoAsk(brandId, question);
      setMsgs((m) => [...m, { role: "agent", text: res.answer }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "agent", text: errMsg(e, "The strategist is unavailable right now.") }]);
    } finally {
      setBusy(false);
    }
  }

  const suggestions = [
    "What should we do first?",
    "Which keywords should we focus on?",
    "What content should we publish this month?",
    "Where are competitors beating us?",
  ];

  return (
    <div className="mr-section seo-ask">
      <h3 className="mr-section__title">Ask your SEO strategist — answers grounded in {brandName}'s data</h3>
      {!msgs.length && (
        <div className="seo-cluster__kws">
          {suggestions.map((s) => (
            <button key={s} className="seo-chip" onClick={() => void send(s)}>{s}</button>
          ))}
        </div>
      )}
      <div className="seo-ask__thread">
        {msgs.map((m, i) => (
          <div key={i} className={`seo-msg seo-msg--${m.role}`}>{m.text}</div>
        ))}
        {busy && <div className="seo-msg seo-msg--agent seo-msg--busy">Reading the brand's data…</div>}
      </div>
      <div className="seo-form__row">
        <input className="seo-input seo-input--grow"
               placeholder="Ask anything — priorities, strategy, why something dropped…"
               value={q} onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && void send()} />
        <button className="seo-btn seo-btn--primary" disabled={busy || !q.trim()} onClick={() => void send()}>
          Ask
        </button>
      </div>
    </div>
  );
}

/* ------------------------------ Competitors ------------------------------ */

export function CompetitorsView({ brandId, isCreator, onToast }: {
  brandId: string; isCreator: boolean; onToast: ToastFn;
}) {
  const [data, setData] = useState<SeoCompetitors | null>(null);
  const [dataError, setDataError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [profiles, setProfiles] = useState<SeoCompetitorProfilesDoc | null>(null);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const [profilesBusy, setProfilesBusy] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [retry, setRetry] = useState(0);

  // Both loads are keyed on the brand and both clear first, so a slower
  // response for the previous brand can neither land nor linger under the new
  // brand's heading.
  useEffect(() => {
    const ctrl = new AbortController();
    setData(null);
    setDataError(null);
    setExpanded(new Set());
    seoCompetitors(brandId, { signal: ctrl.signal })
      .then(setData)
      .catch((e: unknown) => {
        if (isAbortError(e)) return;
        setDataError(errMsg(e, "The tracked-competitor list could not be read"));
      });
    return () => ctrl.abort();
  }, [brandId, retry]);

  useEffect(() => {
    const ctrl = new AbortController();
    setProfiles(null);
    setProfilesError(null);
    seoCompetitorProfiles(brandId, { signal: ctrl.signal })
      .then((r) => setProfiles(r.profiles))
      .catch((e: unknown) => {
        if (isAbortError(e)) return;
        setProfilesError(errMsg(e, "Competitor profiles could not be read"));
      });
    return () => ctrl.abort();
  }, [brandId, retry]);

  function toggleExpanded(domain: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain); else next.add(domain);
      return next;
    });
  }

  async function refreshProfiles() {
    setProfilesBusy(true);
    onToast("Building competitor profiles — visibility, keywords won, and recent content…");
    try {
      const doc = await seoCompetitorProfilesRefresh(brandId);
      setProfiles(doc);
      setProfilesError(null);
      onToast(doc.notes.length ? doc.notes[0] : "Competitor profiles refreshed");
    } catch (e) {
      onToast(errMsg(e, "Refresh failed"), "error");
    } finally {
      setProfilesBusy(false);
    }
  }

  async function track() {
    setBusy(true);
    onToast("Checking rankings and competitor sitemaps…");
    try {
      const res = await seoTrackCompetitors(brandId);
      setData((d) => d && { ...d, shifts: res.shifts, feed: res.feed });
      onToast(res.degraded.length ? res.degraded[0] : "Tracking updated");
    } catch (e) {
      onToast(errMsg(e, "Tracking failed"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function toggleTracked(domain: string, on: boolean) {
    if (!data) return;
    const next = on ? [...data.tracked, domain] : data.tracked.filter((d) => d !== domain);
    try {
      const res = await seoSetCompetitors(brandId, next);
      setData({ ...data, tracked: res.tracked });
    } catch (e) {
      onToast(errMsg(e, "Could not update the list"), "error");
    }
  }

  return (
    <div className="seo-stack">
      <div className="mr-section">
        <div className="seo-lab__head">
          <h3 className="mr-section__title">Top competitors — visibility, keywords won, content feed</h3>
          <button className="seo-btn seo-btn--primary" disabled={profilesBusy} onClick={() => void refreshProfiles()}>
            <Icon name="refresh-cw" size={13} /> Refresh
          </button>
        </div>
        {profiles && <Notes notes={profiles.notes} />}
        {profilesError && (
          <LoadError what="the competitor profiles" error={profilesError}
            onRetry={() => setRetry((n) => n + 1)} />
        )}
        {!profiles && !profilesError && (
          <div className="seo-empty">
            No competitor profiles yet — hit “Refresh data” on this brand first (competitor discovery
            needs rank snapshots), then hit “Refresh” here.
          </div>
        )}
        {profiles && !profiles.profiles.length && (
          <div className="seo-empty">No competitors resolved yet — add some below, or hit “Refresh” once rankings are tracked.</div>
        )}
        {profiles && !!profiles.profiles.length && (
          <div className="seo-comp__grid">
            {profiles.profiles.map((p) => {
              const open = expanded.has(p.domain);
              const hasDetail = !!(p.keywords_won.length || p.recent_posts.length || p.hot_topics.length);
              return (
                <div key={p.domain} className="seo-comp__card">
                  <button className="seo-comp__head" onClick={() => toggleExpanded(p.domain)} aria-expanded={open}>
                    <span className="seo-comp__domain">{p.domain}</span>
                    <Icon name={open ? "chevron-up" : "chevron-down"} size={15} />
                  </button>
                  <div className="seo-comp__stats">
                    <div className="seo-stat">
                      <span className="seo-stat__label">Visibility</span>
                      <span className="seo-stat__num">{p.visibility_pct != null ? `${p.visibility_pct}%` : "—"}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Avg. position</span>
                      <span className="seo-stat__num">{p.avg_position != null ? p.avg_position : "—"}</span>
                    </div>
                    <div className="seo-stat">
                      <span className="seo-stat__label">Keywords won</span>
                      <span className="seo-stat__num">{p.keywords_won.length}</span>
                    </div>
                  </div>
                  {open && !hasDetail && (
                    <div className="seo-empty">No keyword wins or tracked content for {p.domain} yet.</div>
                  )}
                  {open && !!p.keywords_won.length && (
                    <div className="seo-comp__section">
                      <div className="seo-lab__meta">Keywords they beat us on</div>
                      {p.keywords_won.map((k) => (
                        <div key={k.keyword} className="seo-comp__kw">
                          <span className="seo-comp__kw-name">{k.keyword}</span>
                          <span className="seo-comp__kw-pos">
                            them #{k.their_position} · us {k.our_position != null ? `#${k.our_position}` : "not in top 10"}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {open && !!p.recent_posts.length && (
                    <div className="seo-comp__section">
                      <div className="seo-lab__meta">Recent content</div>
                      {p.recent_posts.map((post) => (
                        <div key={post.url} className="seo-comp__post">
                          <a className="seo-comp__post-title" href={post.url} target="_blank" rel="noreferrer">
                            {post.title}
                          </a>
                          <span className="seo-comp__post-meta">
                            <span className="seo-chip">{post.topic}</span>
                            <span className="seo-comp__reach" title={post.estimate_basis || undefined}>
                              {post.est_monthly_clicks != null
                                ? `~${fmt(post.est_monthly_clicks)} clicks/mo (estimate)`
                                : post.estimate_basis || "reach unknown"}
                            </span>
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {open && !!p.hot_topics.length && (
                    <div className="seo-comp__section">
                      <div className="seo-lab__meta">What they're publishing about</div>
                      <div className="seo-cluster__kws">
                        {p.hot_topics.map((t) => <span key={t} className="seo-chip">{t}</span>)}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="mr-section">
        <div className="seo-lab__head">
          <h3 className="mr-section__title">Where we rank — tracked keywords</h3>
          <button className="seo-btn seo-btn--primary" disabled={busy} onClick={() => void track()}>
            <Icon name="refresh-cw" size={13} /> Check now
          </button>
        </div>
        {dataError && (
          <LoadError what="the ranking snapshots" error={dataError}
            onRetry={() => setRetry((n) => n + 1)} />
        )}
        {!dataError && !data?.shifts.length && <div className="seo-empty">No snapshots yet — “Check now” records where you rank for your seed keywords and top clusters, and who sits above you.</div>}
        {data?.shifts.map((s) => (
          <div key={s.keyword} className="seo-shift">
            <span className="seo-shift__kw">{s.keyword}</span>
            <span className="seo-shift__pos">{s.position ? `#${s.position}` : "not in top 10"}</span>
            {s.delta != null && s.delta !== 0 && (
              <span className={`seo-delta seo-delta--${s.delta > 0 ? "up" : "down"}`}>
                <Icon name={s.delta > 0 ? "trending-up" : "trending-down"} size={13} />
                {Math.abs(s.delta)}
              </span>
            )}
            <span className="seo-shift__top">{s.top.slice(0, 3).join(" · ")}</span>
          </div>
        ))}
      </div>

      <div className="mr-section">
        <h3 className="mr-section__title">Competitors we watch</h3>
        <div className="seo-cluster__kws">
          {data?.tracked.map((d) => (
            <span key={d} className="seo-chip seo-chip--on">
              {d}
              {isCreator && (
                <button className="seo-chip__x" aria-label={`Stop watching ${d}`}
                        onClick={() => void toggleTracked(d, false)}>×</button>
              )}
            </span>
          ))}
          {!data?.tracked.length && (
            <span className="seo-empty">{dataError ? "Not loaded — see the error above." : "None yet."}</span>
          )}
        </div>
        {isCreator && !!data?.suggested.length && (
          <>
            <div className="seo-lab__meta">Seen most often above you in the results — click to watch:</div>
            <div className="seo-cluster__kws">
              {data.suggested.filter((d) => !data.tracked.includes(d)).map((d) => (
                <button key={d} className="seo-chip" onClick={() => void toggleTracked(d, true)}>+ {d}</button>
              ))}
            </div>
          </>
        )}
        {Object.entries(data?.feed ?? {}).map(([domain, entry]) => (
          <div key={domain} className="seo-feed">
            <div className="seo-feed__head">
              <span>{domain}</span>
              <span className="seo-lab__meta">
                {entry.first_check
                  ? `baseline saved (${fmt(entry.total)} pages) — new content shows from the next check`
                  : `${entry.new_count} new page(s) since last check`}
              </span>
            </div>
            {entry.new_urls.map((u) => (
              <a key={u} className="seo-feed__url" href={u} target="_blank" rel="noreferrer">{u}</a>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/* -------------------------------- Briefs --------------------------------- */

function briefMarkdown(b: SeoBrief): string {
  return [
    `# Content brief: ${b.keyword}`,
    `Intent: ${b.intent} · Target length: ~${b.target_word_count} words · Built ${b.at}`,
    `Target keywords: ${b.target_keywords.join(", ")}`,
    "",
    "## Outline",
    ...b.outline.map((o) => `- **${o.heading}** — ${o.note}`),
    "",
    b.questions.length ? `## Answer these questions\n${b.questions.map((q) => `- ${q}`).join("\n")}` : "",
    b.entities.length ? `## Terms to include\n${b.entities.join(", ")}` : "",
    b.internal_links.length ? `## Link internally to\n${b.internal_links.map((l) => `- ${l}`).join("\n")}` : "",
    `## Schema\n${b.schema_recommended.join(", ")}`,
  ].filter(Boolean).join("\n");
}

export function BriefsView({ brandId, onToast }: { brandId: string; onToast: ToastFn }) {
  const [briefs, setBriefs] = useState<SeoBrief[]>([]);
  const [briefsError, setBriefsError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState("");
  const [busy, setBusy] = useState(false);

  // A sequence rather than a per-effect controller: `build()` reloads the list
  // too, so the brand switch and the post-build reload compete for the same
  // state and only the newest may write to it.
  const seq = useRef<RequestSequence | null>(null);
  useEffect(() => () => seq.current?.cancel(), []);

  const load = useCallback(() => {
    const s = (seq.current ??= new RequestSequence());
    const ticket = s.start();
    setBriefsError(null);
    seoBriefs(brandId, { signal: ticket.signal })
      .then((r) => {
        if (s.isCurrent(ticket)) setBriefs(r.briefs);
      })
      .catch((e: unknown) => {
        if (isAbortError(e) || !s.isCurrent(ticket)) return;
        setBriefs([]);
        setBriefsError(errMsg(e, "The brief list could not be read"));
      });
  }, [brandId]);

  useEffect(() => {
    setBriefs([]); // never show the previous brand's briefs under this one
    load();
  }, [load]);

  async function build() {
    if (!keyword.trim()) return;
    setBusy(true);
    onToast("Reverse-engineering the top results…");
    try {
      await seoBuildBrief(brandId, keyword.trim());
      setKeyword("");
      load();
      onToast("Brief ready");
    } catch (e) {
      onToast(errMsg(e, "Brief failed"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function copy(b: SeoBrief) {
    await navigator.clipboard.writeText(briefMarkdown(b));
    onToast("Brief copied — paste it to your writer");
  }

  return (
    <div className="mr-section">
      <div className="seo-lab__head">
        <h3 className="mr-section__title">Content briefs — built from what actually ranks</h3>
      </div>
      <div className="seo-form__row">
        <input className="seo-input seo-input--grow" placeholder="Target keyword, e.g. legal intake process"
               value={keyword} onChange={(e) => setKeyword(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && void build()} />
        <button className="seo-btn seo-btn--primary" disabled={busy || !keyword.trim()} onClick={() => void build()}>
          Build brief
        </button>
      </div>
      {briefsError && <LoadError what="the saved briefs" error={briefsError} onRetry={load} />}
      {!briefs.length && !briefsError && <div className="seo-empty">No briefs yet. A brief reads the top-ranking pages for your keyword and hands your writer the outline, questions, and terms needed to beat them.</div>}
      {briefs.map((b) => (
        <details key={b.id} className="seo-brief">
          <summary>
            <span className="seo-cluster__name">{b.keyword}</span>
            <span className="seo-cluster__chips">
              <span className="seo-chip">{b.intent}</span>
              {b.aio_present && <span className="seo-chip seo-chip--trend-new">AI Overview target</span>}
            </span>
            <span className="seo-cluster__nums">~{fmt(b.target_word_count)} words · {b.at}</span>
            <button className="seo-btn" onClick={(e) => { e.preventDefault(); void copy(b); }}>Copy</button>
          </summary>
          <Notes notes={b.degraded} />
          <ol className="seo-brief__outline">
            {b.outline.map((o, i) => (
              <li key={i}><strong>{o.heading}</strong><span>{o.note}</span></li>
            ))}
          </ol>
          {!!b.questions.length && (
            <div className="seo-brief__block">
              <span className="seo-stat__label">Answer these</span>
              {b.questions.map((q) => <div key={q} className="seo-brief__line">{q}</div>)}
            </div>
          )}
          {!!b.entities.length && (
            <div className="seo-brief__block">
              <span className="seo-stat__label">Terms to include</span>
              <div className="seo-cluster__kws">{b.entities.map((t) => <span key={t} className="seo-chip">{t}</span>)}</div>
            </div>
          )}
          {!!b.internal_links.length && (
            <div className="seo-brief__block">
              <span className="seo-stat__label">Link internally to</span>
              {b.internal_links.map((l) => <div key={l} className="seo-brief__line">{l}</div>)}
            </div>
          )}
        </details>
      ))}
    </div>
  );
}

/* --------------------------------- Audit --------------------------------- */

async function copyReport(brandName: string, r: SeoAuditReport, onToast: ToastFn) {
  const lines = [
    `# SEO health report — ${brandName}`,
    `Generated ${r.at} · Health score ${r.health_score}/100 · ${r.pages_ok}/${r.pages_checked} pages OK`,
    "",
    "## Site foundations",
    ...(r.site_checks ?? []).map((c) => `- ${c.ok ? "✅" : "❌"} ${c.name}: ${c.note}${c.ok ? "" : ` — ${c.fix}`}`),
    "",
    "## Issues",
    ...(r.issues.length
      ? r.issues.flatMap((i) => [
          `### ${i.issue} (${i.severity}, ${i.count} page${i.count === 1 ? "" : "s"})`,
          `Fix: ${i.fix}`,
          ...i.pages.map((p) => `- ${p}`),
        ])
      : ["Nothing found at this depth."]),
  ];
  await navigator.clipboard.writeText(lines.join("\n"));
  onToast("Report copied — paste it anywhere");
}

export function AuditView({ brandId, brandName, onToast }: {
  brandId: string; brandName: string; onToast: ToastFn;
}) {
  const [report, setReport] = useState<SeoAuditReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [draftKw, setDraftKw] = useState("");
  const [scored, setScored] = useState<SeoDraftScore | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setReport(null);
    setReportError(null);
    seoAuditReport(brandId, { signal: ctrl.signal })
      .then((r) => setReport(r.report))
      .catch((e: unknown) => {
        if (isAbortError(e)) return;
        setReportError(errMsg(e, "The last audit could not be read"));
      });
    return () => ctrl.abort();
  }, [brandId, retry]);

  async function run() {
    setBusy(true);
    onToast("Auditing the site — checking up to 80 pages…");
    try {
      setReport(await seoRunAudit(brandId));
      setReportError(null);
      onToast("Audit done");
    } catch (e) {
      onToast(errMsg(e, "Audit failed"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function score() {
    if (!draft.trim() || !draftKw.trim()) return;
    try {
      setScored(await seoDraftScore(brandId, draft, draftKw.trim()));
    } catch (e) {
      onToast(errMsg(e, "Scoring failed"), "error");
    }
  }

  return (
    <div className="seo-stack">
      <div className="mr-section">
        <div className="seo-lab__head">
          <h3 className="mr-section__title">Technical health — {brandName}</h3>
          <button className="seo-btn seo-btn--primary" disabled={busy} onClick={() => void run()}>
            <Icon name="refresh-cw" size={13} /> {report ? "Re-run audit" : "Run audit"}
          </button>
        </div>
        {reportError && (
          <LoadError what="the last audit" error={reportError} onRetry={() => setRetry((n) => n + 1)} />
        )}
        {!report && !reportError && <div className="seo-empty">No audit yet — the scan finds broken pages, missing titles/descriptions, duplicate metadata, and missing structured data. Nothing is changed automatically.</div>}
        {report && (
          <>
            <div className="seo-audit__score">
              <span className="seo-audit__num">{report.health_score}</span>
              <span className="seo-lab__meta">/100 · {report.pages_ok}/{report.pages_checked} pages OK · {report.at}</span>
              <button className="seo-btn" onClick={() => void copyReport(brandName, report, onToast)}>
                Copy full report
              </button>
            </div>
            {!!report.site_checks?.length && (
              <div className="seo-scorecard">
                {report.site_checks.map((c) => (
                  <div key={c.name} className={`seo-check${c.ok ? " seo-check--ok" : ""}`}>
                    <Icon name={c.ok ? "check" : "x"} size={14} />
                    <span className="seo-check__name">{c.name}</span>
                    <span className="seo-check__note">{c.ok ? c.note : `${c.note} — ${c.fix}`}</span>
                  </div>
                ))}
              </div>
            )}
            {!report.issues.length && <div className="seo-empty">Clean — nothing to fix at this depth.</div>}
            {report.issues.map((i) => (
              <details key={i.issue} className="seo-issue">
                <summary>
                  <span className={`seo-chip seo-chip--sev-${i.severity}`}>{i.severity}</span>
                  <span className="seo-cluster__name">{i.issue}</span>
                  <span className="seo-cluster__nums">{i.count} page(s)</span>
                </summary>
                <div className="seo-issue__fix">{i.fix}</div>
                {i.pages.map((p) => <div key={p} className="seo-brief__line">{p}</div>)}
              </details>
            ))}
          </>
        )}
      </div>

      <div className="mr-section">
        <h3 className="mr-section__title">Pre-publish draft check</h3>
        <div className="seo-form__row">
          <input className="seo-input seo-input--grow" placeholder="Target keyword the draft is written for"
                 value={draftKw} onChange={(e) => setDraftKw(e.target.value)} />
          <button className="seo-btn seo-btn--primary" disabled={!draft.trim() || !draftKw.trim()}
                  onClick={() => void score()}>
            Score draft
          </button>
        </div>
        <textarea className="seo-input seo-textarea" placeholder="Paste the draft (markdown headings help)…"
                  value={draft} onChange={(e) => setDraft(e.target.value)} />
        {scored && (
          <div className="seo-scorecard">
            <div className="seo-audit__score">
              <span className="seo-audit__num">{scored.score}</span>
              <span className={`seo-chip seo-chip--verdict-${scored.verdict.replace(" ", "-")}`}>{scored.verdict}</span>
              <span className="seo-lab__meta">{fmt(scored.word_count)} words</span>
            </div>
            {scored.checks.map((c) => (
              <div key={c.name} className={`seo-check${c.ok ? " seo-check--ok" : ""}`}>
                <Icon name={c.ok ? "check" : "x"} size={14} />
                <span className="seo-check__name">{c.name}</span>
                <span className="seo-check__note">{c.note}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* --------------------------- Decay update plan ---------------------------- */

export function UpdatePlanButton({ brandId, page, onToast }: {
  brandId: string; page: string; onToast: ToastFn;
}) {
  const [plan, setPlan] = useState<SeoUpdatePlan | null>(null);
  const [busy, setBusy] = useState(false);

  async function get() {
    setBusy(true);
    onToast("Comparing the page against what outranks it…");
    try {
      setPlan(await seoUpdatePlan(brandId, page));
    } catch (e) {
      onToast(errMsg(e, "Could not build the update plan"), "error");
    } finally {
      setBusy(false);
    }
  }

  if (plan) {
    return (
      <div className="seo-plan">
        <span className="seo-stat__label">Update plan — “{plan.query}”</span>
        {plan.suggestions.map((s, i) => <div key={i} className="seo-brief__line">{s}</div>)}
      </div>
    );
  }
  return (
    <button className="seo-btn" disabled={busy} onClick={() => void get()}>
      What should we update?
    </button>
  );
}
