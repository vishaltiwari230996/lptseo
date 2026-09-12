"use client";

/* The hero background: a growth curve, drawn live.
 *
 * WHAT IT SHOWS. Search traffic compounding — a rising area chart with the
 * detail scrolling through it, a dimmer second series trailing behind, and a
 * marker riding the leading edge. The subject is the product's, not the
 * client's: this console is about growth, so the hero is growth. It replaced a
 * montage of law-school footage, which was about the customer's business rather
 * than about what this thing actually does.
 *
 * WHY IT IS DRAWN AND NOT FILMED. Two reasons, and the second is the one that
 * matters:
 *
 *   1. It is exactly on subject at no cost, and it is a few KB instead of 4MB.
 *   2. It cannot jitter. The montage this replaced was built with ffmpeg's
 *      `zoompan`, which recomputes an INTEGER pixel offset every frame — so a
 *      slow move sits on the same pixel for several frames and then jumps two.
 *      That is visible as stutter and no amount of bitrate fixes it, because
 *      the stepping is in the geometry, not the encoding. Everything here is
 *      float, sampled from a continuous function, so there is nothing to step.
 *
 * SMOOTHNESS, CONCRETELY. Three rules, all three load-bearing:
 *   - Position comes from a continuous function of ELAPSED TIME, never a frame
 *     counter. A dropped frame then shows as one slightly larger step rather
 *     than the animation falling behind and catching up.
 *   - The curve is one analytic expression — a trend plus three sines — sampled
 *     fresh each frame. Nothing is stored, tweened between keyframes, or
 *     snapped to a grid.
 *   - The scroll is unbounded and the wobble is sines at incommensurable
 *     frequencies, so there is no loop point to see and no seam to cross.
 */

import { useEffect, useRef } from "react";

/** Left-to-right climb across the visible frame; `u` is 0 at the left edge and
 *  1 at the right. Slightly super-linear, so the gain accelerates the way
 *  compounding traffic does instead of tracking a straight ruler. */
const trend = (u: number) => Math.pow(u, 1.22) * 0.82;

/** The detail that scrolls through the climb. Three sines at incommensurable
 *  frequencies: the sum never repeats, so the motion has no visible period. */
const wobble = (x: number) =>
  Math.sin(x * 0.9) * 0.052 +
  Math.sin(x * 2.3 + 1.7) * 0.026 +
  Math.sin(x * 5.1 + 0.4) * 0.011;

/** Series height at column `u`, in fractions of canvas height. */
const curve = (u: number, t: number, lift: number, amp: number) =>
  trend(u) * lift + wobble(u * 6 + t) * amp;

const RED = "212, 42, 38";
const YELLOW = "255, 224, 0";

/** Scroll rate, in curve-space radians per second. Slow on purpose — this sits
 *  behind a headline and must never pull the eye off it. */
const SPEED = 0.45;

export default function HeroMedia() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let raf = 0;
    let w = 0;
    let h = 0;
    let start = 0;

    const resize = () => {
      // Full DPR here, unlike a blur-heavy background: this draws hairlines and
      // a 1.75px stroke, which are exactly what looks cheap when resampled. It
      // is a handful of paths per frame, so it is affordable.
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      w = Math.max(1, rect.width);
      h = Math.max(1, rect.height);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    /** One series: a filled area under a lit edge. */
    const series = (
      t: number,
      lift: number,
      amp: number,
      rgb: string,
      fillTop: number,
      strokeAlpha: number,
      lineWidth: number,
      glow: number,
    ) => {
      const baseline = h * 0.97;
      // 2px sampling — finer than this is sub-pixel and only costs frames.
      const step = 2;

      ctx.beginPath();
      ctx.moveTo(0, baseline);
      for (let px = 0; px <= w; px += step) {
        ctx.lineTo(px, baseline - curve(px / w, t, lift, amp) * h);
      }
      ctx.lineTo(w, baseline);
      ctx.closePath();

      const g = ctx.createLinearGradient(0, h * 0.2, 0, baseline);
      g.addColorStop(0, `rgba(${rgb}, ${fillTop})`);
      g.addColorStop(1, `rgba(${rgb}, 0)`);
      ctx.fillStyle = g;
      ctx.fill();

      // The edge is stroked separately so the fill does not carry the glow.
      ctx.beginPath();
      for (let px = 0; px <= w; px += step) {
        const y = baseline - curve(px / w, t, lift, amp) * h;
        if (px === 0) ctx.moveTo(px, y);
        else ctx.lineTo(px, y);
      }
      ctx.strokeStyle = `rgba(${rgb}, ${strokeAlpha})`;
      ctx.lineWidth = lineWidth;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.shadowColor = `rgba(${rgb}, 0.85)`;
      ctx.shadowBlur = glow;
      ctx.stroke();
      ctx.shadowBlur = 0;
    };

    const draw = (elapsed: number) => {
      const t = elapsed * SPEED;

      ctx.fillStyle = "#03080a";
      ctx.fillRect(0, 0, w, h);

      // A warm bloom low and right, where the curve peaks — it is what keeps
      // the dark half of the frame from reading as an empty rectangle.
      const bloom = ctx.createRadialGradient(
        w * 0.82, h * 0.74, 0,
        w * 0.82, h * 0.74, Math.max(w, h) * 0.62,
      );
      bloom.addColorStop(0, `rgba(${RED}, 0.42)`);
      bloom.addColorStop(0.45, `rgba(${RED}, 0.11)`);
      bloom.addColorStop(1, "rgba(212, 42, 38, 0)");
      ctx.fillStyle = bloom;
      ctx.fillRect(0, 0, w, h);

      // Horizontal rules stay put, so they read as the chart's own scale and
      // give the scrolling curve something to be measured against.
      ctx.strokeStyle = "rgba(252, 252, 254, 0.045)";
      ctx.lineWidth = 1;
      for (let i = 1; i <= 4; i++) {
        const y = Math.round(h * (i / 5)) + 0.5; // crisp hairline
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }

      // Verticals scroll with the data at float positions, so they slide rather
      // than tick. The modulo recycles them off the left at a fixed count.
      const gap = Math.max(90, w / 12);
      const drift = (t * 34) % gap;
      ctx.strokeStyle = "rgba(252, 252, 254, 0.03)";
      for (let x = -drift; x <= w; x += gap) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
      }

      // Trailing series first — dimmer, flatter, out of phase — so the lead
      // line has something visible to be pulling away from.
      series(t * 0.82 + 2.2, 0.55, 0.7, YELLOW, 0.07, 0.26, 1.1, 10);
      series(t, 1, 1, RED, 0.40, 1, 2, 22);

      // The marker riding the leading edge.
      const yEnd = h * 0.97 - curve(1, t, 1, 1) * h;
      ctx.beginPath();
      ctx.arc(w - 1, yEnd, 4, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${YELLOW}, 1)`;
      ctx.shadowColor = `rgba(${YELLOW}, 0.9)`;
      ctx.shadowBlur = 16;
      ctx.fill();
      ctx.shadowBlur = 0;
    };

    resize();

    if (reduced) {
      // A composed still: the hero keeps its depth and its subject, it simply
      // does not move for someone who asked it not to.
      draw(3.4);
      const onResizeStatic = () => {
        resize();
        draw(3.4);
      };
      window.addEventListener("resize", onResizeStatic);
      return () => window.removeEventListener("resize", onResizeStatic);
    }

    const loop = (now: number) => {
      if (!start) start = now;
      draw((now - start) / 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);

    const onResize = () => resize();
    window.addEventListener("resize", onResize);

    // A hidden tab painting this forever is a battery bug. `start` is rebased
    // on return so the curve resumes where it was instead of jumping forward.
    const onVisibility = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden) {
        start = 0;
        raf = requestAnimationFrame(loop);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <div className="lp-media" aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>
  );
}
