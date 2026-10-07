"use client";

import type { ReactNode } from "react";

export interface SidebarSection {
  id: string;
  label: string;
  hint?: string;
}

/** The Daily / Deep-analysis toggle above the shell. Generic like `Shell`:
 *  it renders whatever workspaces it is handed and knows nothing about what
 *  they contain. A tablist, because that is what it is — two mutually
 *  exclusive views of the same brand. */
export function WorkspaceSwitch({
  workspaces, activeId, onSelect,
}: {
  workspaces: { id: string; label: string }[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="seo-wswitch" role="tablist" aria-label="Console workspace">
      {workspaces.map((w) => (
        <button
          key={w.id}
          type="button"
          role="tab"
          aria-selected={w.id === activeId}
          className={`seo-wswitch__tab${w.id === activeId ? " seo-wswitch__tab--active" : ""}`}
          onClick={() => { if (w.id !== activeId) onSelect(w.id); }}
        >
          {w.label}
        </button>
      ))}
    </div>
  );
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
