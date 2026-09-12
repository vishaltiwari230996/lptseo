"use client";

/* Standalone shell for the extracted SEO agent.
 *
 * In the AgentOS console this panel is reached through AgentHub: `PanelSwitch`
 * renders it inside `<Legacy>` (the `.legacy` wrapper that pins `--surface` for
 * the old design system) and `ConsoleApp` supplies the toast rail. Neither of
 * those is part of a SEO-only extract, so this page reproduces the two pieces
 * the panel actually depends on — the wrapper class and `onToast` — and nothing
 * else. The toast behaviour below is lifted from `ConsoleApp` unchanged: errors
 * stack and never auto-dismiss, everything else replaces the previous transient.
 *
 * `onBack` has nowhere to go here — there is no agent grid to return to — so it
 * is a no-op.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import Landing from "@/components/Landing";
import SeoAgent from "@/components/console/seo/SeoAgent";
import { Icon } from "@/lib/kit-ui";
import type { ToastFn, ToastTone } from "@/components/console/ConsoleApp";

interface ToastItem {
  id: number;
  msg: string;
  tone: ToastTone;
}

// `alert-circle` is not registered in lib/icons.tsx, so the error badge is an
// ✕ carrying the danger colour — unmistakably not a success check.
const TOAST_ICON: Record<ToastTone, string> = {
  ok: "check",
  warn: "alert-triangle",
  error: "x",
};

/** 0 = never auto-dismiss. An error the user did not see is an error they act on. */
const TOAST_TTL_MS: Record<ToastTone, number> = { ok: 2600, warn: 6000, error: 0 };

/** Errors stack, but not without limit — a failing loop must not paper the screen. */
const MAX_TOASTS = 4;

function ToastStack({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
  if (!toasts.length) return null;
  return (
    <div className="ctoasts">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`ctoast ctoast--${t.tone}`}
          role={t.tone === "error" ? "alert" : "status"}
          aria-live={t.tone === "error" ? "assertive" : "polite"}
        >
          <span className="ctoast__ic">
            <Icon name={TOAST_ICON[t.tone]} />
          </span>
          <span className="ctoast__msg">{t.msg}</span>
          {t.tone === "error" && (
            <button type="button" className="ctoast__dismiss" onClick={() => onDismiss(t.id)}>
              Dismiss
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

export default function Page() {
  const { user, ready } = useAuth();
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastTimers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const toastSeq = useRef(0);
  const transientToast = useRef<number | null>(null);

  const dismissToast = useCallback((id: number) => {
    const timer = toastTimers.current.get(id);
    if (timer) clearTimeout(timer);
    toastTimers.current.delete(id);
    if (transientToast.current === id) transientToast.current = null;
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const fire = useCallback<ToastFn>((msg, tone = "ok") => {
    const id = (toastSeq.current += 1);
    if (tone !== "error") {
      const prev = transientToast.current;
      if (prev !== null) {
        const timer = toastTimers.current.get(prev);
        if (timer) clearTimeout(timer);
        toastTimers.current.delete(prev);
      }
      transientToast.current = id;
    }
    setToasts((list) => {
      const kept = tone === "error" ? list : list.filter((t) => t.tone === "error");
      return [...kept, { id, msg, tone }].slice(-MAX_TOASTS);
    });
    const ttl = TOAST_TTL_MS[tone];
    if (ttl > 0) {
      toastTimers.current.set(id, setTimeout(() => dismissToast(id), ttl));
    }
  }, [dismissToast]);

  useEffect(() => {
    const timers = toastTimers.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  // Auth resolves from localStorage after mount, so there is a moment before
  // either answer is known. Showing the sign-in screen during it would flash a
  // login at somebody who is already signed in.
  if (!ready) {
    return (
      <main className="boot" aria-busy="true">
        <span className="boot__spin" aria-hidden="true" />
        <span className="sr">Opening the SEO agent</span>
      </main>
    );
  }

  // Signed out lands on the marketing page, which opens sign-in over itself.
  if (!user) return <Landing />;

  return (
    <>
      <div className="legacy">
        <SeoAgent onToast={fire} onBack={() => {}} />
      </div>
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </>
  );
}
