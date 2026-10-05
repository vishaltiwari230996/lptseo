// @vitest-environment jsdom
/** KeywordPoolView's two biggest keyword sources — Search Console and
 *  Keyword Lab — each require a separate manual step the user may not have
 *  taken. When the pool is thin because of that, the empty state should say
 *  so and offer the fix, rather than just showing a low number with no
 *  explanation. These tests pin that behaviour down; they intentionally do
 *  not touch the populated-table rendering below it.
 */
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { KeywordPoolView } from "./dashboard";

// This project doesn't run Vitest with `globals: true`, so
// @testing-library/react's automatic afterEach cleanup (which relies on
// detecting a global `afterEach`) never registers — without this, each
// test's render leaks into the next one's DOM.
afterEach(cleanup);

describe("KeywordPoolView empty states", () => {
  it("shows a Connect Search Console CTA when GSC is not connected and the pool is thin", () => {
    render(
      <KeywordPoolView
        brandId="b1"
        doc={{ at: "", keywords: [], totals: { keywords: 2 }, bands: {}, clusters: [], notes: [] } as any}
        gscConnected={false}
        keywordLabRun={false}
        onLoaded={vi.fn()}
        onToast={vi.fn()}
      />,
    );
    expect(screen.getByText(/connect search console/i)).toBeInTheDocument();
    expect(screen.getByText(/run keyword lab/i)).toBeInTheDocument();
  });

  it("does not show the Search Console CTA once GSC is connected", () => {
    render(
      <KeywordPoolView
        brandId="b1"
        doc={{ at: "", keywords: [], totals: { keywords: 40 }, bands: {}, clusters: [], notes: [] } as any}
        gscConnected={true}
        keywordLabRun={false}
        onLoaded={vi.fn()}
        onToast={vi.fn()}
      />,
    );
    expect(screen.queryByText(/connect search console/i)).not.toBeInTheDocument();
    expect(screen.getByText(/run keyword lab/i)).toBeInTheDocument();
  });

  it("shows neither CTA once both GSC is connected and Keyword Lab has run", () => {
    render(
      <KeywordPoolView
        brandId="b1"
        doc={{ at: "", keywords: [], totals: { keywords: 120 }, bands: {}, clusters: [], notes: [] } as any}
        gscConnected={true}
        keywordLabRun={true}
        onLoaded={vi.fn()}
        onToast={vi.fn()}
      />,
    );
    expect(screen.queryByText(/connect search console/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/run keyword lab/i)).not.toBeInTheDocument();
  });
});
