"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { Icon } from "@/lib/icons";
import { GlyphTile, CATEGORY_GLYPH } from "@/lib/glyph";

const cx = (...parts: Array<string | false | null | undefined>) =>
  parts.filter((p): p is string => Boolean(p)).join(" ");

/** The Law Prep Tutorial mark — scales of justice, red on the brand yellow.
 *
 *  Why a symbol and not the wordmark: lawpreptutorial.com's logo is a serif
 *  wordmark, and their own square favicon stacks it onto three lines. That is
 *  legible at 100px and a red smudge at the 16-30px this mark actually renders
 *  at. Scales carry the same meaning at any size, and they are already the
 *  brand's own vocabulary — the site uses a scales-of-justice icon across its
 *  course categories.
 *
 *  Why a tile, when the mark it replaces was two bare shapes: the old mark
 *  painted its L in --text-primary so one component could serve both themes
 *  (ink on cloud, cloud on ink). That trick needs a themeable ink, and this
 *  brand's pair is fixed — red and yellow, in that relationship, on both
 *  themes. Carrying its own ground is what makes it theme-independent, and it
 *  reproduces the logo's actual figure/ground: red on yellow, exactly as the
 *  favicon does.
 *
 *  Geometry is centred by construction in the 40x40 box (7.75 clear top and
 *  bottom, 5 each side). The pans hang off the beam's underside at y=15.4 —
 *  keep them touching it; floating them reads as three unrelated marks once
 *  the whole thing is 16px wide.
 *
 *  Decorative by default; pass a `title` where the mark is the only thing
 *  naming the product (e.g. sign-in).
 */
export function BrandMark({ size = 30, title }: { size?: number; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      style={{ flex: "none" }}
    >
      <rect width="40" height="40" rx="9" fill="var(--lpt-yellow)" />
      <g fill="var(--lpt-red)">
        <circle cx="20" cy="10.2" r="2.5" />
        <rect x="6" y="13" width="28" height="2.4" rx="1.2" />
        <rect x="18.8" y="12" width="2.4" height="18.5" />
        <rect x="12.5" y="29.6" width="15" height="2.6" rx="1.3" />
        <path d="M5 15.4 H15 L10 22.6 Z" />
        <path d="M25 15.4 H35 L30 22.6 Z" />
      </g>
    </svg>
  );
}

export function Button({
  variant = "primary",
  size = "md",
  fullWidth,
  iconLeft,
  iconRight,
  as = "button",
  className = "",
  children,
  ...rest
}: {
  variant?: string;
  size?: string;
  fullWidth?: boolean;
  iconLeft?: ReactNode;
  iconRight?: ReactNode;
  as?: "button" | "a";
  className?: string;
  children?: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const Tag = as as "button";
  const cls = cx(
    "ens-btn",
    `ens-btn--${size}`,
    variant !== "primary" ? `ens-btn--${variant}` : undefined,
    fullWidth ? "ens-btn--full" : undefined,
    className,
  );
  return (
    <Tag className={cls} {...rest}>
      {iconLeft ? <span className="ens-btn__icon">{iconLeft}</span> : null}
      {children}
      {iconRight ? <span className="ens-btn__icon">{iconRight}</span> : null}
    </Tag>
  );
}

export function IconButton({
  variant = "ghost",
  size = "md",
  label,
  className = "",
  children,
  ...rest
}: {
  variant?: string;
  size?: string;
  label: string;
  className?: string;
  children?: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cx("ens-iconbtn", `ens-iconbtn--${size}`, variant !== "ghost" ? `ens-iconbtn--${variant}` : undefined, className)}
      aria-label={label}
      title={label}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Input({
  icon,
  className = "",
  ...rest
}: { icon?: ReactNode; className?: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="ens-input-wrap">
      {icon ? <span className="ens-input__icon">{icon}</span> : null}
      <input className={cx("ens-input", icon ? "ens-input--has-icon" : undefined, className)} {...rest} />
    </div>
  );
}

export function Badge({
  variant = "neutral",
  dot,
  icon,
  className = "",
  children,
  ...rest
}: {
  variant?: string;
  dot?: boolean;
  icon?: ReactNode;
  className?: string;
  children?: ReactNode;
} & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cx("ens-badge", `ens-badge--${variant}`, className)} {...rest}>
      {dot ? <span className="ens-badge__dot" /> : null}
      {icon}
      {children}
    </span>
  );
}

export function Tag({
  category,
  glyph,
  className = "",
  children,
  ...rest
}: {
  category?: string;
  glyph?: ReactNode;
  className?: string;
  children?: ReactNode;
} & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cx("ens-tag", category ? `ens-tag--${category}` : undefined, className)} {...rest}>
      {glyph ? <span className="ens-tag__glyph">{glyph}</span> : null}
      {children}
    </span>
  );
}

const SLABEL: Record<string, string> = {
  idle: "Idle",
  running: "Running",
  success: "Done",
  error: "Failed",
  paused: "Paused",
};

export function StatusDot({
  status = "idle",
  label,
  showLabel = true,
  className = "",
  ...rest
}: {
  status?: string;
  label?: string;
  showLabel?: boolean;
  className?: string;
} & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cx("ens-status", `ens-status--${status}`, className)} {...rest}>
      <span className="ens-status__dot" />
      {showLabel ? <span>{label || SLABEL[status]}</span> : null}
    </span>
  );
}

function initials(name = ""): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
}

export function Avatar({
  name,
  src,
  size = "md",
  square,
  color,
  style,
  className = "",
  children,
  ...rest
}: {
  name?: string;
  src?: string;
  size?: string;
  square?: boolean;
  color?: string;
  style?: React.CSSProperties;
  className?: string;
  children?: ReactNode;
} & React.HTMLAttributes<HTMLSpanElement>) {
  const bg = color ? { background: color, color: "#fff", borderColor: "transparent" } : null;
  return (
    <span
      className={cx("ens-avatar", `ens-avatar--${size}`, square && "ens-avatar--square", className)}
      style={{ ...bg, ...style }}
      title={name}
      {...rest}
    >
      {src ? <img src={src} alt={name || ""} /> : children || initials(name)}
    </span>
  );
}

export function AvatarGroup({
  className = "",
  children,
  ...rest
}: { className?: string; children?: ReactNode } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cx("ens-avatar-group", className)} {...rest}>
      {children}
    </span>
  );
}

export function Tabs({
  items = [],
  value,
  onChange,
  variant = "pill",
  className = "",
  ...rest
}: {
  items?: Array<string | { value: string; label: string; icon?: ReactNode; count?: number }>;
  value?: string;
  onChange?: (v: string) => void;
  variant?: string;
  className?: string;
}) {
  return (
    <div role="tablist" className={cx("ens-tabs", `ens-tabs--${variant}`, className)} {...rest}>
      {items.map((it) => {
        const item = typeof it === "string" ? { value: it, label: it } : it;
        const sel = item.value === value;
        return (
          <button
            key={item.value}
            role="tab"
            type="button"
            aria-selected={sel}
            className="ens-tab"
            onClick={() => onChange?.(item.value)}
          >
            {item.icon}
            {item.label}
            {item.count != null ? <span className="ens-tab__count">{item.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

const CATL: Record<string, string> = {
  design: "Design",
  seo: "SEO",
  copy: "Copy",
  social: "Social",
  ads: "Ads",
  data: "Data",
};

export function AgentCard({
  name,
  role,
  description,
  category = "design",
  glyph,
  portrait,
  status,
  added,
  onAdd,
  onOpen,
  comingSoon,
  interactive,
  className = "",
  ...rest
}: {
  name: string;
  role: string;
  description?: string;
  category?: string;
  glyph?: ReactNode;
  /** Character art shown as a full-bleed portrait; switches the card to the portrait layout. */
  portrait?: string;
  status?: string;
  added?: boolean;
  onAdd?: () => void;
  onOpen?: () => void;
  comingSoon?: boolean;
  interactive?: boolean;
  className?: string;
}) {
  if (portrait) {
    return (
      <div
        className={cx("ens-agentcard ens-agentcard--portrait", interactive && !comingSoon ? "ens-agentcard--interactive" : undefined, className)}
        data-category={category}
        data-soon={comingSoon ? "1" : undefined}
        onClick={!comingSoon && onOpen ? onOpen : undefined}
        {...rest}
      >
        <div className="ens-agentcard__art">
          <img src={portrait} alt={`${name} agent character`} loading="lazy" />
          <span className="ens-agentcard__glare" aria-hidden="true" />
          {comingSoon ? <span className="ens-agentcard__soonchip">Coming soon</span> : null}
        </div>
        <div className="ens-agentcard__bar">
          <div className="ens-agentcard__name">{name}</div>
          {comingSoon ? null : onOpen ? (
            <Button size="sm" variant="brand" onClick={onOpen} iconRight={<Icon name="arrow-right" size={15} />}>
              Open
            </Button>
          ) : onAdd ? (
            <Button size="sm" variant={added ? "secondary" : "primary"} onClick={onAdd}>
              {added ? "Added" : "Add to team"}
            </Button>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <div
      className={cx("ens-agentcard", interactive && !comingSoon ? "ens-agentcard--interactive" : undefined, className)}
      data-category={category}
      style={comingSoon ? { opacity: 0.72 } : undefined}
      {...rest}
    >
      <div className="ens-agentcard__top">
        {CATEGORY_GLYPH[category] ? (
          <GlyphTile glyph={CATEGORY_GLYPH[category]} tint={category} size={52} glyphSize={26} className="ens-agentcard__tile" />
        ) : (
          <span className="ens-agentcard__glyph">{glyph}</span>
        )}
        <div className="ens-agentcard__id">
          <div className="ens-agentcard__name">{name}</div>
          <div className="ens-agentcard__role">{role}</div>
        </div>
        {comingSoon ? <Badge variant="outline">Soon</Badge> : null}
      </div>
      {description ? <p className="ens-agentcard__desc">{description}</p> : null}
      <div className="ens-agentcard__foot">
        {comingSoon ? (
          <span className="ens-agentcard__meta">Not available yet</span>
        ) : status ? (
          <StatusDot status={status} />
        ) : (
          <span className="ens-agentcard__meta">Specialist agent</span>
        )}
        {comingSoon ? null : onOpen ? (
          <Button size="sm" variant="brand" onClick={onOpen} iconRight={<Icon name="arrow-right" size={15} />}>
            Open
          </Button>
        ) : onAdd ? (
          <Button size="sm" variant={added ? "secondary" : "primary"} onClick={onAdd}>
            {added ? "Added" : "Add to team"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

const MCOL: Record<string, string> = {
  design: "var(--cat-design)",
  seo: "var(--cat-seo)",
  copy: "var(--cat-copy)",
  social: "var(--cat-social)",
  ads: "var(--cat-ads)",
  data: "var(--cat-data)",
};

export function TeamCard({
  name,
  glyph,
  tint,
  description,
  members = [],
  status = "idle",
  lastRun,
  deployed,
  onDeploy,
  comingSoon,
  interactive,
  className = "",
  ...rest
}: {
  name: string;
  glyph?: string;
  tint?: string;
  description?: string;
  members?: Array<{ name: string; category: string }>;
  status?: string;
  lastRun?: string;
  deployed?: boolean;
  onDeploy?: () => void;
  comingSoon?: boolean;
  interactive?: boolean;
  className?: string;
}) {
  const shown = members.slice(0, 4);
  const extra = members.length - shown.length;
  return (
    <div
      className={cx("ens-teamcard", interactive && !comingSoon ? "ens-teamcard--interactive" : undefined, className)}
      style={comingSoon ? { opacity: 0.72 } : undefined}
      {...rest}
    >
      <div className="ens-teamcard__body">
        <div className="ens-teamcard__head">
          <GlyphTile glyph={glyph || "teams"} tint={tint || "design"} size={48} glyphSize={24} className="ens-teamcard__tile" />
          <div className="ens-teamcard__id">
            <div className="ens-teamcard__name">{name}</div>
            <div className="ens-teamcard__sub">
              {members.length} agents{lastRun && lastRun !== "—" ? ` · last run ${lastRun}` : ""}
            </div>
          </div>
          {comingSoon ? <Badge variant="outline">Soon</Badge> : <StatusDot status={status} />}
        </div>
        {description ? <p className="ens-teamcard__desc">{description}</p> : null}
        <div className="ens-teamcard__members">
          <AvatarGroup>
            {shown.map((m, i) => (
              <Avatar key={i} name={m.name} size="sm" color={MCOL[m.category] || "var(--gray-500)"} />
            ))}
            {extra > 0 ? <Avatar size="sm">+{extra}</Avatar> : null}
          </AvatarGroup>
          <span className="ens-teamcard__count">
            {shown.map((m) => m.name).join(" · ")}
            {extra > 0 ? ` +${extra}` : ""}
          </span>
        </div>
        <div className="ens-teamcard__foot">
          <span className="ens-teamcard__count" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
            {comingSoon ? "Not available yet" : deployed ? "Deployed" : "Ready to deploy"}
          </span>
          {!comingSoon && onDeploy ? (
            <Button size="sm" variant={deployed ? "secondary" : "brand"} onClick={onDeploy}>
              {deployed ? "Open team" : "Deploy team"}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export { Icon };
