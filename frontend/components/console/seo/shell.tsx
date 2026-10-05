"use client";

import type { ReactNode } from "react";

export interface SidebarSection {
  id: string;
  label: string;
  hint?: string;
}

export function Shell({
  sections, activeId, onSelect, children,
}: {
  sections: SidebarSection[];
  activeId: string;
  onSelect: (id: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="seo-shell">
      <nav className="seo-sidebar" aria-label="SEO console sections">
        {sections.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`seo-sidebar__item${s.id === activeId ? " seo-sidebar__item--active" : ""}`}
            onClick={() => onSelect(s.id)}
            aria-current={s.id === activeId ? "page" : undefined}
          >
            <span className="seo-sidebar__label">{s.label}</span>
            {s.hint && <span className="seo-sidebar__hint">{s.hint}</span>}
          </button>
        ))}
      </nav>
      <div className="seo-shell__content">{children}</div>
    </div>
  );
}
