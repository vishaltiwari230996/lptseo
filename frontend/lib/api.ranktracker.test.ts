import { afterEach, describe, expect, it, vi } from "vitest";

/** Typed exactly like the real `fetch` (see api.timeout.test.ts) so
 *  `fetchMock.mock.calls[0]` indexes as `[url, init]` instead of collapsing
 *  to an empty tuple, which a zero-arg mock implementation would do. */
function stub(body: unknown, status = 200) {
  const fetchMock = vi.fn((_url: string, _init?: RequestInit) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("rank tracker client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads the panel payload from the brand-scoped path", async () => {
    const fetchMock = stub({
      rows: [],
      worklist: [],
      pool: { size: 0, cap: 200, built_at: null, sources_used: [], notes: [] },
      budget: { searches: 0, cap: 3000, remaining: 3000, date: "2026-10-05" },
      meta: null,
      job: null,
      competitors: [],
      enabled: true,
    });

    const { seoRankTracker } = await import("./api");
    const doc = await seoRankTracker("b1");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/seo-geo/rank-tracker/b1");
    expect(doc.pool.cap).toBe(200);
  });

  it("url-encodes the query when reading history, and the daily triple carries `at`", async () => {
    const fetchMock = stub({
      history: {
        query: "clat & cuet",
        raw: [],
        daily: [["2026-10-01", { best: 4, worst: 9, last: 4, at: 123456 }]],
        rivals: {},
      },
    });

    const { seoRankHistory } = await import("./api");
    const result = await seoRankHistory("b1", "clat & cuet");

    expect(fetchMock.mock.calls[0][0]).toContain("query=clat%20%26%20cuet");
    expect(result.history.daily[0][1].at).toBe(123456);
  });

  it("allows a null daily triple for a day we ranked nowhere", async () => {
    stub({
      history: {
        query: "q",
        raw: [],
        daily: [["2026-10-02", { best: null, worst: null, last: null, at: 123480 }]],
        rivals: {},
      },
    });

    const { seoRankHistory } = await import("./api");
    const result = await seoRankHistory("b1", "q");

    const [, triple] = result.history.daily[0];
    expect(triple.best).toBeNull();
    expect(triple.worst).toBeNull();
    expect(triple.last).toBeNull();
  });

  it("posts the query in the body to the brand-scoped gap endpoint", async () => {
    const fetchMock = stub({ gap: { query: "q", narrative: "n" } });

    const { seoRankGap } = await import("./api");
    await seoRankGap("b1", "q");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/seo-geo/rank-tracker/b1/gap");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    const init = fetchMock.mock.calls[0][1];
    expect(JSON.parse(init?.body as string)).toEqual({ query: "q" });
  });

  it("starts a sweep job via POST", async () => {
    const job = {
      kind: "rank_tracker", brand_id: "b1", status: "running", phase: "sweeping",
      done: 0, total: 200, started_at: "2026-10-05T09:00:00+00:00", finished_at: null,
      error: null, log: [], alive: true,
    };
    const fetchMock = stub({ job });

    const { seoRankSweep } = await import("./api");
    const result = await seoRankSweep("b1");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/seo-geo/rank-tracker/b1/sweep");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(result.job?.status).toBe("running");
  });

  it("rebuilds the pool via POST and returns the full panel doc", async () => {
    const fetchMock = stub({
      rows: [],
      worklist: [],
      pool: { size: 12, cap: 200, built_at: "2026-10-05T09:00:00+00:00", sources_used: ["gsc"], notes: [] },
      budget: { searches: 1, cap: 3000, remaining: 2999, date: "2026-10-05" },
      meta: { at: "2026-10-05T09:00:00+00:00", ranked: 1, errors: 0, rivals: ["rival.com"], count: 1 },
      job: null,
      competitors: ["rival.com"],
      enabled: true,
    });

    const { seoRankPoolRebuild } = await import("./api");
    const doc = await seoRankPoolRebuild("b1");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/seo-geo/rank-tracker/b1/pool/rebuild");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(doc.pool.size).toBe(12);
    expect(doc.meta?.count).toBe(1);
  });
});
