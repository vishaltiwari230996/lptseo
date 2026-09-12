import type { CSSProperties } from "react";

/**
 * Flat "app-tile" glyphs — a clean iOS / SF-Symbols-style look (Solar icons,
 * CC BY 4.0 — see public/glyph/LICENSE.txt). A white glyph sits on a per-category
 * gradient squircle tile (the `.glyphtile` styles live in kit-ui.css).
 * Used for the large identity spots only; small UI controls keep the line icons.
 */

/** Agent category → glyph file (in /public/glyph). File names match categories. */
export const CATEGORY_GLYPH: Record<string, string> = {
  design: "design",
  seo: "seo",
  copy: "copy",
  social: "social",
  ads: "ads",
  data: "data",
};

export function Glyph({
  name,
  size = 24,
  className,
  style,
}: {
  name: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`/glyph/${name}.svg`}
      alt=""
      width={size}
      height={size}
      draggable={false}
      loading="lazy"
      className={className}
      style={{ width: size, height: size, display: "block", ...style }}
    />
  );
}

/** A premium gradient "app icon" tile with a white glyph centered on it. */
export function GlyphTile({
  glyph,
  tint = "design",
  size = 52,
  glyphSize,
  className = "",
  style,
}: {
  glyph: string;
  tint?: string;
  size?: number;
  glyphSize?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`glyphtile ${className}`.trim()}
      data-cat={tint}
      style={{ width: size, height: size, ...style }}
    >
      <Glyph name={glyph} size={glyphSize ?? Math.round(size * 0.5)} />
    </span>
  );
}
