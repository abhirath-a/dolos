import { describe, it, expect, vi, afterEach } from "vitest";
import { partitionFiles, uploadGroup, graphForBatches } from "./api";
const file = (name: string, size: number) =>
  ({
    name,
    size,
    lastModified: 1,
    arrayBuffer: async () => new Uint8Array(10).buffer,
  }) as File;
afterEach(() => vi.unstubAllGlobals());
describe("batch selection", () => {
  it("partitions a large selection by both byte and count limits", () => {
    expect(
      partitionFiles(
        Array.from({ length: 205 }, (_, i) => file(`${i}.pdf`, 1)),
      ).map((b) => b.length),
    ).toEqual([100, 100, 5]);
    expect(
      partitionFiles(
        Array.from({ length: 9 }, (_, i) => file(`${i}.PDF`, 25 * 1024 * 1024)),
      ).map((b) => b.length),
    ).toEqual([4, 4, 1]);
  });
  it("rejects invalid files before creating any jobs", () => {
    expect(() => partitionFiles([file("empty.pdf", 0)])).toThrow("1 byte");
    expect(() => partitionFiles([file("big.pdf", 26 * 1024 * 1024)])).toThrow(
      "25 MiB",
    );
    expect(() => partitionFiles([file("bad.exe", 1)])).toThrow("PDF, CSV");
  });
  it("uploads only to returned R2 URLs before completing the batch", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            uploads: [
              {
                url: "https://bucket.test/signed",
                headers: { "Content-Type": "application/pdf" },
              },
            ],
            complete_url: "/batches/b/complete",
          }),
        ),
      )
      .mockResolvedValueOnce(new Response(""))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ id: "b", status: "queued", documents: [] }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    const result = await uploadGroup([file("x.pdf", 10)], () => {});
    expect(result.id).toBe("b");
    expect(fetch.mock.calls[1][0]).toBe("https://bucket.test/signed");
    expect(fetch.mock.calls[1][1].headers).toEqual({
      "Content-Type": "application/pdf",
    });
    expect(fetch.mock.calls[2][0]).toBe("/api/batches/b/complete");
  });
  it("paginates and deduplicates shared documents across batches", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            nodes: [{ id: "a" }],
            edges: [{ id: "1" }],
            has_more: true,
            next_offset: 1000,
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            nodes: [{ id: "b" }],
            edges: [{ id: "2" }],
            has_more: false,
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            nodes: [{ id: "a" }],
            edges: [{ id: "1" }],
            has_more: false,
          }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    const graph = await graphForBatches(["one", "two"]);
    expect(graph.nodes).toHaveLength(2);
    expect(graph.edges).toHaveLength(2);
    expect(fetch.mock.calls[1][0]).toContain("offset=1000");
  });
  it("does not silently fall back when R2 is misconfigured", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Missing bucket" }), {
        status: 503,
      }),
    );
    vi.stubGlobal("fetch", fetch);
    await expect(uploadGroup([file("x.pdf", 10)], () => {})).rejects.toThrow(
      "Missing bucket",
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
