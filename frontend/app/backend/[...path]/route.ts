/* Same-origin relay to the Cloud Run backend, authenticated as the project's
 * own service account.
 *
 * The org's Domain Restricted Sharing policy forbids `allUsers` on Cloud Run —
 * and silently re-strips it when granted — so the browser can never call the
 * backend directly in production. It CAN call this route (same origin as the
 * page), and this route invokes Cloud Run with a Google identity token minted
 * from GCP_SA_KEY. The token rides in X-Serverless-Authorization, which Cloud
 * Run verifies and strips, leaving the app's own Authorization header intact
 * for FastAPI's JWT auth. Bodies are streamed both ways, so responses are not
 * buffered against the platform's body-size ceiling.
 *
 * Local dev never comes through here: NEXT_PUBLIC_API_URL is unset there, so
 * lib/api.ts talks to localhost:8080 directly. Production sets it to
 * "/backend", which lands every /backend/api/* request on this file.
 */
import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const UPSTREAM =
  process.env.BACKEND_ORIGIN ||
  "https://agentsbackend-255561670915.us-central1.run.app";

import { GoogleAuth } from "google-auth-library";

// google-auth-library caches and refreshes the identity token per warm
// function instance; hand-rolled signing is exactly the kind of crypto that
// fails only in production, so the official client does it.
let auth: GoogleAuth | null = null;

async function identityToken(): Promise<string> {
  const raw = process.env.GCP_SA_KEY;
  if (!raw) throw new Error("GCP_SA_KEY is not configured on this deployment");
  auth ||= new GoogleAuth({ credentials: JSON.parse(raw) });
  const client = await auth.getIdTokenClient(UPSTREAM);
  return client.idTokenProvider.fetchIdToken(UPSTREAM);
}

async function relay(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await ctx.params;
  const search = new URL(req.url).search;
  const target = `${UPSTREAM}/${path.map(encodeURIComponent).join("/")}${search}`;

  const headers = new Headers(req.headers);
  // hop-by-hop and origin-bound headers must not be forwarded
  for (const h of ["host", "connection", "content-length", "accept-encoding"]) {
    headers.delete(h);
  }
  let token: string;
  try {
    token = await identityToken();
  } catch (exc) {
    return Response.json(
      { detail: exc instanceof Error ? exc.message : "backend relay not configured" },
      { status: 503 },
    );
  }
  headers.set("X-Serverless-Authorization", `Bearer ${token}`);

  const body = req.method === "GET" || req.method === "HEAD" ? undefined : req.body;
  const upstream = await fetch(target, {
    method: req.method,
    headers,
    body,
    redirect: "manual",
    // required by undici whenever a request body is a stream
    ...(body ? { duplex: "half" as const } : {}),
  });

  const out = new Headers(upstream.headers);
  // recomputed by the platform for the re-streamed body
  for (const h of ["content-encoding", "content-length", "transfer-encoding"]) {
    out.delete(h);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export {
  relay as GET,
  relay as POST,
  relay as PUT,
  relay as PATCH,
  relay as DELETE,
  relay as HEAD,
};
