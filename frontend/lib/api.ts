/**
 * API client for the AgentOS backend (FastAPI on Cloud Run).
 *
 * Auth: a Google ID token is exchanged for an app JWT, stored client-side and
 * sent as a Bearer token on every request. A 401 triggers the registered
 * unauthorized handler (so the app can log the user out cleanly).
 */

import {
  createDeadline, deadlineFor, RequestTimeoutError,
  type Deadline, type RequestOptions,
} from "./requestPolicy";

export { isAbortError, isTimeoutError, NO_TIMEOUT, RequestSequence, RequestTimeoutError } from "./requestPolicy";
export type { RequestOptions, RequestTicket } from "./requestPolicy";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8080";

/* ------------------------------ Auth plumbing ---------------------------- */

let authToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

/** A non-2xx reply, carrying the status alongside the message.
 *
 *  Still a plain `Error` with the backend's own `detail` as its message, so the
 *  ~150 call sites that read `e.message` are untouched. What it adds is the one
 *  thing that string could not carry: a *refusal* (the backend saying "valid
 *  request, but no — here is why") and a *failure* (something broke) look
 *  identical as prose, and telling them apart by matching on that prose is how
 *  a reworded sentence silently turns one into the other. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

/** The HTTP status behind a rejection, or null when it never got that far — a
 *  network drop, a timeout, an abort. */
export const apiStatus = (e: unknown): number | null =>
  e instanceof ApiError ? e.status : null;

async function parseError(response: Response): Promise<string> {
  try {
    const data = await response.json();
    return typeof data?.detail === "string" ? data.detail : "Request failed";
  } catch {
    return `Request failed (${response.status})`;
  }
}

/** The single choke point for every call in this file, so the deadline and the
 *  cancellation signal only have to be got right once.
 *
 *  Without a signal, a backend that never answers (blocked on an untimed
 *  upstream call) left this promise pending for the life of the tab: every
 *  `finally { setBusy(false) }` in the app is unreachable in that state and the
 *  UI reads "Working…" for ever. The deadline comes from `requestPolicy`, which
 *  gives model/crawl/render endpoints minutes and everything else 90 seconds;
 *  pass `timeoutMs` in `opts` to override it for one call.
 *
 *  The two ways a call can be cut short are kept distinct on purpose: our own
 *  deadline throws `RequestTimeoutError` (a real failure, show it), while a
 *  caller's `signal` rejects with the plain `AbortError` that `isAbortError()`
 *  recognises (a supersession or an unmount — say nothing). */
async function send(
  path: string,
  init: RequestInit,
  opts: RequestOptions,
): Promise<{ response: Response; deadline: Deadline; timedOut: () => RequestTimeoutError }> {
  const headers = new Headers(init.headers ?? {});
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  const deadline = createDeadline(path, init.method, opts);
  const timedOut = () => new RequestTimeoutError(deadlineFor(path, init.method, opts.timeoutMs));
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers, signal: deadline.signal });
  } catch (e) {
    deadline.clear();
    // `expired` is the only way to tell our timer's abort from the caller's:
    // both surface as the same AbortError out of fetch.
    if (deadline.expired) throw timedOut();
    throw e;
  }
  if (response.status === 401) {
    deadline.clear();
    onUnauthorized?.();
    throw new Error("Your session expired — please sign in again.");
  }
  return { response, deadline, timedOut };
}

/** Returns the raw `Response` for a body we *stream* rather than parse. The
 *  deadline covers up to the response headers only; whoever reads the body owns
 *  that part — which is right for a multi-megabyte PDF or image download that
 *  legitimately takes longer than any deadline, and wrong for everything else.
 *
 *  `fetchBlob` below is its only caller, deliberately: 22 call sites used to
 *  reach for this and hand-roll the rest of `requestJson`, and 11 of them were
 *  parsing JSON with the deadline already disarmed — a reply that stalled after
 *  the headers hung for the life of the tab. Reach for a verb, not for this. */
async function request(
  path: string,
  init: RequestInit = {},
  opts: RequestOptions = {},
): Promise<Response> {
  const { response, deadline } = await send(path, init, opts);
  deadline.clear();
  return response;
}

/** The JSON path — the shape ~150 of the functions below use. Here the deadline
 *  stays armed until the body has been read, so a reply that stalls halfway
 *  through fails like any other timeout instead of hanging. */
async function requestJson<T>(
  path: string,
  init: RequestInit,
  opts?: RequestOptions,
): Promise<T> {
  const { response, deadline, timedOut } = await send(path, init, opts ?? {});
  try {
    if (!response.ok) throw new ApiError(await parseError(response), response.status);
    return (await response.json()) as T;
  } catch (e) {
    if (deadline.expired) throw timedOut();
    throw e;
  } finally {
    deadline.clear();
  }
}

/* The verbs. Every call in this file goes through one of these, so the deadline
 * rules, the auth header, the 401 handling and the error shape are decided once
 * here instead of being re-typed (and half-remembered) at the call site. */

async function getJson<T>(path: string, opts?: RequestOptions): Promise<T> {
  return requestJson<T>(path, {}, opts);
}

async function postJson<T>(path: string, body: unknown, opts?: RequestOptions): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, opts);
}

async function putJson<T>(path: string, body: unknown, opts?: RequestOptions): Promise<T> {
  return requestJson<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, opts);
}

/** DELETE. Every delete endpoint this backend exposes answers 200 with a small
 *  JSON body (`{deleted: id}` and friends) rather than 204, so the reply is read
 *  like any other — which is what keeps the deadline armed through it. Callers
 *  that don't care what came back just ignore the result. */
async function deleteJson<T>(path: string, opts?: RequestOptions): Promise<T> {
  return requestJson<T>(path, { method: "DELETE" }, opts);
}

/** Multipart upload with a JSON reply. No `Content-Type` header on purpose: the
 *  browser must set it itself so the multipart boundary matches the body. */
async function sendForm<T>(path: string, form: FormData, opts?: RequestOptions): Promise<T> {
  return requestJson<T>(path, { method: "POST", body: form }, opts);
}

/** A body we stream instead of parsing — an artifact, a font, a report PDF, the
 *  extension bundle. This is the one place `request()` is called: the deadline
 *  is released once the headers land, so a large download is never cut off
 *  mid-stream by a timer meant for a slow *server*, not a slow *transfer*. */
async function fetchBlob(
  path: string,
  init: RequestInit = {},
  opts?: RequestOptions,
): Promise<Blob> {
  const response = await request(path, init, opts);
  // `ApiError`, not a bare `Error`, for the same reason `requestJson` throws
  // one: a document route that is not deployed yet answers 404 with FastAPI's
  // own "Not Found", and a caller that can read the status can say that in
  // words instead of printing two vague ones. Still an `Error`, so the call
  // sites that only read `.message` are untouched.
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.blob();
}

/** `fetchBlob` as an object URL, for an `<img src>` or an `<a download>` click.
 *  Callers own the URL and should revoke it when they're done with it. */
async function blobUrl(
  path: string,
  init?: RequestInit,
  opts?: RequestOptions,
): Promise<string> {
  return URL.createObjectURL(await fetchBlob(path, init, opts));
}

/* --------------------------------- Types --------------------------------- */

export interface User {
  id: string;
  email: string;
  name: string;
  picture: string;
  is_admin: boolean;
  // Top-tier owner role (above Super Admin): may manage secrets/integrations.
  is_creator?: boolean;
  /** May shape the shared brand registry from the GEO panel: add a brand,
   *  switch its scheduled check, remove it. A creator is one implicitly, and
   *  the backend derives that — never re-derive it here or the two copies drift.
   *
   *  Optional because a session signed in before the backend carried the flag
   *  has a stored user without it; absent must read as "not an editor", which
   *  hides the controls rather than offering ones that would 403. */
  is_geo_editor?: boolean;
}

export interface GalleryItem {
  file_name: string;
  file_type: string;
  view_url: string;
  is_image: boolean;
}

export interface LibraryBrand {
  id: string;
  brand_name: string;
  creative_count: number;
  creatives: GalleryItem[];
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  picture: string;
  provider: string;
  created_at: string;
  last_login: string;
}

export interface MonthlyStat {
  month: string;
  count: number;
  by_brand: Record<string, number>;
}

export interface Analytics {
  total_requests: number;
  monthly: MonthlyStat[];
  by_brand: Record<string, number>;
  by_category: Record<string, number>;
}

/* --------------------------------- Auth ---------------------------------- */

/** The one call that deliberately does NOT use the transport above, and must
 *  stay that way.
 *
 *  `send()` treats every 401 as "your session expired" — it fires the
 *  unauthorized handler (which logs you out) and replaces the server's message
 *  with a generic one. That is right for the ~150 calls made *with* a session
 *  and wrong for the one call made to *get* one: this endpoint answers 401 with
 *  the actual reason a sign-in was refused ("Invalid Google credential: …",
 *  "Google account email is not verified"), and the person at the login screen
 *  needs to read that, not "please sign in again" while already signing in.
 *  Routing it through `postJson` would swallow the only useful thing the server
 *  said. The missing deadline is a smaller cost than a lying error message —
 *  fixing it means giving `send()` a no-401-handling mode, which is an auth
 *  change, not a refactor. */
export async function googleLogin(
  credential: string,
): Promise<{ token: string; user: User }> {
  // Send the browser's timezone so the backend can stamp run rows with local
  // time (falls back to UTC server-side if unavailable).
  let timezone = "";
  try {
    timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    timezone = "";
  }
  const response = await fetch(`${API_URL}/api/auth/google`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credential, timezone }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as { token: string; user: User };
}

/** Sign in without Google — the standalone extract's local-dev door.
 *
 *  Added by this extract, not present in the parent repo. The backend refuses
 *  it with 404 unless it is running with `LOCAL_DEV_AUTH=1` *and*
 *  `APP_ENV=development`, and it applies the same sign-in allowlist the Google
 *  path does, so this can only ever widen how you prove an address — never
 *  which addresses are allowed. `LoginScreen` only offers it when
 *  `NEXT_PUBLIC_LOCAL_DEV_AUTH` is "1".
 *
 *  Deliberately shaped like `googleLogin` above (bare `fetch`, same return, no
 *  `send()`) so the two sign-in paths read as one thing with two doors. */
export async function devLogin(
  email: string,
): Promise<{ token: string; user: User }> {
  let timezone = "";
  try {
    timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    timezone = "";
  }
  const response = await fetch(`${API_URL}/api/auth/dev`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, timezone }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as { token: string; user: User };
}

/* ------------------------------ Library / data --------------------------- */

export async function loadLibrary(perBrand = 24, opts?: RequestOptions): Promise<LibraryBrand[]> {
  const data = await getJson<{ brands: LibraryBrand[] }>(
    `/api/library?per_brand=${perBrand}`,
    opts,
  );
  return data.brands;
}

/* --------------------------------- Admin --------------------------------- */

export async function getAdminUsers(): Promise<{ users: AdminUser[]; total: number }> {
  return getJson("/api/admin/users");
}

export async function getAdminAnalytics(): Promise<Analytics> {
  return getJson("/api/admin/analytics");
}

// Admin-only runtime settings (OpenRouter key + model ids). The key is never
// returned in full — only a masked hint and whether it's set.
export interface AdminSettings {
  openrouter: {
    api_key_set: boolean;
    api_key_hint: string;
    api_key_source: "override" | "env" | "unset";
    model: string;
    fast_model: string;
    image_model: string;
    vision_model: string;
  };
  sources: Record<string, "override" | "env">;
  // Secret API keys (OpenRouter + GEO engine keys), masked with provenance.
  keys: Record<string, { set: boolean; hint: string; source: "override" | "env" | "unset" }>;
  // Curated model choices, keyed by runtime-config field, for dropdowns.
  catalog: Record<AgentModelField, ModelOption[]>;
}

export interface AdminSettingsPatch {
  openrouter_api_key?: string;
  openrouter_model?: string;
  openrouter_fast_model?: string;
  openrouter_image_model?: string;
  openrouter_vision_model?: string;
  perplexity_api_key?: string;
  gemini_api_key?: string;
  openai_api_key?: string;
}

export function getAdminSettings(): Promise<AdminSettings> {
  return getJson("/api/admin/settings");
}

export function updateAdminSettings(patch: AdminSettingsPatch): Promise<AdminSettings> {
  return postJson("/api/admin/settings", patch);
}

export function testOpenRouterKey(): Promise<{ ok: boolean; label?: string; is_free_tier?: boolean }> {
  return postJson("/api/admin/settings/test", {});
}

/* ----------------------------- Image library ----------------------------- */
/* Admin-only gallery: the final creative of every COMPLETED Graphics Designer
   run, archived to GCS at Stage-4 approval and listed newest-first. */

export interface ImageLibraryItem {
  run_id: string;
  user_id: string;
  user_email: string;
  brand: string | null;
  brand_id: string | null;
  summary: string;
  headline: string;
  aspect_ratio: string | null;
  completed_at: string;
  /** A fresh signed GCS URL, or an API proxy path (starts with "/api/"). */
  view_url: string;
}

export function getImageLibrary(limit = 200): Promise<{ items: ImageLibraryItem[]; total: number }> {
  return getJson(`/api/admin/image-library?limit=${limit}`);
}

/** Proxy-served gallery images need the Bearer header, so fetch as a blob and
 *  hand back an object URL (callers should revoke it on unmount). */
export async function imageLibraryBlob(path: string): Promise<string> {
  return blobUrl(path);
}

/* --------------------------- Database viewer ----------------------------- */
/* Admin-only, read-only inspection of the Firestore collections, rendered as  */
/* tables — so the team can see the data really living in the database.        */

export interface DbCollection {
  name: string;
  label: string;
  description: string;
  // null = the count couldn't be read (database unreachable), not "empty".
  count: number | null;
  // Deep link to this collection in the Firebase console.
  console_url?: string;
}

export interface DbCollectionsResponse {
  collections: DbCollection[];
  connected: boolean;
  database: string;
  project: string;
  // Deep link to the database root in the Firebase console.
  console_url?: string;
}

export interface DbCollectionData {
  name: string;
  label: string;
  description: string;
  count: number | null;
  returned: number;
  limit: number;
  columns: string[];
  rows: Record<string, unknown>[];
}

export function getDbCollections(opts?: RequestOptions): Promise<DbCollectionsResponse> {
  return getJson("/api/admin/db/collections", opts);
}

export function getDbCollection(
  name: string,
  limit = 50,
  opts?: RequestOptions,
): Promise<DbCollectionData> {
  return getJson(`/api/admin/db/collections/${encodeURIComponent(name)}?limit=${limit}`, opts);
}

// Delete the superseded telemetry collections (creative_events, sessions,
// requests, conversations). Requires confirm === "DELETE". Operational data is
// never touched.
export function purgeTelemetry(
  confirm: string,
): Promise<{ deleted: Record<string, number>; kept: string }> {
  return postJson("/api/admin/db/purge-telemetry", { confirm });
}

/* ------------------------------ The record ------------------------------- */
/* Every run the signed-in caller has filed, across every agent. Backed by
   GET /api/runs, which reads the `runs` collection scoped to their user id.

   Two fields are absent on purpose and the console must not invent them: a run
   carries no token count and no cost, because the activity trail was never
   asked to record either. `took_seconds` is null for an append-only row, which
   is stamped once — a duration of zero would read as an instant run.          */

export type RunState = "done" | "running" | "queued" | "failed";

export interface RunRow {
  id: string;
  run_id: string;
  agent_id: string;
  agent_name: string;
  brand: string | null;
  brand_id: string | null;
  action: string;
  title: string;
  state: RunState;
  /** Exactly what the backend stored, for the opened row. */
  status_raw: string;
  created_at: string;
  updated_at: string;
  day: string;
  took_seconds: number | null;
  /** The picture the run made, when it made one. */
  image: string | null;
  user: string;
}

export interface RunsPage {
  runs: RunRow[];
  /** Every run this caller has ever filed. `null` when the count could not be
   *  read — never rendered as 0, which would claim they had never run one. */
  total: number | null;
  /** How many of the newest rows the filter and the facets looked at. */
  scanned: number;
  scan_limit: number;
  /** False when the read hit its cap, so the panel can say its counts are of a
   *  window rather than of the whole record. */
  window_complete: boolean;
  facets: {
    agents: { id: string; name: string; count: number }[];
    brands: { name: string; count: number }[];
    states: Partial<Record<RunState, number>>;
  };
  /** One seven-day window, shared by every figure that says "this week" — so a
   *  headline can never disagree with the list beneath it. */
  week: {
    from: string;
    done: number;
    running: number;
    queued: number;
    failed: number;
    total: number;
    by_agent: { id: string; name: string; count: number }[];
  };
  live: { running: number; queued: number };
}

export interface RunsQuery {
  limit?: number;
  agent?: string;
  state?: string;
  brand?: string;
  q?: string;
}

export function listRuns(query: RunsQuery = {}, opts?: RequestOptions): Promise<RunsPage> {
  const qs = new URLSearchParams();
  if (query.limit) qs.set("limit", String(query.limit));
  if (query.agent && query.agent !== "all") qs.set("agent", query.agent);
  if (query.state && query.state !== "all") qs.set("state", query.state);
  if (query.brand && query.brand !== "all") qs.set("brand", query.brand);
  if (query.q) qs.set("q", query.q);
  const tail = qs.toString();
  return getJson<RunsPage>(`/api/runs${tail ? `?${tail}` : ""}`, opts);
}

/* ---------------------------- News banner -------------------------------- */
/* A single announcement set by the creator; shown to every signed-in user.   */

export interface NewsBanner {
  text: string;
  updated_at: string;
}

export function getNews(): Promise<NewsBanner> {
  return getJson<NewsBanner>("/api/news");
}

export function updateNews(text: string): Promise<NewsBanner> {
  return postJson<NewsBanner>("/api/news", { text });
}

/* --------------------- Agent configuration (creator) --------------------- */
/* Per-agent model overrides, managed only by the creator account.          */

export interface ModelOption {
  id: string;
  name: string;
  provider: string;
  /** Curated entries carry one; live OpenRouter entries may not. */
  description?: string;
  recommended?: boolean;
  /** Quality tier for dropdown grouping: "flagship" | "balanced" | "fast". */
  tier?: string;
}

/** A model field that can be overridden per agent (matches backend slugs). */
/** Must stay in step with AGENT_OVERRIDE_FIELDS in the backend's
 *  app/services/runtime_config.py. A field the backend serves but this union
 *  omits is dropped silently by AgentConfigView's FIELD_ORDER filter, so the
 *  control simply never renders — add new fields to both. */
export type AgentModelField =
  | "openrouter_model"
  | "openrouter_fast_model"
  | "openrouter_image_model"
  | "openrouter_vision_model"
  | "gd_planner_model"
  | "gd_polish_image_model";

export interface AgentConfigItem {
  id: string;
  name: string;
  role: string;
  category: string;
  live: boolean;
  /** The model fields this agent actually consumes — the only dropdowns shown. */
  fields: AgentModelField[];
  /** Explicit per-agent choice ("" = inherit the global default). Keys ⊆ fields. */
  overrides: Partial<Record<AgentModelField, string>>;
  /** What the agent actually uses right now (agent → global → env). Keys ⊆ fields. */
  effective: Partial<Record<AgentModelField, string>>;
}

export interface AgentConfigResponse {
  agents: AgentConfigItem[];
  fields: AgentModelField[];
  catalog: Record<AgentModelField, ModelOption[]>;
  /** Display labels for the tier groups, keyed by tier slug. */
  tier_labels?: Record<string, string>;
  global_defaults: Record<AgentModelField, string>;
}

export type AgentConfigPatch = Partial<Record<AgentModelField, string>>;

export function getAgentConfig(opts?: RequestOptions): Promise<AgentConfigResponse> {
  return getJson("/api/admin/agents", opts);
}

export function updateAgentConfig(
  agentId: string,
  patch: AgentConfigPatch,
): Promise<AgentConfigResponse> {
  return postJson(`/api/admin/agents/${agentId}`, patch);
}

/* ----------------------- Graphic Designer pipeline ----------------------- */
/* The 4-stage ad-creative pipeline (backend: graphics_designer_agent).      */

export interface GdDiff {
  token: string;
  find: string;
  replace: string;
  count: number;
}

export interface GdAttempt {
  attempt: number;
  variant: string;
  artifact: string;
  url: string;
  prompt?: string;
  prompt_hash?: string;
  diffs?: GdDiff[];
  warnings?: string[];
  provider?: string;
  method?: string;
  // Honest per-generation remix metadata: ai=true only for a real LLM rewrite.
  remix?: { ai: boolean; axis: string; fallback_reason?: string };
  // Stage-3 Text Optimizer: one attempt per style, sharing a set_id. ai=true
  // only when the image really came from the model; fallbacks carry the reason.
  style?: string;
  style_label?: string;
  ai?: boolean;
  fallback_reason?: string | null;
  qa?: string;
  set_id?: string;
  fonts?: Record<string, string>;
  // Step 5: the user's retouch request this attempt was generated from.
  tweak_instruction?: string;
  created_at: string;
}

export interface GdApproved {
  attempt: number;
  variant: string;
  artifact: string;
  url: string;
}

export interface GdStage {
  variant: string | null;
  attempts: GdAttempt[];
  approved: GdApproved | null;
}

export interface GdRun {
  id: string;
  user_id: string;
  brand_id: string | null;
  state: string;
  config: {
    font: string;
    aspect_ratio: string;
    text_placement?: string;
    cta_placement?: string;
    element_placement?: string;
    element_styles?: Record<string, GdElementStyle>;
    subheadings?: GdSubheading[];
    // Stage-3 free-drag coordinates per element id (headline / subheading-N / cta).
    layout?: Record<string, GdLayoutEntry>;
    shapes?: GdShape[];
    // Canva-style free elements (emoji / icon / sticker / uploaded image).
    elements?: GdElement[];
    logo_layout?: GdLogoLayout;
    custom_gradient?: GdCustomGradient | null;
    custom_element?: GdCustomElement | null;
    subject_asset_ref?: string | null;
    background_asset_ref?: string | null;
    remix_enabled?: boolean;
    creative_brief?: Record<string, string>;
    creative_type?: string;
    // Stage-3 Text Optimizer: free-text placement/style notes for the polish prompts.
    polish_notes?: string;
    use_ai_compositor: boolean;
    tokens: Record<string, string>;
    tokens_approved: Record<string, boolean>;
  };
  stages: Record<string, GdStage>;
  logo: { artifact: string } | null;
  manifest_log: Record<string, unknown>[];
  tokens_ready: boolean;
  created_at: string;
  updated_at: string;
}

// Per-element Stage-3 styling for the deterministic renderer. `placement`,
// `size_pct` and the pixel nudge are omitted for the inline highlight (it follows
// the headline); `color` is omitted for the CTA (its orange button is locked).
// `size_pct` is the element's size as a % of the canvas width; `offset_x`/`offset_y`
// are a pixel nudge from the placement anchor.
export interface GdElementStyle {
  font?: string;
  color?: string;
  placement?: string;
  align?: string; // "left" | "center" | "right" — line alignment inside the box
  size_pct?: number;
  offset_x?: number;
  offset_y?: number;
}

// Stage-3 free-drag coordinate for one element. x/y ∈ [0,1] place the element's
// `anchor` point on the canvas; w ∈ (0,1] is its max width as a fraction of width.
export interface GdLayoutEntry {
  x: number;
  y: number;
  w: number;
  anchor: string;
}

// One Stage-3 shape / infographic element (rect, circle, triangle, arrow,
// divider, callout, or a named icon). Positioned by absolute coords like text.
export interface GdShape {
  id: string;
  kind: string;
  x: number;
  y: number;
  w: number;
  h: number;
  anchor: string;
  fill: string;
  stroke: string | null;
  stroke_w: number;
  radius: number;
  icon: string | null;
  text: string;
  z: number;
}

// One Stage-3 sub-heading line (the dynamic 1–5 list, replacing the old fixed
// subtext1/subtext2). Each line is independently styled, placed and approved.
export interface GdSubheading {
  text: string;
  font?: string;
  color?: string;
  align?: string;
  size_pct?: number;
  placement?: string;
  offset_x?: number;
  offset_y?: number;
  approved?: boolean;
}

// One row of the emoji catalogue served by GET /api/gd/elements.
export interface EmojiRow {
  char: string;
  name: string;
  category: string;
  file: string;
}

// One Stage-3 free element (emoji / icon / sticker / uploaded image). Positioned
// by absolute coords like shapes; `ref` identifies the asset (emoji char, icon
// key, sticker key, or an uploaded-image ref from gdElementUpload).
export interface GdElement {
  id: string;
  kind: "emoji" | "icon" | "sticker" | "image";
  x: number;
  y: number;
  w: number;
  h: number;
  anchor: string;
  z: number;
  rotation: number;
  opacity: number;
  ref: string;
  fill: string;
}

// Stage-4 logo placement controls (deterministic compositor).
export interface GdLogoLayout {
  position: string;
  size_pct: number | null;
  margin_pct: number;
  offset_x: number;
  offset_y: number;
}

export interface GdStage3Element {
  key: string;
  label: string;
  token: string;
  placeable: boolean;
  colorable: boolean;
  sizable: boolean;
  placement_kind: "text" | "cta";
}

export interface GdTextColor {
  key: string;
  label: string;
  swatch: string;
  phrase: string;
}

export interface GdVariant {
  id: string;
  title: string;
  desc: string;
  angle?: string;
  category?: string;
  css_gradient?: string;
  subject?: string; // Stage-2 element variants supply this instead of a prompt file
  prompt_file?: string; // Stage-1 variants only
}

// A per-creative, temporary AI gradient (Stage 1). Lives on the run config only —
// never added to the canonical prompt library. Selected with variant id "AI".
export interface GdCustomGradient {
  id: string; // always "AI"
  cid?: string; // curated/llm id, used to exclude already-seen picks on regenerate
  title: string;
  desc: string;
  prompt: string;
  css_gradient: string;
  source?: string; // "agent" | "agent+llm"
}

export interface GdGradientSuggestion {
  type: "gradient";
  state: "proposed";
  source: string;
  ai: boolean;
  gradient: GdCustomGradient;
  note: string;
  // Set when the LLM path failed and a curated brand preset was served instead
  // (ai is false) — surfaced to the user so a preset is never passed off as AI.
  fallback_reason?: string;
}

// A per-creative, temporary AI element (Stage 2). Lives on the run config only —
// never added to the catalogue. Selected with variant id "AI".
export interface GdCustomElement {
  id: string; // always "AI"
  cid?: string;
  title: string;
  desc: string;
  category: string;
  subject: string;
  source?: string; // "agent" | "agent+llm"
}

export interface GdElementSuggestion {
  type: "element";
  state: "proposed";
  source: string;
  ai: boolean;
  element: GdCustomElement;
  note: string;
}

export interface GdExplorePick {
  id: string;
  title: string;
  category: string;
  reason: string;
}

export interface GdExplore {
  type: "explore";
  ai: boolean;
  picks: GdExplorePick[];
  wildcard: GdExplorePick | null;
  idea: string;
  note: string;
}

export interface GdAspectRatio {
  ar: string;
  label: string;
  dimensions: string;
  w: number;
  h: number;
  orientation: string;
  default: boolean;
}

export interface GdQuestion {
  id: string;
  question: string;
  options: { id: string; label: string }[];
}

// One turn of the pre-generation discovery conversation (Steps 1–2). `kind`
// drives rendering: "choice" = chips only, "text" = free text only,
// "choice_text" = chips with a free-text override.
export interface GdDiscoveryQuestion {
  id: string;
  group: "intent" | "context";
  kind: "choice" | "text" | "choice_text";
  prompt: string;
  options?: { id: string; label: string }[];
  placeholder?: string;
  optional?: boolean;
}

// One message in the strategist conversation (agent ⇄ user).
export interface GdChatMessage {
  role: "agent" | "user";
  text: string;
}

// One agent turn returned by the conversational strategist (kind:"chat").
export interface GdChatTurn {
  type: "chat";
  state: string;
  source: string;
  reply: string;
  brief: Record<string, string>;
  done: boolean;
  direction: GdDirection | null;
}

// The synthesized creative direction returned after the discovery conversation.
export interface GdDirection {
  type: "direction";
  state: string;
  source: string;
  summary: string;
  concept: string;
  concept_title: string;
  concept_rationale: string;
  tone: string;
  palette_hint: string;
  copy_angle: string;
  highlights: string[];
}

export interface GdBrandOption {
  id: string;
  name: string;
}

export interface GdConfig {
  brand_id: string;
  brand_name: string;
  stage1_variants: GdVariant[];
  stage2_variants: GdVariant[];
  stage2_categories: string[];
  stage2_placements: { key: string; label: string; row: number; col: number }[];
  fonts: string[];
  font_family: string;
  font_variants: { name: string; weight: number; style: string; file: string }[];
  text_placements: { key: string; label: string; phrase: string }[];
  cta_placements: { key: string; label: string; phrase: string }[];
  text_colors: GdTextColor[];
  stage3_elements: GdStage3Element[];
  text_size_pct_min: number;
  text_size_pct_max: number;
  default_text_size_pct: Record<string, number>;
  text_offset_px_range: number;
  subheading_min: number;
  subheading_max: number;
  anchors: string[];
  shape_kinds: string[];
  icon_keys: string[];
  logo_positions: { key: string; label: string; row: number; col: number }[];
  logo_size_pct_min: number;
  logo_size_pct_max: number;
  logo_offset_px_range: number;
  aspect_ratios: GdAspectRatio[];
  brand_kit_block: string;
  locked_colors: {
    gradient: string[];
    text: string;
    accent: string;
    headline_highlight: { from: string; to: string; direction: string };
    cta: { from: string; to: string; direction: string; shadow: string };
  };
  stage1_source_note: string;
  onboarding_questions: GdQuestion[];
  discovery_questions: GdDiscoveryQuestion[];
  content_tokens: string[];
}

export interface GdHookSuggestion {
  headlines: { headline: string; highlight: string }[];
  ctas: { cta: string }[];
  subtext_pairs: { subtext1: string; subtext2: string }[];
}

/** Brands the studio can produce for (registry packs) — drives the picker. */
export const gdListBrands = () =>
  getJson<{ brands: GdBrandOption[]; default: string }>("/api/gd/brands");

/** Brands whose kit data has been ingested — the setup-screen readiness strip. */
export interface GdIngestedBrand {
  id: string;
  name: string;
  logo_url: string | null;
  primary_colors: string[];
  counts: { fonts: number; logos: number; reference_assets?: number };
  source?: string | null;
}

export const gdIngestedBrands = (opts?: RequestOptions) =>
  getJson<{ brands: GdIngestedBrand[] }>("/api/gd/ingested-brands", opts);

const _brandQuery = (brand?: string | null) =>
  brand ? `?brand=${encodeURIComponent(brand)}` : "";

export const gdGetConfig = (brand?: string | null) =>
  getJson<GdConfig>(`/api/gd/config${_brandQuery(brand)}`);

export const gdCreateRun = (
  brandId?: string | null,
  init?: { aspect_ratio?: string; creative_type?: string; creative_brief?: Record<string, string>; remix_enabled?: boolean },
) => postJson<GdRun>("/api/gd/runs", { brand_id: brandId ?? null, ...(init ?? {}) });

/* ---------------- Brand Reference Library (ingestion + retrieval) --------- */
// Test/debug surface so a human can SEE which reference creatives the agent
// picks up for a given brand + creative type + brief. Backed by /api/ref-library.

export interface RefRecord {
  id: string;
  brand_id: string;
  brand_name: string;
  creative_type: string;
  file_name: string;
  width: number;
  height: number;
  aspect_ratio: string;
  orientation: string;
  format_match: boolean;
  palette: string[];
  tags: string[];
  summary: string;
  source: string;
  _score?: number;
  _why?: string[];
}

export interface RefDriveSyncResult {
  source: string;
  folder_id: string;
  downloaded: number;
  ingested: number;
  mirrored_to_gcs: number;
  by_type: Record<string, number>;
  skipped_folders: string[];
}

/** Pull on-brand reference creatives from the shared Google Drive folder into
 *  the library (admin/creator only). Backed by POST /api/ref-library/sync-drive. */
export const gdRefSyncDrive = (useLlm = false) =>
  postJson<RefDriveSyncResult>(`/api/ref-library/sync-drive?use_llm=${useLlm}`, {});

export const gdUpdateConfig = (
  id: string,
  body: {
    font?: string;
    aspect_ratio?: string;
    text_placement?: string;
    cta_placement?: string;
    element_placement?: string;
    element_styles?: Record<string, GdElementStyle>;
    subheadings?: GdSubheading[];
    layout?: Record<string, GdLayoutEntry | null>;
    shapes?: GdShape[];
    elements?: GdElement[];
    logo_layout?: Partial<GdLogoLayout>;
    custom_gradient?: GdCustomGradient | null;
    custom_element?: GdCustomElement | null;
    subject_asset_ref?: string | null;
    background_asset_ref?: string | null;
    remix_enabled?: boolean;
    creative_brief?: Record<string, string>;
    polish_notes?: string;
    use_ai_compositor?: boolean;
    tokens?: Record<string, string>;
    token_approvals?: Record<string, { approved: boolean; source?: string; original_suggestion?: string }>;
  },
) => postJson<GdRun>(`/api/gd/runs/${id}/config`, body);

// Stage 3 with the Text Optimizer returns the brand_strict attempt as `attempt`
// plus ALL styled siblings in `attempts` (absent on single-attempt generates).
export const gdGenerate = (id: string, stage: number, variant?: string) =>
  postJson<{ attempt: GdAttempt; attempts?: GdAttempt[]; run: GdRun }>(
    `/api/gd/runs/${id}/generate`, { stage, variant });

// AI Suggest Placement — vision-first: a micro-subagent looks at the approved
// Stage-2 image and judges zone / text colour / density; the arranger computes
// exact coords ("source": "vision"). Falls back to the metadata-only arranger
// ("source": "deterministic"). The caller applies it via gdUpdateConfig or
// discards. Does not persist server-side.
export type GdPlacementSuggestion = {
  layout: Record<string, GdLayoutEntry>;
  shapes?: GdShape[];
  element_styles?: Record<string, GdElementStyle>;
  text_color?: string;
  source?: "vision" | "deterministic";
  reason?: string;
};
export const gdSuggestPlacement = (id: string) =>
  postJson<GdPlacementSuggestion>(`/api/gd/runs/${id}/suggest-placement`, {});

export const gdApprove = (id: string, stage: number, attempt?: number) =>
  postJson<GdRun>(`/api/gd/runs/${id}/approve`, { stage, attempt });

// Step 5: guardrailed retouch of the approved final. Rejections surface the
// guardrail violations as the error message; nothing is stored server-side.
export const gdTweak = (id: string, instruction: string) =>
  postJson<{ attempt: GdAttempt; run: GdRun }>(`/api/gd/runs/${id}/tweak`, { instruction });

export const gdBack = (id: string, stage: number) =>
  postJson<GdRun>(`/api/gd/runs/${id}/back`, { stage });

export const gdSuggest = (id: string, body: Record<string, unknown>) =>
  postJson<Record<string, unknown>>(`/api/gd/runs/${id}/suggest`, body);

/** Auto-mode plan: the AI's picks for all four stages, validated server-side
 *  against the run's real pack inventory. */
export interface GdPlan {
  version: number;
  brief: string;
  concept: string;
  gradient: { cid: string; reason: string };
  element: { cid: string; reason: string };
  text: { headline: string; highlight: string; subline: string; cta: string; reason: string };
  logo: { logo_id: string | null; reason: string };
  layout?: GdPlanLayout;
}

/** Binding wireframe zones planned from the brief (spec 2026-07-14). */
export interface GdPlanLayout {
  subject_cell: string;
  headline_zone: string;
  sub_zone: string;
  cta_zone: string;
  logo_corner: string;
}

export const gdPlan = (id: string, brief: string) =>
  postJson<{ plan: GdPlan; run: GdRun }>(`/api/gd/runs/${id}/plan`, { brief });

export interface GdBrandLogoVariant {
  id: string;
  name: string;
  thumb: string; // data-URL thumbnail
}

export async function gdBrandLogos(id: string) {
  return getJson<{ logos: GdBrandLogoVariant[]; brand_name: string }>(
    `/api/gd/runs/${id}/brand-logos`,
  );
}

export async function gdStage4(
  id: string,
  logo: File | null,
  useAi: boolean,
  logoId?: string | null,
): Promise<{ attempt: GdAttempt; run: GdRun }> {
  const form = new FormData();
  // Omitting the file makes the backend fall back to the picked/brand logo.
  if (logo) form.append("logo", logo);
  form.append("use_ai", String(useAi));
  if (!logo && logoId) form.append("logo_id", logoId);
  return sendForm<{ attempt: GdAttempt; run: GdRun }>(`/api/gd/runs/${id}/stage4`, form);
}

/** Artifacts require the Bearer header, so fetch as a blob and hand back an
 *  object URL (callers should revoke it on unmount). */
export async function gdArtifactBlob(path: string): Promise<string> {
  return blobUrl(path);
}

/** Live Stage-3 overlay preview: renders the real (deterministic) text overlay
 *  at a small size and returns an object URL. `tokens`/`subheading_texts` carry
 *  the unsaved edits so the preview matches what Generate will produce. */
export async function gdTextPreview(
  id: string,
  body: { tokens?: Record<string, string>; subheading_texts?: string[] },
): Promise<string> {
  return blobUrl(`/api/gd/runs/${id}/text-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Stage-3 element catalogue (emoji / icon / sticker keys + the per-run cap). */
export const gdElements = () =>
  getJson<{ emoji: EmojiRow[]; icons: string[]; stickers: string[]; max_elements: number }>(
    "/api/gd/elements",
  );

/** Upload a custom image element for one run; returns a `ref` to use in a
 *  GdElement with kind "image". Multipart — no JSON Content-Type so the
 *  browser sets the boundary; auth header still comes from `sendForm()`. */
export async function gdElementUpload(runId: string, file: File): Promise<{ ref: string }> {
  const form = new FormData();
  form.append("file", file);
  return sendForm<{ ref: string }>(`/api/gd/runs/${runId}/elements/upload`, form);
}

/** Fetch one brand font file (validated server-side against the pack) as an
 *  object URL, for FontFace registration so the editor canvas shows TRUE
 *  brand typography. Callers should revoke the URL after the face loads. */
export async function gdFontBlob(name: string, brand?: string | null): Promise<string> {
  return blobUrl(
    `/api/gd/fonts/${encodeURIComponent(name)}${brand ? `?brand=${encodeURIComponent(brand)}` : ""}`,
  );
}

/** Upload an image for a run. role="subject"/"background": the deterministic
 *  composite modes — store the returned `ref` via gdUpdateConfig
 *  ({ subject_asset_ref } / { background_asset_ref }) and generate with variant
 *  "UPLOAD" (Pillow composite, no image model). role="prompt": an image the
 *  user attached WITH the brief — the backend appends it to
 *  config.prompt_image_refs (max 3, deduped) and Stage-1/2 AI generation
 *  manipulates/incorporates it as the brief directs. */
export async function gdSubjectUpload(
  runId: string,
  file: File,
  role: "subject" | "background" | "prompt" = "subject",
): Promise<{ ref: string }> {
  const form = new FormData();
  form.append("file", file);
  return sendForm<{ ref: string }>(`/api/gd/runs/${runId}/subject/upload?role=${role}`, form);
}

/* ---------------- Creative Agent (brochures / decks / carousels / blogs) --- */
// Standard social posts use the Graphics Studio editor (gd* above). Everything
// else routes to the dedicated Creative Agent: plan → review → generate a real
// PDF / PPTX / image set, manually or autonomously. Backed by /api/creative.

export interface CreativeTypeMeta {
  key: string;
  label: string;
  aspect_ratio: string;
  orientation: string;
  multi_frame: boolean;
  output_format: "image" | "image_set" | "pdf" | "pptx";
  notes: string;
  unit: string;
  default_count: number;
  min_count: number;
  max_count: number;
}

export interface CreativeStep {
  key: string;
  label: string;
  detail: string;
}

export interface CreativeDecision {
  step: string;
  decision: string;
  rationale: string;
  source: "agent" | "user";
  timestamp: string;
}

export interface CreativeArtifact {
  name: string;
  mime: string;
  ref: string;
  bytes: number;
  url: string;
}

export interface CreativePlan {
  creative_type: string;
  count: number;
  source: string;
  grounded: boolean;
  rationale: string;
  decisions: CreativeDecision[];
  frames?: { index: number; role: string; headline: string; body: string; visual: string }[];
  slides?: { index: number; title: string; bullets: string[]; notes: string }[];
  sections?: { heading: string; body: string; bullets: string[] }[];
  cover?: { title: string; subtitle?: string; visual?: string };
  inline?: { caption: string; visual: string }[];
  contact?: { line: string };
}

export interface CreativeRun {
  id: string;
  user_id: string;
  brand_id: string;
  brand_name: string;
  creative_type: string;
  output_format: string;
  /** Carousel only: "text" (per-slide copy) or "images_only" (image + logo). */
  text_mode?: "text" | "images_only";
  autonomous: boolean;
  autonomous_ack: boolean;
  state: "INTENT" | "STRATEGY" | "LAYOUT" | "OUTPUT" | "DONE";
  brief: string;
  intent: Record<string, unknown>;
  plan: CreativePlan | null;
  plan_approved: boolean;
  references: RefRecord[];
  grounding: string;
  decision_log: CreativeDecision[];
  artifacts: CreativeArtifact[];
  /** Live generation progress (present while/after Step 4 runs). */
  progress?: { done: number; total: number; state?: string };
}

export const creativeTypes = () =>
  getJson<{
    types: CreativeTypeMeta[];
    steps: CreativeStep[];
    autonomous_warning: string;
    engines: Record<string, boolean>;
  }>("/api/creative/types");

export const creativeCreate = (body: {
  creative_type: string;
  brand_id?: string | null;
  brief?: string;
  autonomous?: boolean;
  text_mode?: "text" | "images_only";
}) => postJson<CreativeRun>("/api/creative/runs", body);

export const creativeGet = (id: string) => getJson<CreativeRun>(`/api/creative/runs/${id}`);

export const creativeAcknowledge = (id: string) =>
  postJson<CreativeRun>(`/api/creative/runs/${id}/acknowledge`, {});

export const creativePlan = (id: string, body: { count?: number | null; use_llm?: boolean } = {}) =>
  postJson<CreativeRun>(`/api/creative/runs/${id}/plan`, body);

/** Carousel text mode: push the user's exact per-slide headline/sub-text into the
 *  plan before generation. */
export const creativeUpdatePlanText = (
  id: string,
  frames: { index: number; headline?: string; body?: string }[],
) => postJson<CreativeRun>(`/api/creative/runs/${id}/plan/text`, { frames });

export const creativeApprove = (id: string) =>
  postJson<CreativeRun>(`/api/creative/runs/${id}/plan/approve`, {});

export const creativeGenerate = (id: string) =>
  postJson<CreativeRun>(`/api/creative/runs/${id}/generate`, {});

export const creativeAutonomous = (
  id: string,
  body: { count?: number | null; use_llm?: boolean } = {},
) => postJson<CreativeRun>(`/api/creative/runs/${id}/autonomous`, body);

export const creativeOverride = (id: string) =>
  postJson<CreativeRun>(`/api/creative/runs/${id}/override`, {});

/** Download a produced artifact (PDF/PPTX/PNG/zip) with the auth header, as an
 *  object URL (callers should revoke it after triggering the download). */
export async function creativeArtifactBlob(url: string): Promise<string> {
  return blobUrl(url);
}

/* ----------------------- Marketing Research agent ------------------------ */
// Backed by /api/mr. Data enters via CSV export upload; reports render as HTML.

export type MrPlatform = "google_ads" | "meta" | "hubspot";

export const MR_REPORT_KINDS = [
  "daily_summary",
  "weekly_summary",
  "monthly_summary",
  "quarterly_summary",
  "threshold_alert",
  "competitor_digest",
  "opportunity_report",
  "utm_attribution",
  "icp_signal",
  "daily_movement",
] as const;
export type MrReportKind = (typeof MR_REPORT_KINDS)[number];

/** The two board kinds, deliberately kept out of `MR_REPORT_KINDS`.
 *
 *  Those ten are the campaign reports: one period, a model-written narrative,
 *  built one at a time at `POST /api/mr/reports/{kind}` — which answers 422 for
 *  a board kind and names the route that does build it. The board report takes
 *  **two** periods, is nobody's narrative (it is the roll-up tab's own
 *  arithmetic) and comes from `POST /api/mr/board-report`.
 *
 *  They do share the one run rail, so `GET /api/mr/runs` lists them and
 *  `GET /api/mr/runs/{id}` reads them back — which is why the run types below
 *  accept both, and why a panel that maps a run to a label has to handle a kind
 *  that is not one of the ten. */
export const MR_BOARD_KINDS = ["board_report", "board_report_comparison"] as const;
export type MrBoardKind = (typeof MR_BOARD_KINDS)[number];

/** Any kind the run rail can hand back. */
export type MrAnyReportKind = MrReportKind | MrBoardKind;

export const isBoardKind = (kind: string): kind is MrBoardKind =>
  (MR_BOARD_KINDS as readonly string[]).includes(kind);

export interface MrDataGap {
  source: string;
  message: string;
}

export interface MrIngestResult {
  dataset_id: string;
  platform: string; // MrPlatform for CSVs, "pdf:<filename>" for PDF uploads
  metrics: number;
  leads: number;
  gaps: MrDataGap[];
}

export interface MrDataset {
  id: string;
  platform: string;
  generated_at?: string | null;
  metrics: number;
  leads: number;
  gaps: MrDataGap[];
}

export interface MrConnector {
  key: string;
  label: string;
  logo: string | null;
  category: string;
  status: "connected" | "needs_setup" | "available";
  detail: string;
}

export interface MrConfig {
  spreadsheet_id: string;
  spreadsheet_url: string;
  year: number;
  competitors: { name: string; url: string }[];
  schedule: { report: string; cadence: string }[];
  thresholds: Record<string, number>;
}

export interface MrReport {
  id: string;
  kind: MrReportKind;
  generated_at: string;
  user_id: string;
  agent_id: string;
  sources?: MrSource[];
  structured: Record<string, unknown>;
  markdown: string;
  html: string;
}

export interface MrRunSummary {
  /** Both the ten campaign kinds and the two board kinds come back here — one
   *  run rail, one listing. A caller that maps this to a label must survive a
   *  kind it has no entry for. */
  id: string;
  kind: MrAnyReportKind;
  generated_at: string;
  period?: string | null;
}

export type MrMetricStatus = "good" | "warn" | "bad" | "na";

export interface MrChannelAgg {
  spend: number;
  leads: number;
  qualified_leads: number;
  demos_booked: number;
  demos_completed: number;
  cost_per_lead?: number | null;
  cost_per_qualified_lead?: number | null;
  cost_per_demo_booked: number | null;
  cost_per_demo_completed: number | null;
  cac?: number | null;
  goal?: {
    cpd_booked_low: number;
    cpd_booked_high: number;
    cpd_completed_low: number;
    cpd_completed_high: number;
  } | null;
  status?: Partial<Record<string, MrMetricStatus>>;
}

export interface MrFlagGroup {
  metric: string | null;
  level: string;
  count: number;
  text: string;
}

export interface MrSource {
  platform: string;
  generated_at: string | null;
  metrics: number;
  leads: number;
}

export interface MrOverview {
  has_data: boolean;
  month: string | null;
  totals: MrChannelAgg | null;
  channels: Record<string, MrChannelAgg>;
  flag_summary: MrFlagGroup[];
  /** The lead sheet's month block for the overview month (null until connected). */
  lead_quality?: MrLeadMonth | null;
  sources: MrSource[];
}

export const mrOverview = () => getJson<MrOverview>("/api/mr/overview");

export interface MrDeltaField { delta: number | null; mtd: number | null; corrected: boolean }
export interface MrRateField { value: number | null; mode: "recomputed" | "mtd" }
export interface MrDeltaBlock {
  additive: Record<string, MrDeltaField>;
  rates: Record<string, MrRateField>;
}
export interface MrVendorDelta {
  vendor: string;
  vendor_slug: string;
  date: string;
  since: string | null;
  days: number;
  month_start: boolean;
  corrected: boolean;
  blocks: { team_overall: MrDeltaBlock; channels: Record<string, MrDeltaBlock> };
}
export interface MrSnapshotCaptureResult {
  date: string;
  tabs: { tab: string; slug?: string; captured?: boolean; skipped?: boolean; error?: string }[];
  exported: string[];
}
export interface MrSnapshotMeta {
  vendor: string; vendor_slug: string; gid: number; date: string; month: string; captured_at: string;
}

export const mrSnapshotCapture = () =>
  postJson<MrSnapshotCaptureResult>("/api/mr/snapshots/capture", {});
export const mrSnapshotDeltas = () => getJson<MrVendorDelta[]>("/api/mr/snapshots/deltas");
export const mrSnapshots = () => getJson<MrSnapshotMeta[]>("/api/mr/snapshots");

export interface MrSnapshotDoc {
  vendor: string; vendor_slug: string; gid: number; date: string; month: string; captured_at: string;
  canonical: { team_overall: Record<string, unknown>; channels: Record<string, Record<string, unknown>> };
}
export interface MrVendorDetail {
  vendor: string; vendor_slug: string; gid: number;
  dates: string[];
  snapshot: MrSnapshotDoc;
  delta: MrVendorDelta;
}
export const mrVendorDetail = (slug: string, date?: string, opts?: RequestOptions) =>
  getJson<MrVendorDetail>(`/api/mr/snapshots/vendor/${slug}${date ? `?date_iso=${date}` : ""}`, opts);

export interface MrMonthRow {
  month: string; spend: number; leads: number; qualified_leads: number;
  demos_booked: number; demos_completed: number; cpql: number | null;
}
export interface MrChannelPoint { month: string; spend: number; leads: number; qualified_leads: number }
export interface MrTrendVendor {
  vendor: string; spend_mtd: number; leads: number; qualified_leads: number;
  cpql: number | null; spend_series: { month: string; spend: number }[];
}
/** `kind` is the routing key — the board sends pace to the hero and efficiency to
 *  the vendor chart. Never re-derive it by matching words in `text`: the pace
 *  sentence mentions "qualified leads" and used to match both. */
export interface MrInsight {
  kind?: "pace" | "efficiency" | "mover";
  level: "good" | "warn" | "info";
  text: string;
}
export interface MrTrends {
  has_data: boolean; month: string | null;
  monthly: MrMonthRow[];
  channels: Record<string, MrChannelPoint[]>;
  vendors: MrTrendVendor[];
  insights: MrInsight[];
}
export const mrTrends = () => getJson<MrTrends>("/api/mr/trends");

export interface MrPortfolio {
  date: string; month: string; vendors: number;
  total_budget: number; total_spend: number; budget_utilized_pct: number | null;
  leads: number; qualified_leads: number; cost_per_qualified_lead: number | null;
  qual_demos_booked: number; cost_per_qual_demo_booked: number | null;
  demos_completed: number; cost_per_demo_completed: number | null;
  show_rate_pct: number | null; services_sold: number;
  pacing: { day: number; days_in_month: number; expected_pct: number };
  benchmarks: { cpqdb_max: number; ql_ratio_min: number; show_rate_min: number; cac_target: number; cpql_red: number };
}
export const mrPortfolio = (opts?: RequestOptions) =>
  getJson<MrPortfolio>("/api/mr/snapshots/portfolio", opts);

/* Lead-analysis sheet: per-vendor Meeting Outcome / Deal Stage picture + the
   five lead-quality flags. Auto-detected from any connected workbook. */
export interface MrLeadFlag { level: string; message: string; metric: string }
export interface MrLeadVendor {
  campaign: string; slug: string; matched_vendor: string | null;
  booked: number; completed: number; no_show: number; canceled: number;
  bad_lead: number; pending: number; other: number; resolved: number;
  completed_rate_pct: number | null; no_show_rate_pct: number | null;
  canceled_rate_pct: number | null; bad_lead_rate_pct: number | null;
  deal_stages: Record<string, number>;
  services_sold: number; amount: number; mrr: number;
  brands: Record<string, number>; sources: Record<string, number>;
  tracker: {
    leads: number; qualified_leads: number; demos_booked: number;
    ql_ratio_pct: number | null; booking_rate_pct: number | null;
  } | null;
  flags: MrLeadFlag[];
  story: string;
}
export interface MrLeadTotals {
  booked: number; completed: number; no_show: number; canceled: number;
  bad_lead: number; pending: number; other: number; resolved: number;
  completed_rate_pct: number | null; no_show_rate_pct: number | null;
  canceled_rate_pct: number | null; bad_lead_rate_pct: number | null;
  services_sold: number; amount: number; mrr: number;
  brands: Record<string, number>; sources: Record<string, number>;
}
export interface MrLeadMonth {
  vendors: MrLeadVendor[];
  totals: MrLeadTotals;
  flag_count: number;
}
export interface MrLeadAnalysis {
  has_data: boolean;
  hint?: string;
  generated_at?: string;
  source_label?: string;
  tab?: string;
  gaps?: string[];
  months?: Record<string, MrLeadMonth>;
  latest_month?: string | null;
  unmatched_campaigns?: string[];
}
export const mrLeadAnalysis = () => getJson<MrLeadAnalysis>("/api/mr/lead-analysis");

/** Upload one platform's CSV export and normalize it into a dataset. */
export async function mrIngest(file: File, platform: MrPlatform): Promise<MrIngestResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("platform", platform);
  return sendForm<MrIngestResult>("/api/mr/ingest", form);
}

export interface MrSheetTabResult {
  tab: string;
  dataset_id?: string;
  metrics?: number;
  gaps?: MrDataGap[];
  error?: string;
}
export interface MrSheetIngestResult {
  spreadsheet_id: string;
  year: number;
  tabs: MrSheetTabResult[];
  /** "ok" | "partial" — "partial" means some component kept its PREVIOUS data. */
  status?: string;
  /** Why, in plain words. The backend has always sent these; the console used
   *  to drop them, which is how a pull that ingested zero tracker tabs still
   *  reported success and left weeks-old figures on screen. */
  degraded?: string[];
  ingested?: number;
}

/** Pull the live Google-Sheets performance tracker (brand tabs). */
export const mrIngestSheet = (body: { gid?: string; brand?: string; year?: number } = {}) =>
  postJson<MrSheetIngestResult>("/api/mr/ingest-sheet", body);

export const mrDatasets = () => getJson<MrDataset[]>("/api/mr/datasets");

/** Remove one ingested file/pull; its numbers leave the dashboard immediately. */
export async function mrDeleteDataset(id: string): Promise<void> {
  await deleteJson<{ deleted: string }>(`/api/mr/datasets/${id}`);
}

/** Upload a PDF report — text is extracted and metrics parsed into a dataset. */
export async function mrIngestPdf(file: File): Promise<MrIngestResult> {
  const form = new FormData();
  form.append("file", file);
  return sendForm<MrIngestResult>("/api/mr/ingest-pdf", form);
}

export const mrConnectors = (opts?: RequestOptions) =>
  getJson<MrConnector[]>("/api/mr/connectors", opts);

export const mrConfig = () => getJson<MrConfig>("/api/mr/config");

export interface MrChannelGoalFields {
  cpd_booked_low: number; cpd_booked_high: number;
  cpd_completed_low: number; cpd_completed_high: number;
  completed_demo_pct: number;
}
export interface MrTargets {
  thresholds: Record<string, number>;
  channel_goals: Record<string, MrChannelGoalFields>;
  edited: boolean;
}
export const mrGetTargets = () => getJson<MrTargets>("/api/mr/targets");
export const mrSaveTargets = (body: {
  thresholds?: Record<string, number>;
  channel_goals?: Record<string, Partial<MrChannelGoalFields>>;
  reset?: boolean;
}) => postJson<MrTargets>("/api/mr/targets", body);

export interface MrTabProfile {
  title: string;
  gid: number;
  kind: string;
  granularity: string;
  date_range: string | null;
  platforms: string[];
  metrics: string[];
  summary: string;
  useful: boolean;
  hidden: boolean;
}

export interface MrAskAnswer {
  question: string;
  timeframe: string | null;
  answer: string;
  used_tabs: string[];
}

export interface MrSheetSource {
  id: string;
  label: string;
  primary: boolean;
  include_in_dashboard: boolean;
  added_at?: string;
  /** Who connected it. `null` on rows written before the field existed — "no
   *  store ever recorded an owner", which is not the same as "owned by nobody"
   *  and is why those rows stay listed for everyone. */
  added_by?: string | null;
  /** Whether **this** caller may disconnect it. `DELETE /api/mr/sources/{id}`
   *  answers 403 when it is false, so the console has to stop offering the
   *  button rather than let the click earn the error.
   *
   *  Optional on purpose: this field and the 403 shipped together, so a reply
   *  without it came from a backend that has no gate at all — see
   *  `mayDisconnect` in `components/console/mr/format.ts`, which is the one
   *  place that decides what absence means. */
  can_remove?: boolean;
}
export interface MrSheetSources {
  enabled: boolean;
  service_account: string;
  sources: MrSheetSource[];
}

export const mrSources = () => getJson<MrSheetSources>("/api/mr/sources");

/** Connect another Google Sheet by pasted link; returns the agent's first-pass read of its tabs. */
export const mrAddSource = (url: string) =>
  postJson<{ source: MrSheetSource; tabs: MrTabProfile[]; tab_count: number }>("/api/mr/sources", { url });

/** Disconnect a secondary sheet — the agent stops reading it immediately. */
export async function mrDeleteSource(id: string): Promise<void> {
  await deleteJson<{ removed: string }>(`/api/mr/sources/${id}`);
}

export const mrWorkbook = () => getJson<{ tabs: MrTabProfile[]; count: number }>("/api/mr/workbook");

export const mrWorkbookScan = () =>
  postJson<{ tabs: MrTabProfile[]; count: number }>("/api/mr/workbook/scan", {});

export const mrAsk = (question: string, timeframe?: string) =>
  postJson<MrAskAnswer>("/api/mr/ask", { question, timeframe });

export interface MrReportPeriod {
  period: string;   // "2026-07" | "2026-Q3"
  label: string;    // "July 2026" | "Q3 2026"
  current: boolean; // the month/quarter containing yesterday
}

export interface MrReportPeriods {
  months: MrReportPeriod[];
  quarters: MrReportPeriod[];
}

export const mrReportPeriods = () =>
  getJson<MrReportPeriods>("/api/mr/report-periods");

export const mrBuildReport = (kind: MrReportKind, period?: string) =>
  postJson<MrReport>(`/api/mr/reports/${kind}`, period ? { period } : {});

export const mrListRuns = () => getJson<MrRunSummary[]>("/api/mr/runs");

export const mrGetRun = (id: string) => getJson<MrReport>(`/api/mr/runs/${id}`);

/* -------------------------------- board report ---------------------------- */

/** One published row of the ledger. A single-period report fills `value`; a
 *  comparison fills `a`/`b` and the three derived fields. `null` is **absent**,
 *  never zero — the whole point of the coverage block below. */
export interface MrBoardRow {
  key: string;
  label: string;
  group: string;
  format: string;
  polarity: string;
  basis?: string | null;
  value?: number | null;
  a?: number | null;
  b?: number | null;
  delta?: number | null;
  change_pct?: number | null;
  improved?: boolean | null;
}

/** What one column of the report could fill, and why the rest is blank.
 *
 *  Computed per request against the capture sitting in the store, so it
 *  describes THIS pull rather than the catalog: against production today the
 *  report fills 13 of 38, because the capture predates the roll-up parser
 *  learning the other rows. A thin capture and a thin quarter are different
 *  facts and this is what tells them apart. */
export interface MrBoardCoverageColumn {
  column: string;           // "Q1 2026" — the column heading
  period: string;           // "2026-Q1"
  months: string[];
  filled: string[];         // metric keys that carry a number
  absent: string[];         // metric keys that do not
  absent_reasons: Record<string, string>;  // key -> why, in the reader's terms
  filled_count: number;
  metric_count: number;
}

export interface MrBoardCoverage {
  columns: MrBoardCoverageColumn[];
  metric_count: number;
  /** Why the channel table is withheld rather than zeroed. */
  channel_reconciliation?: string;
}

export interface MrBoardStructured {
  generator: string;
  cache_key: string;
  columns: string[];
  periods: { key: string; label: string; months: string[] }[];
  groups: string[];
  rows: MrBoardRow[];
  /** Period totals the roll-up withheld, each naming the months it withheld
   *  for. A partly-covered period is partial, not shorter. */
  gaps: string[];
  /** The day the figures behind this report were pulled from the sheet. */
  captured_on?: string;
  /** Optional because a backend older than the coverage block simply does not
   *  send it, and "the report did not say" must not render as "0 of 0". */
  coverage?: MrBoardCoverage;
}

/** The board report as it comes off the run rail. Deliberately not `MrReport`:
 *  no model writes any part of this, so it carries no `markdown` and no `html`
 *  — `ai: false` with a reason is the honest provenance, not a degradation. */
export interface MrBoardReport {
  id: string;
  kind: MrBoardKind;
  generated_at: string;
  user_id: string;
  agent_id: string;
  sources?: MrSource[];
  structured: MrBoardStructured;
  ai?: boolean;
  fallback_reason?: string;
  /** True when this exact report (same periods, same capture, same generator)
   *  was already in the store and was handed back rather than derived again. */
  reused?: boolean;
}

/** Build — or re-serve — one board report.
 *
 *  `period` alone gives the one-column report; `compareTo` adds column B. The
 *  two columns are only "A" and "B", so month-vs-month, quarter-vs-quarter and
 *  year-vs-year are the same request. A period is `YYYY-MM`, `YYYY-Qn` or
 *  `YYYY`; a window the tracker holds nothing for is a 422 carrying the
 *  server's own sentence, never a substituted period. `MR_BOARD_REPORT` off
 *  answers 404 — this deployment builds no board reports at all. */
export const mrBuildBoardReport = (period: string, compareTo?: string) =>
  postJson<MrBoardReport>("/api/mr/board-report",
    compareTo ? { period, compare_to: compareTo } : { period });

/** The same run, read back as a board report. Same URL as `mrGetRun` — one run
 *  rail serves both — as a separate function rather than a union return so the
 *  campaign panels keep reading the narrative shape they were written for. The
 *  listing says which kind a run is; that is what picks the reader. */
export const mrGetBoardRun = (id: string) => getJson<MrBoardReport>(`/api/mr/runs/${id}`);

/** The board report as a document. Both endpoints are authenticated, so the
 *  bytes are fetched with the caller's token and handed over as an object URL:
 *  an `<a href>` straight to the endpoint would arrive without one and 401. */
export const mrBoardReportHtmlUrl = (id: string) =>
  blobUrl(`/api/mr/board-report/${id}/html`);
export const mrBoardReportPdfUrl = (id: string) =>
  blobUrl(`/api/mr/board-report/${id}/pdf`);

/** Download-report PDFs — the console panels rendered server-side in the same
 *  format. Returns an object URL ready for an <a download> click. */
async function mrPdfBlobUrl(path: string): Promise<string> {
  return blobUrl(path);
}
export const mrReportPdfUrl = (id: string) => mrPdfBlobUrl(`/api/mr/runs/${id}/pdf`);
export const mrVendorPdfUrl = (slug: string, date?: string) =>
  mrPdfBlobUrl(`/api/mr/snapshots/vendor/${slug}/pdf${date ? `?date_iso=${date}` : ""}`);
export const mrLeadsPdfUrl = (month?: string) =>
  mrPdfBlobUrl(`/api/mr/lead-analysis/pdf${month ? `?month=${month}` : ""}`);

/* ------------------------------ SEO agent (a2) ---------------------------- */

export interface SeoBrand {
  id: string;
  name: string;
  domain: string;
  gsc_property: string;
  seeds: string[];
  enabled: boolean;
}

export interface SeoRunNums {
  /** "search-console" = impression-based numbers; "rank-tracking" = live SERP positions only. */
  mode?: "search-console" | "rank-tracking";
  clicks_28d: number;
  clicks_prev_28d: number;
  impressions_28d: number;
  avg_position: number;
  est_potential_clicks: number;
  tracked?: number;
  top3?: number;
  top10?: number;
  unranked?: number;
  moved_up?: number;
  moved_down?: number;
}

export interface SeoBrandCard {
  brand: SeoBrand;
  gsc_connected?: boolean;
  headline?: string | null;
  last_run: {
    at: string;
    summary: SeoRunNums;
    degraded: string[];
    todo_count: number;
    topic_count: number;
  } | null;
}

export interface SeoOverview {
  sources: { gsc: boolean; serp: boolean };
  brands: SeoBrandCard[];
}

export type SeoTodoStatus = "todo" | "assigned" | "done";

export interface SeoTodo {
  id: string;
  kind: string;
  page: string;
  query: string;
  action: string;
  why: string;
  est_monthly_clicks: number | null;
  position: number;
  impressions: number | null;
  status: SeoTodoStatus;
}

export interface SeoTopic {
  keyword: string;
  source: string;
  priority: "high" | "medium" | "low";
  impact: string;
  angle: string;
  volume_est: number | null;
  volume_label: string;
  trend: "rising" | "falling" | "flat" | "new";
  difficulty: string | null;
  est_monthly_clicks: number | null;
  why: string;
  score: number;
  /** Search intent (e.g. "informational", "commercial") — absent on old persisted runs. */
  intent?: string;
  /** True when this topic was excluded from the live Top-10 to avoid cannibalizing an existing page. */
  avoided?: boolean;
  /** Why this topic was avoided, e.g. "overlaps https://example.com/page" — null/absent when not avoided. */
  avoided_reason?: string | null;
}

export interface SeoGaTotals {
  sessions: number;
  users: number;
  new_users: number;
  engagement_rate: number;
  avg_session_sec: number;
  pageviews: number;
}

/** Live Google Analytics (GA4) overview — null when GA isn't shared with the service account. */
export interface SeoGa {
  property: string;
  property_name: string;
  totals: SeoGaTotals;
  prev_totals: SeoGaTotals;
  top_pages: { path: string; views: number; sessions: number }[];
  channels: { channel: string; sessions: number; prev_sessions: number }[];
  key_events: { event: string; count: number }[];
}

export interface SeoRun {
  brand_id: string;
  at: string;
  trigger: string;
  degraded: string[];
  summary: SeoRunNums;
  insights: string[];
  todos: SeoTodo[];
  topics: SeoTopic[];
  ga?: SeoGa | null;
}

export const seoOverview = (opts?: RequestOptions) =>
  getJson<SeoOverview>("/api/seo-geo/overview", opts);

export interface SeoGscStatus {
  connected: boolean;
  property: string | null;
}

export interface SeoSiteIssue {
  insight: string;
  evidence: string;
  action: string;
  priority: "high" | "medium" | "low";
  category: "content" | "structure" | "trust" | "other";
}

export interface SeoSiteReview {
  at: string;
  page_count: number;
  positioning: string;
  scorecard?: Record<string, { grade: number; note: string }>;
  strengths: string[];
  issues: SeoSiteIssue[];
  suggested_seeds: string[];
  covered_topics: string[];
  missing_topics: string[];
  degraded: string[];
}

export interface SeoPlanItem {
  source: string;
  action: string;
  detail: string;
}

export const seoBrandDetail = (id: string, opts?: RequestOptions) =>
  getJson<{
    brand: SeoBrand;
    run: SeoRun | null;
    gsc?: SeoGscStatus;
    plan?: SeoPlanItem[];
    site_review?: SeoSiteReview | null;
  }>(`/api/seo-geo/brands/${id}`, opts);

export const seoAnalyzeSite = (brandId: string) =>
  postJson<SeoSiteReview>(`/api/seo-geo/site-review/${brandId}`, {});

export const seoOauthStart = (brandId: string) =>
  getJson<{ url: string }>(`/api/seo-geo/oauth/start/${brandId}`);

export const seoOauthDisconnect = (brandId: string) =>
  postJson<{ connected: boolean }>(`/api/seo-geo/oauth/disconnect/${brandId}`, {});

export const seoRunBrand = (id: string) =>
  postJson<{ at: string; summary: SeoRunNums; degraded: string[]; todo_count: number; topic_count: number }>(
    `/api/seo-geo/run/${id}`,
    {},
  );

export const seoSaveBrand = (b: { id?: string; name: string; domain: string; gsc_property?: string; seeds?: string[] }) =>
  postJson<{ brands: SeoBrand[] }>("/api/seo-geo/brands", b);

export async function seoDeleteBrand(id: string): Promise<{ brands: SeoBrand[] }> {
  return deleteJson<{ brands: SeoBrand[] }>(`/api/seo-geo/brands/${id}`);
}

export const seoSetTodoStatus = (brandId: string, todoId: string, status: SeoTodoStatus) =>
  postJson<{ id: string; status: SeoTodoStatus }>(`/api/seo-geo/todos/${brandId}/${todoId}`, { status });

/* ------------------------ SEO agent: researcher layer --------------------- */

export interface SeoCluster {
  name: string;
  intent: "informational" | "commercial" | "transactional" | "navigational" | "local";
  keywords: string[];
  volume_est: number;
  best_position: number | null;
  coverage: "gap" | "weak" | "ranking";
  opportunity: number;
  tier: "high" | "medium" | "watch";
  recommendation: string;
  owned_by?: string[];
  aio_present?: boolean;
}

export interface SeoKeywordLab {
  brand_id: string;
  at: string;
  keyword_count: number;
  degraded: string[];
  clusters: SeoCluster[];
  gaps: string[];
}

export interface SeoRankShift {
  keyword: string;
  position: number | null;
  previous: number | null;
  delta: number | null;
  top: string[];
}

export interface SeoSitemapEntry {
  at: string;
  total: number;
  new_urls: string[];
  new_count: number;
  first_check: boolean;
}

export interface SeoCompetitors {
  tracked: string[];
  suggested: string[];
  shifts: SeoRankShift[];
  feed: Record<string, SeoSitemapEntry>;
  custom_queries: string[];
  pool_size: number;
  pool_cap: number;
}

export interface SeoBrief {
  id: string;
  keyword: string;
  at: string;
  intent: string;
  target_keywords: string[];
  outline: { heading: string; note: string }[];
  questions: string[];
  entities: string[];
  target_word_count: number;
  schema_recommended: string[];
  internal_links: string[];
  who_ranks: { domain: string; title: string; position: number }[];
  our_position: number | null;
  aio_present: boolean;
  degraded: string[];
}

export interface SeoAuditIssue {
  issue: string;
  severity: "high" | "medium" | "low";
  count: number;
  pages: string[];
  fix: string;
}

export interface SeoSiteCheck {
  name: string;
  ok: boolean;
  note: string;
  fix: string;
}

export interface SeoAuditReport {
  brand_id: string;
  at: string;
  pages_checked: number;
  pages_ok: number;
  health_score: number;
  site_checks?: SeoSiteCheck[];
  issues: SeoAuditIssue[];
}

export interface SeoDraftScore {
  score: number;
  verdict: "publish-ready" | "needs work" | "rework";
  keyword: string;
  word_count: number;
  checks: { name: string; ok: boolean; note: string; weight: number }[];
}

export interface SeoUpdatePlan {
  page: string;
  at: string;
  query: string;
  our_position: number | null;
  suggestions: string[];
}

export const seoKeywordLab = (brandId: string, opts?: RequestOptions) =>
  getJson<{ lab: SeoKeywordLab | null }>(`/api/seo-geo/keywords/${brandId}`, opts);

export const seoRunKeywordLab = (brandId: string) =>
  postJson<SeoKeywordLab>(`/api/seo-geo/keywords/${brandId}/run`, {});

export const seoCompetitors = (brandId: string, opts?: RequestOptions) =>
  getJson<SeoCompetitors>(`/api/seo-geo/competitors/${brandId}`, opts);

export async function seoSetCompetitors(brandId: string, domains: string[]): Promise<{ tracked: string[] }> {
  return putJson<{ tracked: string[] }>(`/api/seo-geo/competitors/${brandId}`, { domains });
}

export const seoTrackCompetitors = (brandId: string) =>
  postJson<{ shifts: SeoRankShift[]; feed: Record<string, SeoSitemapEntry>; degraded: string[] }>(
    `/api/seo-geo/competitors/${brandId}/track`,
    {},
  );

export const seoAddCustomQuery = (brandId: string, query: string) =>
  postJson<{ custom_queries: string[]; pool_size: number }>(
    `/api/seo-geo/competitors/${brandId}/custom-queries`, { query },
  );

export const seoRemoveCustomQuery = (brandId: string, query: string) =>
  requestJson<{ custom_queries: string[]; pool_size: number }>(
    `/api/seo-geo/competitors/${brandId}/custom-queries`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    },
  );

/** Top-5 competitor profiles: visibility, keywords they beat us on, and a
 *  content feed with honestly labelled reach estimates (never a bare number). */
export interface SeoCompetitorPost {
  url: string;
  title: string;
  topic: string;
  est_monthly_clicks: number | null;
  estimate_basis: string;
}

export interface SeoCompetitorProfile {
  domain: string;
  /** Null when there are no tracked keywords to compute visibility from —
   *  distinct from a real 0%; render "—" (matches avg_position's pattern). */
  visibility_pct: number | null;
  avg_position: number | null;
  keywords_won: { keyword: string; their_position: number; our_position: number | null }[];
  recent_posts: SeoCompetitorPost[];
  hot_topics: string[];
}

export interface SeoCompetitorProfilesDoc {
  at: string;
  notes: string[];
  profiles: SeoCompetitorProfile[];
}

export const seoCompetitorProfiles = (brandId: string, opts?: RequestOptions) =>
  getJson<{ profiles: SeoCompetitorProfilesDoc | null }>(`/api/seo-geo/competitors/${brandId}/profiles`, opts);

export const seoCompetitorProfilesRefresh = (brandId: string) =>
  postJson<SeoCompetitorProfilesDoc>(`/api/seo-geo/competitors/${brandId}/profiles/refresh`, {});

export const seoBriefs = (brandId: string, opts?: RequestOptions) =>
  getJson<{ briefs: SeoBrief[] }>(`/api/seo-geo/briefs/${brandId}`, opts);

export const seoBuildBrief = (brandId: string, keyword: string) =>
  postJson<SeoBrief>(`/api/seo-geo/briefs/${brandId}`, { keyword });

export const seoAuditReport = (brandId: string, opts?: RequestOptions) =>
  getJson<{ report: SeoAuditReport | null }>(`/api/seo-geo/audit/${brandId}`, opts);

export const seoRunAudit = (brandId: string) =>
  postJson<SeoAuditReport>(`/api/seo-geo/audit/${brandId}/run`, {});

export const seoDraftScore = (brandId: string, text: string, keyword: string) =>
  postJson<SeoDraftScore>(`/api/seo-geo/draft-score/${brandId}`, { text, keyword });

export const seoUpdatePlan = (brandId: string, page: string) =>
  postJson<SeoUpdatePlan>(`/api/seo-geo/update-plan/${brandId}`, { page });

export const seoAsk = (brandId: string, question: string) =>
  postJson<{ question: string; answer: string }>(`/api/seo-geo/ask/${brandId}`, { question });

/* ---------------------------------------------------------------- blog writer (a9) */

export interface BwCounts {
  sitemap_urls: number;
  blog_urls: number;
  titled: number;
}

export interface BwBrand {
  id: string;
  name: string;
  domain: string;
  inventory: { counts: BwCounts; scanned: string } | null;
  voice: { studied: string; count: number } | null;
}

export interface BwVoice {
  brand_id: string;
  studied: string;
  posts_read: string[];
  count: number;
  profile: Record<string, string | string[]>;
}

export interface BwInventory {
  domain: string;
  scanned: string;
  posts: { url: string; title: string }[];
  counts: BwCounts;
  notes: string[];
}

export interface BwEvidence {
  id: string;
  claim: string;
  quote: string;
  url: string;
  source_name: string;
  source_class: "studies" | "experts" | "news" | "anecdotes" | "competitors" | string;
  date: string;
  credibility: string;
}

export interface BwRound {
  n: number;
  at: string;
  queries: { angle: string; q: string; hits: number }[];
  read: string[];
  added: number;
  gaps: string[];
}

export interface BwBlock {
  id: string;
  kind: "intro" | "section" | "conclusion" | string;
  heading: string;
  text: string;
  cites: string[];
  history: string[];
  last_comment?: string;
}

export interface BwDraft {
  meta: { title: string; description: string; slug: string };
  blocks: BwBlock[];
  internal_links: { url: string; title: string }[];
  notes: string[];
  /** True once the house writing guidelines line-edit has run on every block. */
  guidelines_applied?: boolean;
}

export interface BwVisual {
  n: number;
  section: string;
  type: string;
  theme: string;
  prompt: string;
  rationale: string;
}

export interface BwRunSummary {
  id: string;
  brand_id: string;
  brand_name: string;
  topic: string;
  created: string;
  status: string;
}

export interface BwRun {
  id: string;
  brand_id: string;
  brand_name: string;
  domain: string;
  topic: string;
  notes: string;
  created: string;
  status: "research" | "saturated" | "capped";
  rounds: BwRound[];
  ledger: BwEvidence[];
  gaps: string[];
  draft: BwDraft | null;
  visuals: { items: BwVisual[]; notes: string[] } | null;
}

export type BwExportFormat = "md" | "html" | "txt" | "visuals-md" | "visuals-txt";

export const bwBrands = () => getJson<{ brands: BwBrand[] }>("/api/blog/brands");

export const bwInventory = (brandId: string) => getJson<BwInventory>(`/api/blog/brands/${brandId}/inventory`);

export const bwScanInventory = (brandId: string) => postJson<BwInventory>(`/api/blog/brands/${brandId}/inventory`, {});

export const bwVoice = (brandId: string) => getJson<BwVoice>(`/api/blog/brands/${brandId}/voice`);

export const bwStudyVoice = (brandId: string) => postJson<BwVoice>(`/api/blog/brands/${brandId}/voice`, {});

export const bwRuns = () => getJson<{ runs: BwRunSummary[] }>("/api/blog/runs");

export const bwRun = (id: string) => getJson<BwRun>(`/api/blog/runs/${id}`);

export const bwCreateRun = (p: { brand_id: string; topic: string; notes?: string }) =>
  postJson<BwRun>("/api/blog/runs", p);

export const bwResearchStep = (id: string) => postJson<BwRun>(`/api/blog/runs/${id}/research/step`, {});

export const bwBuildDraft = (id: string) => postJson<BwRun>(`/api/blog/runs/${id}/draft`, {});

export const bwCommentBlock = (id: string, blockId: string, comment: string) =>
  postJson<BwRun>(`/api/blog/runs/${id}/blocks/${blockId}/comment`, { comment });

export const bwPlanVisuals = (id: string) => postJson<BwRun>(`/api/blog/runs/${id}/visuals`, {});

export async function bwExport(id: string, format: BwExportFormat): Promise<Blob> {
  return fetchBlob(`/api/blog/runs/${id}/export?format=${format}`, { method: "GET" });
}

/* --------------------------- SEO agent: Pages ------------------------------ */
// Per-page traffic + crawl intel: where analytics and the site crawl disagree,
// what's underperforming, and what to do about each page.

export interface SeoPageIntel {
  path: string;
  /** Null for pages known only from GA/GSC — never crawled into the corpus
   *  (see the "not-crawled" flag), so there's no on-page fact to source these from. */
  url: string | null;
  title: string | null;
  views: number;
  sessions: number;
  engagement_rate: number;
  clicks: number;
  impressions: number;
  position: number | null;
  best_query: string | null;
  /** Beyond content/structure/trust flags, "not-crawled" = seen in analytics but
   *  absent from the site crawl; its recommendation explains why. */
  flags: string[];
  recommendation: string;
  /** True only when THIS page's recommendation came from the AI pass (top-traffic
   *  slice + successful call). Older persisted docs lack the field — falsy is the
   *  honest default ("Rule:"), never assume AI wrote it. */
  ai?: boolean;
  word_count: number;
}

export interface SeoPagesDoc {
  brand_id: string;
  at: string;
  ai: boolean;
  notes: string[];
  pages: SeoPageIntel[];
}

export const seoPages = (id: string, opts?: RequestOptions) =>
  getJson<{ pages: SeoPagesDoc | null }>(`/api/seo-geo/pages/${id}`, opts);

export const seoPagesRefresh = (id: string) =>
  postJson<SeoPagesDoc>(`/api/seo-geo/pages/${id}/refresh`, {});

/* ---- Technical health: sitemap, Core Web Vitals, keyword pool ---- */

export interface SeoSitemapIssue {
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
}

export interface SeoSitemapDoc {
  at: string;
  domain: string;
  present: boolean;
  is_index: boolean;
  child_sitemaps: number;
  status: number | null;
  url_count: number;
  declared_in_robots: string[];
  lastmod_coverage_pct: number;
  lastmod_newest: string | null;
  stale_count: number;
  duplicate_count: number;
  sampled: { url: string; status: number }[];
  orphans: string[];
  missing_from_site: string[];
  issues: SeoSitemapIssue[];
  notes: string[];
  score: number;
}

export const seoSitemap = (id: string, opts?: RequestOptions) =>
  getJson<{ sitemap: SeoSitemapDoc | null }>(`/api/seo-geo/sitemap/${id}`, opts);

/** Starts the deep audit, which is what diagnoses the sitemap now; poll
 *  `seoDeepAudit` for progress. */
export const seoSitemapRefresh = (id: string) =>
  postJson<{ job: DeepJob }>(`/api/seo-geo/sitemap/${id}/refresh`, {});

/** One CrUX metric at the 75th percentile — the threshold Google itself
 *  assesses against. `unit` is "ms" everywhere except CLS, which is unitless
 *  and arrives scaled by 100 so one integer comparison works for all of them. */
export interface SeoVitalMetric {
  metric: string;
  label: string;
  p75: number;
  unit: "ms" | "cls";
  category: "good" | "needs-improvement" | "poor";
  good_pct: number | null;
  poor_pct: number | null;
  good_threshold: number;
  poor_threshold: number;
}

export type SeoVitalsAssessment = "passing" | "needs-improvement" | "failing" | "insufficient-data";

export interface SeoVitalsSlice {
  metrics: Record<string, SeoVitalMetric>;
  assessment: SeoVitalsAssessment;
  period?: { year: number; month: number; day: number } | null;
}

export interface SeoVitalsDoc {
  at: string;
  origin: string;
  origin_vitals: Partial<Record<"mobile" | "desktop", SeoVitalsSlice>>;
  pages: {
    url: string;
    has_data: boolean;
    metrics?: Record<string, SeoVitalMetric>;
    assessment?: SeoVitalsAssessment;
  }[];
  notes: string[];
}

export const seoVitals = (id: string, opts?: RequestOptions) =>
  getJson<{ vitals: SeoVitalsDoc | null; available: boolean }>(`/api/seo-geo/vitals/${id}`, opts);

export const seoVitalsRefresh = (id: string) =>
  postJson<{ vitals: SeoVitalsDoc }>(`/api/seo-geo/vitals/${id}/refresh`, {});

export interface SeoPriorityItem {
  id: string;
  title: string;
  why_it_matters: string;
  severity: "critical" | "warning" | "suggestion";
  source: "vitals" | "keyword_pool" | "competitors" | "deep_audit" | "insights";
  action_link: string;
}

export interface SeoPrioritiesDoc {
  brand_id: string;
  at: string;
  items: SeoPriorityItem[];
  notes: string[];
}

export const seoPriorities = (id: string, opts?: RequestOptions) =>
  getJson<{ priorities: SeoPrioritiesDoc | null }>(`/api/seo-geo/priorities/${id}`, opts);

export const seoPrioritiesRefresh = (id: string) =>
  postJson<{ priorities: SeoPrioritiesDoc }>(`/api/seo-geo/priorities/${id}/refresh`, {});

export type SeoKeywordBand = "top3" | "page1" | "page2" | "beyond" | "unranked";

export interface SeoPoolKeyword {
  keyword: string;
  sources: string[];
  cluster: string | null;
  intent: string;
  page: string;
  clicks: number;
  impressions: number;
  position: number;
  ctr: number;
  /** Estimated ADDITIONAL monthly clicks from a realistic ranking move — not
   *  "clicks if we were #1". A keyword already ranking well scores low. */
  opportunity: number;
  thin: boolean;
  ranked: boolean;
  band: SeoKeywordBand;
}

export interface SeoKeywordPoolDoc {
  at: string;
  keywords: SeoPoolKeyword[];
  totals: {
    keywords: number;
    ranked: number;
    thin: number;
    clicks: number;
    impressions: number;
    opportunity: number;
  };
  bands: Record<SeoKeywordBand, number>;
  clusters: string[];
  notes: string[];
}

export const seoKeywordPool = (id: string, opts?: RequestOptions) =>
  getJson<{ pool: SeoKeywordPoolDoc | null }>(`/api/seo-geo/keyword-pool/${id}`, opts);

export const seoKeywordPoolRefresh = (id: string) =>
  postJson<{ pool: SeoKeywordPoolDoc }>(`/api/seo-geo/keyword-pool/${id}/refresh`, {});

/* ---- Deep audit: every sitemap URL crawled once, then five diagnostics ---- */

export type DeepSeverity = "high" | "medium" | "low" | "info";

export interface DeepJob {
  kind: string;
  brand_id: string;
  status: "running" | "done" | "failed" | "interrupted";
  phase: string;
  done: number;
  total: number;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  log: string[];
  alive: boolean;
}

export interface DeepSummary {
  at: string;
  domain: string;
  urls: number;
  sitemaps: number;
  pages_by_type: Record<string, number>;
  live_pages: number;
  gsc_connected: boolean;
  notes: string[];
  sitemap: { score: number; affected: number; issues: number };
  landing: { pages: number; avg_score: number; high: number; template_issues: number;
             grades: Record<string, number> };
  cannibalization: { pairs: number; high: number; medium: number; confirmed: number };
  density: { posts: number; pass: number; average_only: number; fail: number; median_wpm: number | null };
}

export const seoDeepAudit = (id: string, opts?: RequestOptions) =>
  getJson<{ summary: DeepSummary | null; job: DeepJob | null; speed_job: DeepJob | null }>(
    `/api/seo-geo/deep-audit/${id}`, opts);

export const seoDeepAuditRun = (id: string) =>
  postJson<{ job: DeepJob }>(`/api/seo-geo/deep-audit/${id}/run`, {});

/* sitemap */

export interface DeepSitemapFile {
  url: string;
  status: number;
  kind: "index" | "urlset" | "error" | "unparseable";
  declared_in_robots: boolean;
  parents: string[];
  depth: number;
  url_count: number;
  child_count: number;
  lastmod_count: number;
  lastmod_invalid: number;
  lastmod_future: number;
  bytes: number;
  redirected: boolean;
  error: string | null;
}

export interface DeepIssue {
  code: string;
  severity: DeepSeverity;
  title: string;
  why: string;
  fix: string;
  count: number;
  urls: string[];
  detail: Record<string, unknown>[];
}

export interface DeepSitemapDoc {
  at: string;
  domain: string;
  score: number;
  url_count: number;
  sitemap_count: number;
  healthy_urls: number;
  affected_urls: number;
  sitemaps: DeepSitemapFile[];
  robots: { status: number; declared: string[] };
  issues: DeepIssue[];
  probes: {
    host_checks: { url: string; status: number; final_url: string; ok: boolean }[];
    slash_sections: string[];
    unlisted_checked: number;
  };
  counts: { by_type: Record<string, number>; by_status: Record<string, number>; with_lastmod: number };
}

export interface DeepSitemapUrl {
  url: string;
  type: string;
  subtype: string;
  status: number;
  final: string;
  noindex: boolean;
  canonical: string;
  lastmod: string | null;
  inlinks: number;
  sitemaps: string[];
  problems: string[];
}

export const seoDeepSitemap = (id: string, opts?: RequestOptions) =>
  getJson<{ sitemap: DeepSitemapDoc | null; urls: DeepSitemapUrl[] }>(
    `/api/seo-geo/deep-audit/${id}/sitemap`, opts);

/* landing pages */

export interface DeepFinding {
  check: string;
  group: string;
  severity: DeepSeverity;
  title: string;
  evidence: string;
  why: string;
  fix: string;
}

export interface DeepLandingPage {
  url: string;
  subtype: string;
  title: string;
  h1: string;
  keyword: string;
  keyword_source: string;
  status: number;
  words: number;
  /** Main-content words not shared with 2+ other landing pages. */
  unique_words?: number | null;
  inlinks: number;
  score: number;
  grade: "A" | "B" | "C" | "D" | "F";
  checks_run: number;
  checks_failed: number;
  high: number;
  medium: number;
  low: number;
  lcp_ms: number | null;
  findings?: DeepFinding[];
}

export interface DeepMatrixRow {
  check: string;
  label: string;
  group: string;
  severity: DeepSeverity;
  title: string;
  why: string;
  fix: string;
  count: number;
  share: number;
  template: boolean;
  pages: { url: string; evidence: string }[];
}

export interface DeepLandingDoc {
  at: string;
  pages: number;
  avg_score: number;
  grades: Record<string, number>;
  high_total: number;
  template_issues: number;
  issues: DeepMatrixRow[];
  subtypes: { subtype: string; pages: number; avg_score: number; high: number }[];
  near_duplicate_pairs: { a: string; b: string; similarity: number }[];
  speed_included: boolean;
  keyword_sources: Record<string, number>;
}

export const seoDeepLanding = (id: string, opts?: RequestOptions) =>
  getJson<{ landing: DeepLandingDoc | null; pages: DeepLandingPage[] }>(
    `/api/seo-geo/deep-audit/${id}/landing`, opts);

export const seoDeepLandingPage = (id: string, url: string, opts?: RequestOptions) =>
  getJson<{ page: DeepLandingPage }>(
    `/api/seo-geo/deep-audit/${id}/landing/page?url=${encodeURIComponent(url)}`, opts);

/* cannibalization */

export type DeepCannibalKind =
  | "same-target" | "copied-spoke" | "numbered-series" | "blog-vs-landing"
  | "hub-spoke-overlap" | "hub-spoke";

export interface DeepCannibalDoc {
  at: string;
  pages_checked: number;
  pairs: number;
  by_severity: Record<"high" | "medium" | "low", number>;
  by_kind: Record<DeepCannibalKind, number>;
  clusters: {
    keep: string;
    severity: DeepSeverity;
    keyword: string;
    /** `similarity` is null when either page has too little text to compare —
     *  which is not the same as 0% similar, and must not be shown as it. */
    competitors: { url: string; kind: DeepCannibalKind; severity: DeepSeverity;
                   similarity: number | null; action: string }[];
  }[];
  healthy_hubs: { hub: string; spokes: number }[];
  duplicate_titles: { text: string; urls: string[] }[];
  duplicate_h1s: { text: string; urls: string[] }[];
  confirmed: { query: string; impressions: number; severity: DeepSeverity;
               pages: { url: string; impressions: number; clicks: number; position: number }[] }[];
  gsc_connected: boolean;
  method: Record<string, string>;
}

export const seoDeepCannibal = (id: string, opts?: RequestOptions) =>
  getJson<{ cannibalization: DeepCannibalDoc | null }>(
    `/api/seo-geo/deep-audit/${id}/cannibalization`, opts);

/* blog keyword density */

export interface DeepDensityPost {
  url: string;
  title: string;
  focus_keyword: string;
  keyword_source: string;
  words: number;
  mentions: number;
  mentions_exact: number;
  mentions_variant: number;
  required: number;
  shortfall: number;
  words_per_mention: number | null;
  density_pct: number;
  windows: number;
  windows_covered: number;
  coverage_pct: number;
  gaps: { window: number; words: string; starts_with: string }[];
  per_window: number[];
  max_in_window: number;
  stuffing: boolean;
  pass_average: boolean;
  pass_every_window: boolean;
  verdict: "pass" | "pass-on-average" | "fail";
  placement: Record<string, boolean>;
  placement_missing: string[];
}

export interface DeepDensityDoc {
  at: string;
  rule: string;
  posts: number;
  pass_every_window: number;
  pass_on_average_only: number;
  fail: number;
  stuffing: number;
  median_words_per_mention: number | null;
  median_coverage_pct: number;
  total_missing_mentions: number;
  buckets: Record<string, number>;
  placement_missing: Record<string, number>;
  keyword_sources: Record<string, number>;
  method: Record<string, string>;
}

export const seoDeepDensity = (id: string, opts?: RequestOptions) =>
  getJson<{ density: DeepDensityDoc | null; posts: DeepDensityPost[] }>(
    `/api/seo-geo/deep-audit/${id}/density`, opts);

/* page speed */

export type SpeedRating = "good" | "needs-improvement" | "poor" | null;

export interface SpeedRow {
  url: string;
  key: string;
  type: string;
  subtype: string;
  status?: number | null;
  ttfb_ms?: number | null;
  fcp_ms?: number | null;
  lcp_ms?: number | null;
  dcl_ms?: number | null;
  load_ms?: number | null;
  cls?: number;
  tbt_ms?: number;
  requests?: number;
  bytes?: number;
  third_party_requests?: number;
  third_party_bytes?: number;
  render_blocking?: number;
  lcp_element?: string;
  lcp_rating?: SpeedRating;
  /** Set when the LCP element sits in a popup (a fixed, modal-like overlay). */
  lcp_overlay?: string | null;
  /** When the page's own largest element painted, with any popup set aside. */
  content_lcp_ms?: number | null;
  content_lcp_element?: string | null;
  content_lcp_rating?: SpeedRating;
  cls_rating?: SpeedRating;
  tbt_rating?: SpeedRating;
  error: string | null;
  /** Loaded, but this machine's network failed under it — not a measurement. */
  degraded?: boolean;
  /** Detail endpoint only. */
  failed_requests?: { url: string; error: string }[];
  blocked_requests?: string[];
  lcp_candidates?: { t: number; size: number; el: string; overlay: string | null }[];
  shifts?: { t: number; v: number; nodes: string[] }[];
  heaviest?: { url: string; type: string; bytes: number; ms: number | null; third: boolean; party?: "own" | "cdn" | "third" }[];
  cdn_requests?: number;
  cdn_bytes?: number;
  bytes_by_type?: Record<string, number>;
  third_party_hosts?: { host: string; bytes: number }[];
}

export interface SpeedSummary {
  at: string;
  measured: number;
  failed: number;
  degraded?: number;
  queued: number;
  complete: boolean;
  conditions: string;
  lcp: { median_ms: number | null; p75_ms: number | null; good: number; needs_improvement: number; poor: number };
  cls: { median: number | null; poor: number; needs_improvement: number };
  tbt_median_ms: number | null;
  bytes_median: number | null;
  requests_median: number | null;
  by_type: Record<string, { pages: number; lcp_median_ms: number | null; poor: number }>;
  content_lcp?: { median_ms: number | null; p75_ms: number | null; good: number; needs_improvement: number; poor: number };
  popup_lcp?: {
    pages: number;
    overlays: { overlay: string; pages: number }[];
    lcp_median_ms: number | null;
    content_median_ms: number | null;
  };
  common_shift_sources?: { node: string; cls: number; pages: number }[];
  common_lcp_elements: { element: string; pages: number }[];
  third_party: { host: string; bytes: number }[];
}

export const seoPageSpeed = (id: string, opts?: RequestOptions) =>
  getJson<{ summary: SpeedSummary | null; rows: SpeedRow[]; job: DeepJob | null }>(
    `/api/seo-geo/page-speed/${id}`, opts);

export const seoPageSpeedRun = (id: string, body: { limit?: number | null; fresh?: boolean }) =>
  postJson<{ job: DeepJob }>(`/api/seo-geo/page-speed/${id}/run`, body);

export const seoPageSpeedPage = (id: string, url: string, opts?: RequestOptions) =>
  getJson<{ page: SpeedRow }>(
    `/api/seo-geo/page-speed/${id}/page?url=${encodeURIComponent(url)}`, opts);

/* ------------------------------ rank tracker ------------------------------ */
//
// A 200-query scoreboard swept every two hours, with history, tracked-rival
// positions, and a worklist. The sweep is a background job — see DeepJob,
// reused here rather than redefined, exactly like page-speed above.

export interface RankSerpEntry {
  position: number;
  domain: string;
  url: string;
  title: string;
}

export interface RankRow {
  query: string;
  position: number | null;
  url: string;
  checked_at: string;
  top: RankSerpEntry[];
  error: string | null;
  /** The week's movement and who (if anyone) outranks us — computed for
   *  every row, not just the worklist's top 10 (`annotate_rows` on the
   *  backend). `leader*` is null when nobody is above us, including when we
   *  rank #1 — that is a real "nothing to show", not missing data. */
  impressions: number;
  delta_7d: number | null;
  dropped: boolean;
  leader: string | null;
  leader_position: number | null;
  leader_url: string | null;
}

export interface RankWorklistRow {
  query: string;
  position: number | null;
  impressions: number;
  leader: string;
  leader_position: number;
  leader_url: string;
  tracked_rival: boolean;
  delta_7d: number | null;
  score: number;
  reason: string;
  /** True for a query that fell out of the results entirely this week — the
   *  panel badges this distinctly rather than pattern-matching `reason`. */
  dropped: boolean;
}

export interface RankPool {
  size: number;
  cap: number;
  built_at: string | null;
  sources_used: string[];
  notes: string[];
}

export interface RankBudget {
  date: string;
  searches: number;
  cap: number;
  remaining: number;
}

/** Fields the panel reads off the sweep's manifest. The manifest (see
 *  `jobs.save_list`) also carries `chunks`, `count` and `saved_at` — harmless
 *  extra keys at runtime, not declared here beyond `count`. */
export interface RankMeta {
  at: string;
  ranked: number;
  errors: number;
  rivals: string[];
  count: number;
}

/** Why the most recent sweep did nothing, when it did nothing. `job` only
 *  reports that the background thread finished; a sweep that was switched
 *  off, had no API key, or found another run already in progress finishes
 *  cleanly having written nothing at all. */
export interface RankSweepOutcome {
  checked: number;
  ranked: number;
  errors: number;
  /** null = the sweep ran to completion. "budget" = it stopped part-way. The
   *  other three mean it did not start and charged nothing. */
  blocked: "disabled" | "credentials" | "running" | "budget" | null;
  at: string;
  notes: string[];
}

export interface RankTrackerDoc {
  rows: RankRow[];
  meta: RankMeta | null;
  worklist: RankWorklistRow[];
  pool: RankPool;
  budget: RankBudget;
  competitors: string[];
  enabled: boolean;
  job: DeepJob | null;
  /** null before any sweep has ever run — distinct from a sweep that ran and
   *  reported nothing blocked. */
  last_sweep: RankSweepOutcome | null;
}

/** One raw rank observation. `h` is the epoch HOUR (not seconds); `p` is the
 *  position, or null for a sweep in which we ranked nowhere.
 *
 *  A map, not a `[h, p]` pair, and that is load-bearing rather than
 *  stylistic: Firestore rejects an array whose elements are themselves
 *  arrays, so the tuple encoding could not be stored at all. */
export interface RankPoint {
  h: number;
  p: number | null;
}

/** One day's rolled-up triple, for days older than the raw retention window.
 *  `d` is the ISO date. A day we ranked nowhere records `best`, `worst` and
 *  `last` as null; `at` is the epoch-hour of the day's final point and is
 *  always present. Same nested-array constraint as `RankPoint` — this used to
 *  be `[date, {...}]`. */
export interface RankDaily {
  d: string;
  best: number | null;
  worst: number | null;
  last: number | null;
  at: number;
}

export interface RankHistory {
  query: string;
  /** The recent window, one point per sweep. This is the window `delta_7d`
   *  and the dropout flag are computed over, so it is the part the chart must
   *  show — `daily` is empty for the tracker's first week and a week stale
   *  thereafter. */
  raw: RankPoint[];
  daily: RankDaily[];
  rivals: Record<string, { raw: RankPoint[]; daily: RankDaily[] }>;
}

export interface RankGap {
  query: string;
  our_url: string;
  our_position: number | null;
  their_url: string;
  their_domain: string;
  their_position: number;
  metrics: {
    words: { ours: number | null; theirs: number | null };
    headings: { ours: number | null; theirs: number | null };
    schema: { ours: string[] | null; theirs: string[] | null };
    questions: { ours: number | null; theirs: number | null };
  };
  narrative: string;
  notes: string[];
  at: string;
  cached: boolean;
}

export const seoRankTracker = (id: string, opts?: RequestOptions) =>
  getJson<RankTrackerDoc>(`/api/seo-geo/rank-tracker/${id}`, opts);

export const seoRankHistory = (id: string, query: string, opts?: RequestOptions) =>
  getJson<{ history: RankHistory }>(
    `/api/seo-geo/rank-tracker/${id}/history?query=${encodeURIComponent(query)}`, opts);

export const seoRankSweep = (id: string) =>
  postJson<{ job: DeepJob }>(`/api/seo-geo/rank-tracker/${id}/sweep`, {});

export const seoRankPoolRebuild = (id: string) =>
  postJson<RankTrackerDoc>(`/api/seo-geo/rank-tracker/${id}/pool/rebuild`, {});

export const seoRankGap = (id: string, query: string) =>
  postJson<{ gap: RankGap }>(`/api/seo-geo/rank-tracker/${id}/gap`, { query });

/* ---- GEO agent (a10): AI answer visibility ---- */

export type GeoEngineId = "perplexity" | "gemini" | "chatgpt" | "aio" | "ai_mode";

/** How an engine's answers are actually obtained. `proxy` means an OpenRouter
 *  stand-in model answered — NOT the consumer product whose name is on the
 *  chip. The panel must never render proxy and native identically. `serpapi`
 *  and `dataforseo` are both the real consumer Google surface, fetched by
 *  different vendors. */
export type GeoEngineMode = "native" | "proxy" | "serpapi" | "dataforseo" | "off" | "unknown";

export interface GeoEngineStatus {
  connected: boolean;
  mode: GeoEngineMode;
  model: string;   // e.g. "google-ai-overview" / "google-ai-mode" for the SERP engines
  means: string;   // plain-language sentence, rendered verbatim as the tooltip
}

/** What an engine IS, as opposed to whether it is reachable: how it is asked,
 *  and therefore why its answer count differs from the engine beside it.
 *
 *  `kind` is deliberately not a union — a kind this build has never heard of
 *  must read as "not a billed search engine", not fail to compile. */
export interface GeoEngineSpec {
  label: string;
  /** "chat" is sampled repeatedly; "serp" is a billed Google snapshot */
  kind: string;
  /** readings of each question one check buys */
  runs_per_prompt: number;
  /** the prompt intents this engine is spent on; null = every question. The
   *  billed engines skip the brand-intent ones, which already name the brand. */
  intents: string[] | null;
}

export interface GeoGlobalConfig {
  engines: Record<GeoEngineId, boolean>;
  engine_status?: Record<string, GeoEngineStatus>;
  /** engine id -> display label, from the backend's own spec table */
  engine_labels?: Record<string, string>;
  /** engine id -> how it is asked. Optional per the deploy-skew law; without it
   *  the panel states the arithmetic and claims no cadence. Read this rather
   *  than mirroring `SERP_ENGINES` / `CHAT_RUNS_PER_PROMPT` as constants — a
   *  copy of a fact starts lying the day the fact changes. */
  engine_specs?: Record<string, GeoEngineSpec>;
  default_runs: number;
  default_daily_cap: number;
  /** joint monthly ceiling for the billed SERP engines (AIO + AI Mode) */
  default_aio_monthly_cap?: number;
}

export interface GeoBrandRow {
  id: string;
  name: string;
  domain: string;
  prompts: number;
  recent_answers: number;
  calls_used_today: number;
  competitors: number;
  /** The scheduled check, as the list route now reports it per brand — so the
   *  Brands screen draws a row's switch without one `poll/status` call each.
   *
   *  All three are optional and MUST be read through `checkOf()` in
   *  `components/console/geo/schedule.ts`. Vercel puts this frontend live about
   *  a minute after a push and Cloud Run four to six minutes after; in that
   *  window the list answers without them, and a row that renders "off" or
   *  crashes on a missing field is the deploy skew that has taken this console
   *  down before. `next_due_at` is null for a brand that has never completed a
   *  sweep — it is due on the next run, not on an invented date. */
  auto_poll?: boolean;
  poll_interval_days?: number;
  next_due_at?: string | null;
}

/** What `POST /api/geo/brands` answers with (201).
 *
 *  `auto_poll` and `poll_interval_days` are read back off the document that was
 *  actually written and are null when that seed failed — in which case
 *  `warning` says so. Reporting the schedule we intended would be a claim about
 *  a brand whose stored schedule is now something else. */
export interface GeoBrandCreated {
  brand: SeoBrand;
  prompts: number;
  auto_poll: boolean | null;
  poll_interval_days: number | null;
  warning: string | null;
  next_steps?: string[];
}

/** Self-serve create: a name and the site's address, nothing else. GEO-editor
 *  only. The brand lands with NO questions and the scheduled check OFF.
 *
 *  Two refusals are ordinary answers here rather than failures, and the caller
 *  must keep them apart by `apiStatus`: 409 = that brand is already in the
 *  shared registry, 422 = what was typed is not a brand. Both carry the
 *  backend's own sentence as the message. */
export const geoCreateBrand = (body: { name: string; url: string }) =>
  postJson<GeoBrandCreated>("/api/geo/brands", body);

export interface GeoPrompt {
  id: string;
  text: string;
  intent: "brand" | "category" | "problem";
  stage: "awareness" | "consideration" | "purchase";
  enabled: boolean;
  source?: "ai" | "custom";   // custom = team-written, survives regeneration
  /** buyer-persona key; "" or absent = untagged (prompts saved before personas existed) */
  persona?: string;
}

/** A buyer persona the prompt universe is segmented by. */
export interface GeoPersona {
  key: string;
  label: string;
  description: string;
}

/** The prompt-universe document, as every prompts endpoint returns it.
 *  `personas` is optional on the wire — a backend from before personas existed
 *  simply does not send it. */
export interface GeoPromptUniverse {
  brand_id: string;
  prompts: GeoPrompt[];
  personas?: GeoPersona[];
  updated_at?: string;
}

/** Which engines a new question is sent to — and therefore what it costs.
 *
 *  A question that does not name the brand is also put to Google's search
 *  engines, and every one of those calls is billed; a question that already
 *  names the brand goes only to the chat engines. Both create endpoints now
 *  REQUIRE it, and it is required here too: the client-side default of
 *  "category" was quietly buying search calls for every hand-typed question.
 *
 *  Deliberately narrower than `GeoPrompt["intent"]`. The generator still writes
 *  "problem" questions and the store still holds them, so they keep displaying
 *  and keep round-tripping through a save — they are simply not a thing a
 *  person is offered when writing one. */
export type GeoAskedIntent = "category" | "brand";

export const geoAddCustomPrompt = (brandId: string, text: string, intent: GeoAskedIntent) =>
  postJson<GeoPromptUniverse>(`/api/geo/brands/${brandId}/prompts/custom`, { text, intent });

/** One pasted list, one prompt per line. Partial acceptance is the NORMAL
 *  outcome and answers 200: `skipped` carries the per-line reasons, and
 *  `total` is the universe size after the paste, not the number added. */
export interface GeoPromptsBulkResult {
  added: GeoPrompt[];
  skipped: { text: string; reason: string }[];
  total: number;
  universe: GeoPromptUniverse;
}

export const geoAddPromptsBulk = (
  brandId: string,
  body: { text: string; intent: GeoAskedIntent; persona?: string; stage?: string },
) => postJson<GeoPromptsBulkResult>(`/api/geo/brands/${brandId}/prompts/bulk`, body);

/** Replace the persona list; an empty list clears it. Prompts tagged with a
 *  persona that disappears here are untagged in the same write. Omitting `key`
 *  lets the store slug one from the label. */
export const geoSavePersonas = (
  brandId: string,
  body: { personas: { key?: string; label: string; description?: string }[] },
) => putJson<GeoPromptUniverse>(`/api/geo/brands/${brandId}/personas`, body);

export interface GeoCompetitor {
  key: string;
  name: string;
  aliases: string[];
  /** what their citations are counted against. Optional: without it the
   *  comparison falls back to an alias that looks like a hostname, and if
   *  there is none their citation rate reads "no domain", not 0%. */
  domain?: string;
}

export interface GeoBrandConfig {
  brand_id: string;
  aliases: Record<string, string[]>;
  competitors: GeoCompetitor[];
  daily_cap: number;
  /** spend ceiling for the per-call SERP engines (AIO + AI Mode), joint across
   *  both, per calendar month */
  aio_monthly_cap?: number;
  /** days between scheduled sweeps (cron fires daily and honours this) */
  poll_interval_days?: number;
  auto_poll?: boolean;
  last_poll_completed_at?: string;
  /** The brand's registry flag, echoed back by `PUT config` — it lives on the
   *  shared brand record, not in this document. `false` is the removal: the
   *  brand leaves GEO, the SEO Analyst, Blog Writer and Issues at once, and
   *  every stored measurement, question set and Search Console grant is kept.
   *  `true` puts it back. Optional: a `GET config` predating it does not send
   *  it, and absent must read as on. */
  enabled?: boolean;
}

export interface GeoPollProgress {
  done: number;
  total: number;
  calls_used_today: number;
  daily_cap: number;
  capped: boolean;
  engines: string[];
  date: string;
  /** The backend's own stop signal: set when a whole batch errors or several
   *  consecutive batches fail. `done` counts only non-errored answers, so
   *  without this a dead provider key would never satisfy `done >= total`. */
  terminal: boolean;
  terminal_reason: string | null;
  /** WHICH refusal this is: `lease_held` (a check is running right now),
   *  `already_checked_today` (this brand's one manual check per day is used),
   *  `daily_cap` (today's engine budget is spent), `engine_failed` (the
   *  providers stopped answering). Null on a healthy step.
   *
   *  A code rather than something to match out of `terminal_reason`, for the
   *  reason the backend states: four refusals need four different sentences and
   *  four different offers, and prose is not an API. Never string-match the
   *  reason — read this. */
  stop_code: string | null;
  /** When the refusal clears: seconds away for `lease_held`, the next UTC
   *  midnight for the two day-keyed ones, null for a dead engine. It is what
   *  lets the button say when it frees up instead of sitting dead. */
  unlocks_at: string | null;
  /** The account already running a check on this brand. Set only alongside
   *  `stop_code: "lease_held"` — a refusal reserves nothing and bills nothing. */
  lease_held_by: string | null;
  /** The joint monthly SERP budget for AI Overviews and AI Mode is spent, so
   *  those two engines dropped out of this check and the rest carried on.
   *  `aio_credits_month` is what has been drawn against that budget so far. */
  aio_capped: boolean;
  aio_credits_month: number;
}

export interface GeoMentionStats {
  rate: number | null;
  stdev: number | null;
  n_prompts: number;
  n_answers: number;
}

export interface GeoMetricBlock {
  mention: GeoMentionStats;
  sov: {
    share: Record<string, number | null>;
    credit: Record<string, number>;
    unclaimed_answers: number;
    n_answers: number;
  };
  citation: { rate: number | null; n_answers_with_citations: number; cited_answers: number };
  source_mix: { domain: string; count: number; share: number }[];
  n_answers: number;
  /** rows a brand could actually have been named in — the denominator every
   *  rate above uses. Printing `n_answers` beside a rate describes two
   *  different populations. */
  n_measured?: number;
  n_errors: number;
  /** answers where Google published no AI Overview at all (AIO only) */
  n_no_aio?: number;
  /** what ONE check of this engine owes for the brand's current question list.
   *
   *  **Per check, where every other count in this block is per window** — the
   *  two are the same number only on a window holding a single check. Scale it
   *  by `GeoReport.n_sweeps` before using it as a denominator; pairing it with
   *  `n_answers` unscaled renders "350 of 70 asked".
   *
   *  It excludes nothing: a failed call and a query Google published no
   *  overview for are both inside it, so `n_answers` — not `n_measured` — is
   *  the numerator that matches it.
   *
   *  `null` from a caller with no question list in hand, absent from a backend
   *  that predates the field. Both mean "we cannot say how many were asked
   *  for", and neither means zero. */
  n_expected?: number | null;
  /** answers counted per measurement surface ("native" | "openrouter" |
   *  "serpapi" | "unknown") — every rate above is only as good as this */
  via_mix?: Record<string, number>;
}

export interface GeoPromptRollup {
  prompt_id: string;
  text: string;
  intent: string;
  /** persona key the prompt is tagged with; "" or absent = untagged */
  persona?: string;
  n: number;
  self_rate: number;
  cited_rate: number;
  rivals: { key: string; count: number }[];
  engines_hit: string[];
}

/** Per buyer persona: how often the brand is named and cited on that persona's
 *  questions. The `""` persona is the unassigned bucket — answers polled before
 *  prompts carried a persona — and stays visible on purpose. */
export interface GeoPersonaRollup {
  persona: string;
  n_prompts: number;
  n_answers: number;
  mention_rate: number | null;
  cited_rate: number | null;
}

export interface GeoReport {
  brand_id: string;
  days: number;
  blended: GeoMetricBlock;
  engines: Record<string, GeoMetricBlock>;
  source_gap: { domain: string; count: number; example_prompt_ids: string[] }[];
  competitors: Record<string, GeoMentionStats>;
  competitor_names: Record<string, string>;
  /** engine -> ISO time it last produced a usable answer, even if that fell
   *  outside this report's window */
  engine_last_seen?: Record<string, string>;
  /** checks this brand actually ran inside `days`. What scales the per-check
   *  `n_expected` into a window expectation; zero is a real answer (no check in
   *  this period) and is never a divisor. Optional per the deploy-skew law. */
  n_sweeps?: number;
  /** the month's billed-search budget is spent, so Google's two engines are
   *  dropped from checks and their counts are frozen.
   *
   *  Read THESE, not `GeoPollProgress.aio_capped`: the `aio_*` names are
   *  compatibility aliases for the same facts, and they read as though only AI
   *  Overview were affected when the guard covers both Google engines. Being
   *  here rather than only on a poll step is what lets the pause survive a
   *  refresh — a brand whose credit ran out three weeks ago used to open as an
   *  ordinary stale number. */
  search_credit_spent?: boolean;
  search_credit_used?: number;
  search_credit_limit?: number;
  /** when the pause began; null unless it is spent right now */
  serp_capped_since?: string | null;
  prompt_rollup?: GeoPromptRollup[];
  /** optional per deploy-skew law: absent from a backend that predates personas */
  persona_rollup?: GeoPersonaRollup[];
}

export interface GeoAnswer {
  engine: string;
  model: string;
  text: string;
  citations: { url: string; domain: string; title: string }[];
  latency_ms: number;
  error: string | null;
  prompt_id: string;
  prompt_text: string;
  intent: string;
  run: number;
  at: string;
  mentions?: Record<string, number>;
  brand_mentioned?: boolean;
  brand_position?: number | null;
  brand_cited?: boolean;
  sentiment?: "positive" | "neutral" | "negative" | null;
  via?: string;       // "native" | "openrouter" | "serpapi" | "dataforseo" — the surface measured
  no_aio?: boolean;   // Google showed no AI Overview for this query (excluded from rates)
  /** persona key of the prompt at poll time; "" or absent = untagged */
  persona?: string;
}

/** Where the brand's sweep stands when nobody is watching a progress bar.
 *  `next_due_at` is null only when the brand has never completed a sweep — an
 *  invented date would be worse than saying so. */
export interface GeoPollStatus {
  brand_id: string;
  pending: number;
  done: number;
  total: number;
  auto_poll: boolean;
  interval_days: number;
  last_completed_at: string | null;
  next_due_at: string | null;
  due_now: boolean;
  due_reason: string;
  /** What "Check now" may do *before* anyone presses it. One manual check per
   *  brand per day, whoever clicks; these say whether today's is spent, who
   *  spent it, and when the next unlocks. Optional because Vercel puts the
   *  frontend live four to six minutes before Cloud Run has the matching
   *  backend: absent must read as "not used", never as "blocked". */
  manual_check_used?: boolean;
  manual_check_by?: string | null;
  manual_check_unlocks_at?: string | null;
}

export const geoPollStatus = (brandId: string, req?: RequestOptions) =>
  getJson<GeoPollStatus>(`/api/geo/brands/${brandId}/poll/status`, req);

export const geoConfig = (opts?: RequestOptions) =>
  getJson<GeoGlobalConfig>("/api/geo/config", opts);

export const geoBrands = (req?: RequestOptions) =>
  getJson<{ brands: GeoBrandRow[] }>("/api/geo/brands", req);

export const geoPrompts = (brandId: string, req?: RequestOptions) =>
  getJson<GeoPromptUniverse>(`/api/geo/brands/${brandId}/prompts`, req);

export const geoGeneratePrompts = (brandId: string) =>
  postJson<GeoPromptUniverse>(
    `/api/geo/brands/${brandId}/prompts/generate`, {},
  );

/** Replace the universe whole. A prompt whose `persona` key is ABSENT keeps
 *  whatever persona the store already has for it (an editor built before
 *  personas existed must not untag the universe by round-tripping it);
 *  `persona: ""` untags explicitly. */
export async function geoSavePrompts(brandId: string, prompts: GeoPrompt[]): Promise<GeoPromptUniverse> {
  return putJson<GeoPromptUniverse>(`/api/geo/brands/${brandId}/prompts`, { prompts });
}

export const geoBrandConfig = (brandId: string) =>
  getJson<GeoBrandConfig>(`/api/geo/brands/${brandId}/config`);

/** Patch the brand's GEO config. An omitted field is left untouched.
 *
 *  `enabled` is the odd one out and is deliberately in the same list: it writes
 *  the SHARED brand registry rather than this config document, so `false` here
 *  removes the brand from the SEO Analyst, Blog Writer and Issues as well as
 *  from GEO — reversibly, and with every stored measurement kept. It is the
 *  only field on this route a reader must be warned about before pressing.
 *  GEO-editor only; a member gets 403. */
export async function geoSaveBrandConfig(
  brandId: string,
  patch: Partial<Pick<GeoBrandConfig,
    "aliases" | "competitors" | "daily_cap" | "aio_monthly_cap" | "poll_interval_days"
    | "auto_poll" | "enabled">>,
): Promise<GeoBrandConfig> {
  return putJson<GeoBrandConfig>(`/api/geo/brands/${brandId}/config`, patch);
}

/** One resumable slice of a check: up to `batch_size` real, paid engine calls,
 *  bounded server-side so the request returns rather than holding the line for
 *  a whole sweep. A full check is many of these, driven by the loop in
 *  `components/hub/work/geo/pollLoop.ts`.
 *
 *  `poll_token` names the LOOP, not the step: the same value on every step of
 *  one press of Check now is what holds the brand's lease from the first batch
 *  to the last, so a second person is refused consistently instead of the two
 *  of them trading the brand between batches. Omitting it is still valid and
 *  falls back to per-step leasing — the backend cleans the value and never
 *  rejects it, so a token is never worth a 422 in the middle of a check. */
export const geoPollStep = (
  brandId: string,
  body: { engines?: string[]; runs?: number; batch_size?: number; poll_token?: string } = {},
  req?: RequestOptions,
) => postJson<GeoPollProgress>(`/api/geo/brands/${brandId}/poll/step`, body, req);

export const geoReport = (brandId: string, days = 7, req?: RequestOptions) =>
  getJson<GeoReport>(`/api/geo/brands/${brandId}/report?days=${days}`, req);

export const geoAnswers = (
  brandId: string,
  opts: { prompt_id?: string; engine?: string; days?: number } = {},
  req?: RequestOptions,
) => {
  const p = new URLSearchParams();
  if (opts.prompt_id) p.set("prompt_id", opts.prompt_id);
  if (opts.engine) p.set("engine", opts.engine);
  if (opts.days) p.set("days", String(opts.days));
  const qs = p.toString();
  return getJson<{ answers: GeoAnswer[]; total: number }>(
    `/api/geo/brands/${brandId}/answers${qs ? `?${qs}` : ""}`,
    req,
  );
};

/* ------------- GEO competitor comparison (a10) --------------------------- */

/** One tracked entity — us or a rival — scored on exactly the answers the
 *  brand was scored on, so the two can be read side by side. */
export interface GeoComparisonRow {
  key: string;
  name: string;
  is_self: boolean;
  /** the domain their citations are counted against; "" = none on record */
  domain: string;
  mention: GeoMentionStats;
  /** null = no domain on record, so the citation rate is UNKNOWN. Never draw
   *  it as a zero — that reads as "never cited", which we did not measure. */
  citation: { rate: number | null; n_answers_with_citations: number; cited_answers: number } | null;
  sov_share: number | null;
  sov_credit: number | null;
  /** mean 1-based order they are named in; lower is better, null = never named */
  avg_position: number | null;
  /** the exact strings this rate was matched on, derived from the name and
   *  domain. A 0% beside the names we searched for is debuggable; a bare 0%
   *  sends someone hunting for a bug that is really a spelling.
   *  OPTIONAL on purpose: a browser can be running this build against the
   *  previous API for minutes after a deploy — see `matchNames`. */
  match_names?: string[];
  per_engine: Record<string, number | null>;
  /** Question-level scoreboard against us; null on our own row.
   *  Compared by RATE, not by "appeared at least once" — presence saturates
   *  over a week and would score every question a tie. */
  vs_self: {
    n_prompts: number;
    ahead: number;        // questions the engines name us on more often
    behind: number;       // questions they own
    tied: number;         // same rate, both present
    both_absent: number;  // open ground: neither of us is ever named
    behind_prompt_ids: string[];
  } | null;
}

export interface GeoQuestionRow {
  prompt_id: string;
  text: string;
  intent: string;
  n: number;
  rates: Record<string, number>;
  self_rate: number;
  rivals_ahead: { key: string; name: string; rate: number }[];
  leader: string;
  engines: string[];
}

/** A domain cited on our questions that belongs to nobody we track yet — the
 *  discovery half: who else is in the answer. */
export interface GeoUntrackedDomain {
  domain: string;
  count: number;
  answers_you_absent: number;
  /** distinct questions the domain was cited on (optional per deploy-skew law) */
  n_questions?: number;
  example_prompt_ids: string[];
}

export interface GeoComparison {
  brand_id: string;
  days: number;
  entities: string[];
  names: Record<string, string>;
  domains: Record<string, string>;
  rows: GeoComparisonRow[];
  questions: GeoQuestionRow[];
  untracked_domains: GeoUntrackedDomain[];
  n_answers: number;
  n_measured: number;
  tracked_competitors: number;
}

export const geoComparison = (brandId: string, days = 7, req?: RequestOptions) =>
  getJson<GeoComparison>(`/api/geo/brands/${brandId}/comparison?days=${days}`, req);

/** What a re-read of stored answers changed. Zero engine calls: nothing is
 *  re-asked, only re-parsed with the current competitor list. */
export interface GeoRescanResult {
  brand_id: string;
  days: number;
  answers_scanned: number;
  answers_updated: number;
  days_updated: string[];
  entities: string[];
  /** characters of each answer that were stored — a name past this point was
   *  never kept and only a real poll can recover it */
  text_cap: number;
}

export const geoRescan = (brandId: string, days = 7) =>
  postJson<GeoRescanResult>(`/api/geo/brands/${brandId}/rescan`, { days });

/* ------------- GEO score history (a10 performance dashboard) ------------- */

/** One completed sweep. `score` is null when nothing in it was measurable —
 *  an empty sweep scores nothing, it does not score zero. */
export interface GeoHistoryPoint {
  date: string;              // YYYYMMDD
  at: string;
  source: "sweep" | "backfill";
  score: number | null;
  components: Record<string, number>;
  /** renormalised over the components that COULD be measured, so the bars add
   *  up to the score above them */
  weights: Record<string, number>;
  missing: string[];
  mention_rate: number | null;
  /** Measured over answers that carry citations AT ALL — a smaller population
   *  than `n_measured`. It cannot be combined with `mention_rate` to derive how
   *  many answers both named and linked you; use `n_named` / `n_named_cited`,
   *  which are counted over one denominator. */
  citation_rate: number | null;
  sov_self: number | null;
  n_measured: number;
  /** Answers that named you, out of `n_measured`. Absent on points stored
   *  before the split was recorded — the panel must then draw no split rather
   *  than invent one. */
  n_named?: number;
  /** Answers that named you AND linked your site, out of `n_measured`. */
  n_named_cited?: number;
  n_answers: number;
  n_prompts: number;
  engines: Record<string, number | null>;
  competitors: Record<string, number | null>;
  /** measured on far fewer answers than the series' best — real, but thin */
  partial?: boolean;
}

export interface GeoTrendMove {
  change: number | null;
  direction: "up" | "down" | "flat" | "unknown";
}

/** One ISO week of sweeps, derived on read from the per-sweep points. A week
 *  with no sweep is ABSENT rather than drawn as zero, so `delta_score` is
 *  against the previous week that actually has a score. */
export interface GeoWeeklyPoint {
  week: string;            // e.g. "2026-W35"
  start: string;           // YYYYMMDD of that week's Monday
  score: number | null;
  mention_rate: number | null;
  citation_rate: number | null;
  n_sweeps: number;
  /** every sweep that week was thin — the bar is provisional, not a cliff */
  all_partial: boolean;
  delta_score: number | null;
}

/** One sweep's own record: when it ran, what stopped it, which engines it
 *  reached — what a human reads when a chart point looks wrong. */
export interface GeoRunLogEntry {
  id: string;
  recorded_at: string;
  day: string;             // YYYYMMDD
  /** null when nothing in the sweep produced a timestamped record */
  started_at: string | null;
  finished_at: string;
  duration_s: number | null;
  trigger: string;         // "cron" | "manual" — who started it
  steps: number;
  done: number;
  total: number;
  completed: boolean;
  /** "completed", or the terminal reason when the sweep stopped short */
  stopped_because: string;
  terminal_reason: string | null;
  /** engines that produced at least one usable answer, in the panel's order */
  engines: string[];
  calls: number;
  /** engine id -> calls that errored */
  errors: Record<string, number>;
  /** queries where Google published no AI answer at all (not an error) */
  no_aio: number;
  score: number | null;
  /** where the Action Plan stood when this sweep ran; null = no plan yet */
  plan_progress: { done: number; total: number } | null;
}

export interface GeoHistory {
  brand_id: string;
  days: number;
  points: GeoHistoryPoint[];
  /** BOTH optional per the deploy-skew law: a backend from before the rollup
   *  and run log existed simply does not send them. */
  weekly?: GeoWeeklyPoint[];
  runs?: GeoRunLogEntry[];
  trend: {
    current: GeoHistoryPoint | null;
    previous: GeoHistoryPoint | null;
    first: GeoHistoryPoint | null;
    since_last: GeoTrendMove;
    since_start: GeoTrendMove;
    n_points: number;
  };
  component_labels: Record<string, string>;
  min_point_answers: number;
  names: Record<string, string>;
  backfill_days: number;
}

export const geoHistory = (brandId: string, days = 90, req?: RequestOptions) =>
  getJson<GeoHistory>(`/api/geo/brands/${brandId}/history?days=${days}`, req);

/* --------------------- GEO Action Plan (a10 strategy) -------------------- */

/** The place an action happens, resolved from the discovered venue list rather
 *  than from whatever the strategist model typed. `null` = work on our own
 *  site. Never a name the backend could not verify — those actions are dropped
 *  before they reach here. */
export interface GeoStrategyVenue {
  name: string;
  url: string;
  kind: "community" | "listicle" | "review" | "forum" | "video";
  cited_where_absent: number;
  examples: { title: string; url: string }[];
}

export interface GeoStrategyAction {
  id: string;
  title: string;
  venue: GeoStrategyVenue | null;
  deliverable: string;
  /** 2-4 short imperative steps; empty on plans saved before steps existed */
  steps?: string[];
  detail: string;
  owner_role: string;
  effort: "low" | "medium" | "high";
  impact: "low" | "medium" | "high";
  kpi: string;
  /** the model's KPI was off-list and was coerced to the nearest allowed one —
   *  the target sentence may read slightly off its KPI */
  kpi_coerced?: boolean;
  target: string;
  /** what doing this is expected to move, in the plan's own words */
  expected_impact?: string;
  why_evidence: string;
  status: "todo" | "in_progress" | "done" | "skipped";
  status_at?: string;
  /** free text — a name, an email, "the agency"; "" or absent = unassigned */
  assignee?: string;
  assigned_at?: string;
}

/** A fortnight of work. Waves arrive calendar-ordered from the backend. */
export interface GeoStrategyWave {
  weeks: string;
  title: string;
  objective: string;
  why_evidence: string;
  actions: GeoStrategyAction[];
}

export interface GeoStrategyBaseline {
  measured_at: string;
  n_answers: number;
  mention_rate: number;
  citation_rate: number;
  sov_self: number;
  aio_named_rate: number;
  aio_cited_rate: number;
  source_gap_top_count: number;
  missing_questions_count: number;
  winning_questions_count: number;
  [key: string]: unknown;
}

export interface GeoStrategy {
  summary: string;
  waves: GeoStrategyWave[];
  monitoring: { cadence: string; review_ritual: string; leading_indicators: string[] };
  expectations: string;
  baseline: GeoStrategyBaseline;
  /** provenance of the venue list this plan was built from */
  venues?: {
    category: string;
    counts: Record<string, number>;
    searched: number;
    complete: boolean;
    errors: string[];
  };
  /** actions refused because they named a venue we could not verify — shown,
   *  not swallowed: a plan that silently shrank is worth investigating */
  dropped_actions?: { title: string; venue: string; reason: string }[];
  generated_at: string;
}

export interface GeoStrategyDoc {
  brand_id: string;
  current: GeoStrategy | null;
  history?: { generated_at: string; summary: string }[];
}

export const geoStrategyGet = (brandId: string, req?: RequestOptions) =>
  getJson<GeoStrategyDoc>(`/api/geo/brands/${brandId}/strategy`, req);

export const geoStrategyGenerate = (brandId: string) =>
  postJson<GeoStrategyDoc>(`/api/geo/brands/${brandId}/strategy/generate`, {});

/** Move an action and/or hand it to someone. Either field alone is a valid
 *  request; `assignee: ""` clears the assignment. */
export async function geoStrategyActionUpdate(
  brandId: string, actionId: string,
  body: { status?: GeoStrategyAction["status"]; assignee?: string },
): Promise<GeoStrategyDoc> {
  return putJson<GeoStrategyDoc>(
    `/api/geo/brands/${brandId}/strategy/actions/${actionId}`,
    body,
  );
}

/** @deprecated Use `geoStrategyActionUpdate` — same endpoint, wider body. */
export const geoStrategyActionStatus = (
  brandId: string, actionId: string, status: GeoStrategyAction["status"],
): Promise<GeoStrategyDoc> => geoStrategyActionUpdate(brandId, actionId, { status });

/* ------------- GEO Content Optimizer (a10, Layers 1-6) ------------------- */

export interface OptimizerGap { kind: string; priority: number; message: string }

export interface OptimizerTermEntry {
  term: string;
  display: string;
  importance: number;
  prevalence: number;
  range: [number, number];
  confidence: string;
  brand?: boolean;
}

export interface OptimizerSubtopicCoverage {
  label: string;
  covered: boolean;
  best_sim: number;
  evidence: string;
}

export interface OptimizerBand {
  feature: string;
  n: number;
  kind: string;
  lo?: number | null;
  hi?: number | null;
  median?: number | null;
  modes?: number[] | null;
  confidence: string;
  note: string;
}

export interface OptimizerReport {
  total: number;
  term_coverage: number;
  semantic_coverage: number | null;
  structure_fit: number;
  stuffing_penalty: number;
  winners_median: number | null;
  degraded: string[];
  gaps: OptimizerGap[];
  /** the mirror of `gaps`: what the draft already does the way the winners do.
   *  Optional per deploy-skew law — absent on reports scored before it existed. */
  strengths?: { kind: string; message: string }[];
  draft_features: Record<string, number>;
  subtopic_coverage: OptimizerSubtopicCoverage[];
  draft_term_counts: Record<string, number>;
}

export interface OptimizerAnalysis {
  meta: {
    analysis_id: string;
    keyword: string;
    locale: string;
    created_at: string;
    n_docs: number;
    article_share: number;
    warnings: string[];
    degraded: string[];
    volatility: string;
    aio_present: boolean;
    paa: string[];
  };
  results: { rank: number; url: string; title: string; page_type: string; excluded: string; flags: string[] }[];
  term_profile: OptimizerTermEntry[];
  subtopics: { label: string; suggested_heading: string; doc_idxs: number[] }[];
  structure_bands: Record<string, OptimizerBand>;
  winners_median_score: number | null;
  last_report?: OptimizerReport;
  disclaimer: string;
}

export interface OptimizerIndexRow {
  id: string;
  keyword: string;
  locale: string;
  created_at: string;
  n_docs: number;
  score: number | null;
  /** the page-check verdict label, when the analysis carries one. Optional AND
   *  nullable: rows written before page checks existed do not send it. */
  verdict?: string | null;
  /** the checked page's URL; "" when a pasted draft was checked */
  source_url?: string;
}

/* ------------- GEO Page check (a10) -------------------------------------- */

/** "Will publishing this page help or hurt?" — the whole read, brand-scoped.
 *  Replaces the old un-scoped /api/geo/optimizer/* endpoints, which are
 *  DELETED from the backend. */

export interface GeoPageCheckVerdict {
  label: "likely helps" | "needs work" | "likely cannibalizes" | "cannot tell";
  reasons: string[];
  confidence: "high" | "medium" | "low";
}

export interface GeoPageCheckBlock {
  /** "" when a pasted draft was checked rather than a live URL */
  source_url: string;
  target_query: string;
  /** where the target query came from — "given" is the user's own keyword */
  target_query_source: "given" | "page_title" | "draft_heading";
  verdict: GeoPageCheckVerdict;
  pros: { kind: string; message: string }[];
  /** gaps plus unanswered People-Also-Ask questions; `priority` is present on
   *  the gap-derived rows */
  cons: { kind: string; priority?: number; message: string }[];
  cannibalization: {
    risk: "high" | "medium" | "low" | "unknown";
    evidence: { kind: "serp" | "corpus" | "gsc"; url: string; detail: string }[];
    note: string;
  };
  page_flags: string[];
  checked_at: string;
  disclaimer: string;
}

/** The full analysis document: the optimizer snapshot plus the page-check
 *  block. `page_check` is optional — analyses stored by the old optimizer
 *  never got one. */
export interface GeoPageCheckDoc extends OptimizerAnalysis {
  page_check?: GeoPageCheckBlock;
}

/** One page (URL) or one draft against today's winners for its target query,
 *  plus a cannibalization read against the brand's own pages. Exactly one of
 *  `url` / `draft`. Costs a SERP sweep; re-scoring against the pinned snapshot
 *  (`geoPageCheckRescore`) never re-spends. */
export const geoPageCheck = (
  brandId: string,
  body: { url?: string; draft?: string; keyword?: string; locale?: string },
) => postJson<GeoPageCheckDoc>(`/api/geo/brands/${brandId}/page-check`, body);

export const geoPageChecks = (brandId: string, req?: RequestOptions) =>
  getJson<{ analyses: OptimizerIndexRow[] }>(`/api/geo/brands/${brandId}/page-checks`, req);

export const geoPageCheckGet = (brandId: string, id: string) =>
  getJson<GeoPageCheckDoc>(`/api/geo/brands/${brandId}/page-checks/${id}`);

/** Deterministic re-score of an edited draft against the PINNED snapshot — no
 *  new SERP call. Refresh = run `geoPageCheck` again explicitly. */
export const geoPageCheckRescore = (brandId: string, id: string, body: { draft: string }) =>
  postJson<OptimizerReport>(`/api/geo/brands/${brandId}/page-checks/${id}/rescore`, body);

/* ------------- Issues (the console's own record of what is wrong) -------- */

export interface IssueFix {
  label: string;
  /** workspace slug the fix lives in — "seo" | "geo" */
  workspace: string;
  /** the subject to open it on — a brand id */
  subject: string;
  /** a section id of that workspace */
  section: string;
}

export interface Issue {
  /** stable across reads — hash of (area, brand, code), so a row can be keyed */
  id: string;
  severity: "high" | "medium" | "low";
  /** "seo" | "geo" | "runs" */
  area: string;
  brand_id: string;
  brand: string;
  code: string;
  title: string;
  detail: string;
  /** where to go to fix it; null = nowhere specific to send anyone */
  fix: IssueFix | null;
  since: string | null;
}

/** Most severe first. A source that could not be read arrives as a
 *  low-severity issue of its own — never as an empty, healthy-looking list. */
export interface IssuesPayload {
  issues: Issue[];
  counts: { high: number; medium: number; low: number };
  generated_at: string;
}

export const getIssues = (req?: RequestOptions) =>
  getJson<IssuesPayload>("/api/issues", req);

/* ------------- Schedule (the cron jobs that drive the agents) ------------ */

export interface CronSchedule { cron: string; timezone: string }
export interface CronAttempt { time: string; ok: boolean }

export interface CronJob {
  id: string;
  /** the registry's curated name; null on a job the registry does not know */
  name: string | null;
  agent_id: string | null;
  agent_label: string | null;
  /** "POST /api/geo/cron/poll" form */
  endpoint: string;
  purpose: string | null;
  why_time: string | null;
  schedule: CronSchedule | null;
  state: "ENABLED" | "PAUSED" | null;
  last_attempt: CronAttempt | null;
  next_time: string | null;
  /** "live_only" = firing in production with no registry entry (name, purpose
   *  and why_time null); "registry_only" = expected by the backend but absent
   *  from the scheduler — a dead cron — with `schedule` carrying the expected
   *  values. When `scheduler_ok` is false EVERY row arrives "registry_only",
   *  because expectations were all that could be read. */
  origin: "live_registered" | "live_only" | "registry_only";
}

/** `scheduler_ok: false` is the honest-partial state: the rows are the
 *  registry's expectations only, and every live field is null. */
export interface CronJobsPayload {
  generated_at: string;
  scheduler_ok: boolean;
  scheduler_error: string | null;
  jobs: CronJob[];
}

export const getCronJobs = (req?: RequestOptions) =>
  getJson<CronJobsPayload>("/api/cron/jobs", req);

/* ------------- Agent health (the Agents panel's run figures) ------------- */

export interface AgentHealthModel {
  id: string | null;
  name: string | null;
  /** Where the effective model came from: a per-agent override, the workspace
   *  default, or the environment. Null when nothing could say. */
  source: "agent" | "global" | "env" | null;
}

export interface AgentHealthError {
  at: string;
  message: string;
}

export interface AgentHealthUser {
  id: string;
  name: string;
}

/** One specialist's record over the payload's window. Workspace-shared: every
 *  signed-in reader sees the same figures. `success_rate` is never rendered —
 *  the panel's sentences are built from `runs` / `ok` / `errors` directly, so
 *  the words can never disagree with the figures they came from. */
export interface AgentHealth {
  id: string;
  label: string;
  model: AgentHealthModel;
  runs: number;
  ok: number;
  errors: number;
  success_rate: number | null;
  last_run_at: string | null;
  last_error: AgentHealthError | null;
  /** The last three attempts in the window all failed. */
  recent_failing: boolean;
  users_count: number;
  /** Names always non-empty; capped at 10 — `users_count` carries the rest. */
  users: AgentHealthUser[];
}

export interface AgentsHealthPayload {
  generated_at: string;
  window_days: number;
  agents: AgentHealth[];
}

export const getAgentsHealth = (req?: RequestOptions) =>
  getJson<AgentsHealthPayload>("/api/agents/health", req);
