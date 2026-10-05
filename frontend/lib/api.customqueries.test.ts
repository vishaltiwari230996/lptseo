import { describe, expect, it, vi, afterEach } from "vitest";

describe("custom rank-tracking queries", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("adds a custom query", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ custom_queries: ["clat coaching jaipur"] }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
    const { seoAddCustomQuery } = await import("./api");
    const result = await seoAddCustomQuery("b1", "clat coaching jaipur");
    expect(result.custom_queries).toContain("clat coaching jaipur");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/seo-geo/competitors/b1/custom-queries"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("removes a custom query", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ custom_queries: [] }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
    const { seoRemoveCustomQuery } = await import("./api");
    const result = await seoRemoveCustomQuery("b1", "clat coaching jaipur");
    expect(result.custom_queries).toEqual([]);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/seo-geo/competitors/b1/custom-queries"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("rejects when adding a custom query fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "You can track at most 50 custom queries" }),
      { status: 400, headers: { "content-type": "application/json" } },
    )));
    const { seoAddCustomQuery } = await import("./api");
    await expect(seoAddCustomQuery("b1", "clat coaching jaipur")).rejects.toThrow(
      "You can track at most 50 custom queries",
    );
  });

  it("rejects when removing a custom query fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "Brand not found" }),
      { status: 404, headers: { "content-type": "application/json" } },
    )));
    const { seoRemoveCustomQuery } = await import("./api");
    await expect(seoRemoveCustomQuery("b1", "clat coaching jaipur")).rejects.toThrow(
      "Brand not found",
    );
  });
});
