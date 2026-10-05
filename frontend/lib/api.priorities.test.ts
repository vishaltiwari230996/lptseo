import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

describe("seoPriorities", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              priorities: {
                brand_id: "b1",
                at: "2026-09-21",
                items: [],
                notes: [],
              },
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls the priorities endpoint and returns the typed doc", async () => {
    const { seoPriorities } = await import("./api");
    const result = await seoPriorities("b1");
    expect(result.priorities?.brand_id).toBe("b1");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/seo-geo/priorities/b1"),
      expect.anything(),
    );
  });
});
