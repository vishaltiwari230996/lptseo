"use client";

import { useEffect, useRef, useState } from "react";
import { devLogin, googleLogin } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { BrandMark } from "@/lib/kit-ui";
declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential: string }) => void;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: Record<string, unknown>,
          ) => void;
        };
      };
    };
  }
}

const CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

/* Local-dev sign-in, added by the standalone extract. Running this offline
 * there is no Google Web Client ID to render a button against and no Firestore
 * to upsert a user into, so the Google path cannot complete and the panel
 * behind this screen is unreachable. `NEXT_PUBLIC_LOCAL_DEV_AUTH=1` offers the
 * `/api/auth/dev` door instead; the backend independently refuses it unless it
 * is itself in development mode, so setting this in a deployed frontend
 * achieves nothing but a 404. */
const DEV_AUTH = process.env.NEXT_PUBLIC_LOCAL_DEV_AUTH === "1";
const DEV_EMAIL = process.env.NEXT_PUBLIC_LOCAL_DEV_EMAIL || "";

function Logo() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
      <BrandMark size={40} title="Law Prep Tutorial" />
      {/* The logo sets "Law Prep" above "Tutorial" and gives the whole lockup one
          colour. A sign-in row is 40px tall, so it is set on one line here, with
          the split carrying the emphasis the stacking carries there. --blue-600
          is the brand primary: LPT red on light, LPT yellow on ink. */}
      <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 22, letterSpacing: "-0.01em", color: "var(--text-primary)" }}>
        Law Prep <span style={{ color: "var(--blue-600)" }}>Tutorial</span>
      </span>
    </div>
  );
}

/** `overlay` lays the card over the landing page instead of owning the
 *  viewport. Same card, same two sign-in doors — only the container changes,
 *  so there is one sign-in screen in this app and not two that drift. */
export default function LoginScreen({ overlay = false }: { overlay?: boolean }) {
  const { login } = useAuth();
  const buttonRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [devEmail, setDevEmail] = useState(DEV_EMAIL);
  const [devBusy, setDevBusy] = useState(false);

  async function signInLocally(event: React.FormEvent) {
    event.preventDefault();
    const address = devEmail.trim();
    if (!address || devBusy) return;
    setError(null);
    setDevBusy(true);
    try {
      const { token, user } = await devLogin(address);
      login(token, user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Local sign-in failed");
      setDevBusy(false);
    }
  }

  useEffect(() => {
    if (!CLIENT_ID) {
      // With the local-dev door open, a missing client id is the expected
      // state, not a fault — saying so in red just teaches the reader to
      // ignore this line. The dev panel below explains itself instead.
      if (!DEV_AUTH) setError("NEXT_PUBLIC_GOOGLE_CLIENT_ID is not set.");
      return;
    }

    let cancelled = false;

    async function handleCredential(response: { credential: string }) {
      try {
        const { token, user } = await googleLogin(response.credential);
        if (!cancelled) login(token, user);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Sign-in failed");
      }
    }

    function tryInit() {
      if (!window.google || !buttonRef.current) return false;
      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: handleCredential,
      });
      window.google.accounts.id.renderButton(buttonRef.current, {
        theme: "outline",
        size: "large",
        shape: "pill",
        text: "continue_with",
        logo_alignment: "center",
        width: 280,
      });
      return true;
    }

    if (!tryInit()) {
      const timer = setInterval(() => {
        if (tryInit()) clearInterval(timer);
      }, 150);
      setTimeout(() => clearInterval(timer), 8000);
      return () => {
        cancelled = true;
        clearInterval(timer);
      };
    }
    return () => {
      cancelled = true;
    };
  }, [login]);

  return (
    <div
      className={overlay ? undefined : "capp"}
      role={overlay ? "dialog" : undefined}
      aria-modal={overlay ? true : undefined}
      aria-label={overlay ? "Sign in" : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        ...(overlay
          ? { position: "fixed", inset: 0, zIndex: 61, padding: 16, overflowY: "auto" }
          : { background: "var(--grad-hero)" }),
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 420,
          margin: 16,
          padding: "40px 36px",
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-xl)",
          boxShadow: "var(--shadow-lg)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          textAlign: "center",
        }}
      >
        <Logo />
        <h1 style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 600, margin: "0 0 6px", letterSpacing: "-0.02em" }}>
          Own the search results law aspirants actually read.
        </h1>
        <p style={{ fontSize: 14, color: "var(--text-secondary)", margin: "0 0 28px", lineHeight: 1.5 }}>
          Sign in to run SEO and content strategy for Law Prep Tutorial.
        </p>

        {CLIENT_ID && (
          <div ref={buttonRef} style={{ minHeight: 44, display: "flex", alignItems: "center", justifyContent: "center" }} />
        )}

        {DEV_AUTH && (
          <form
            onSubmit={signInLocally}
            style={{
              width: "100%",
              display: "flex",
              flexDirection: "column",
              gap: 10,
              textAlign: "left",
              // Only a divider when there is something above to divide from.
              ...(CLIENT_ID
                ? { marginTop: 20, paddingTop: 20, borderTop: "1px solid var(--border)" }
                : {}),
            }}
          >
            <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
              Local development
            </span>
            <input
              type="email"
              value={devEmail}
              onChange={(e) => setDevEmail(e.target.value)}
              placeholder="you@lawpreptutorial.com"
              aria-label="Email address to sign in as"
              autoComplete="off"
              spellCheck={false}
              required
              style={{
                width: "100%",
                boxSizing: "border-box",
                padding: "10px 12px",
                fontSize: 14,
                fontFamily: "inherit",
                color: "var(--text-primary)",
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-md)",
              }}
            />
            <button
              type="submit"
              disabled={devBusy || !devEmail.trim()}
              style={{
                width: "100%",
                padding: "10px 12px",
                fontSize: 14,
                fontWeight: 600,
                fontFamily: "inherit",
                // Neutral rather than brand-filled: --blue-600 is LPT yellow
                // in dark mode, so white-on-brand would be unreadable there.
                color: "var(--text-primary)",
                background: "transparent",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-pill)",
                cursor: devBusy ? "progress" : "pointer",
                opacity: devBusy || !devEmail.trim() ? 0.55 : 1,
              }}
            >
              {devBusy ? "Signing in…" : "Continue without Google"}
            </button>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
              Signs in against the local backend with no Google verification. The
              address must still pass the server&apos;s sign-in allowlist.
            </span>
          </form>
        )}

        {error && (
          <p style={{ color: "var(--danger)", fontSize: 12, fontWeight: 500, marginTop: 16 }}>{error}</p>
        )}

        <p style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 24 }}>
          Secure sign-in with Google. No passwords stored.
        </p>
      </div>
    </div>
  );
}
