import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { filterEdges, pageRange, demoAnalysis, withContext } from "./domain";
import type { ModelDemo, Graph } from "./types";
const demo = JSON.parse(
  readFileSync(new URL("../public/model-demo.json", import.meta.url), "utf8"),
) as ModelDemo;
describe("investigation scope and evidence", () => {
  it("keeps every ledger row reachable beyond 100 relationships", () => {
    const items = Array.from({ length: 205 }, (_, i) => i);
    const visited = [];
    for (let page = 0; page <= 20; page++)
      visited.push(...pageRange(items, page).items);
    expect(visited).toEqual(items);
    expect(pageRange(items, 999).page).toBe(20);
  });
  it("derives demo flags from actual recorded margins, never invented scores", () => {
    const result = demoAnalysis(demo, demo.models.graphsage);
    expect(result.findings.map((f) => f.edge_id)).toEqual(
      demo.models.graphsage
        .scores!.filter((s) => s.score > 0)
        .map((s) => s.transaction_id),
    );
    expect(result.model.run_id).toBe(demo.run_id);
  });
  it("adds outside-view sampled context with source provenance", () => {
    const row =
      demo.models.graphsage.scores![0].explanation!.context_transactions![0];
    const graph = withContext({ nodes: [], edges: [] }, [row]);
    expect(graph.edges[0].id).toBe(row.id);
    expect(graph.edges[0].source_row).toBe(row.source_row);
    expect(graph.nodes.map((n) => n.id)).toContain(row.sender_id);
  });
  it("does not replace an existing graph edge when adding context", () => {
    const row =
      demo.models.graphsage.scores![0].explanation!.context_transactions![0];
    const original = {
      id: row.id,
      source: row.sender_id,
      target: row.receiver_id,
      relation: "transfer",
      description: "Original evidence",
    };
    expect(
      withContext({ nodes: [], edges: [original] }, [row]).edges[0].description,
    ).toBe("Original evidence");
  });
  it("filters by names, flags, and entity together", () => {
    const graph: Graph = {
      nodes: [
        { id: "a", label: "Origin" },
        { id: "b", label: "Destination" },
      ],
      edges: [{ id: "x", source: "a", target: "b", relation: "transfer" }],
    };
    expect(
      filterEdges(graph, "destination", new Set(["x"]), true, "a"),
    ).toHaveLength(1);
    expect(filterEdges(graph, "missing", new Set(), false, null)).toHaveLength(
      0,
    );
  });
});

describe("reviewer regression checks", () => {
  it("preserves date-only precision and historical qualifiers", async () => {
    const { dateLabel } = await import("./domain");
    expect(dateLabel("2009-09-30", null, "on or about")).toBe(
      "on or about Sep 30, 2009",
    );
    expect(dateLabel("2009-09-30")).not.toContain("12:00");
    expect(dateLabel("2025-01-06T01:00:00Z")).toContain("2025");
  });
  it("exports only rendered relationships, including filtered model context", async () => {
    const { visibleExport } = await import("./domain");
    const analysis = demoAnalysis(demo, demo.models.graphsage);
    const exported = visibleExport(
      demo.graph,
      analysis,
      new Set([demo.focus_edge_id]),
      true,
      new Set(),
      demo.focus_edge_id,
      false,
    );
    expect(exported.graph.edges.map((e) => e.id)).toEqual([demo.focus_edge_id]);
    expect(
      exported.analysis?.model?.scores?.[0].explanation?.context_transactions,
    ).toHaveLength(0);
    expect(
      exported.graph.nodes.every((n) =>
        exported.graph.edges.some(
          (e) => e.source === n.id || e.target === n.id,
        ),
      ),
    ).toBe(true);
  });
  it("keeps sampled neighbors in model view even if a flagged-only state is supplied", async () => {
    const { graphEdgesForView } = await import("./domain");
    const context = new Set(
      demo.models.graphsage.scores![0].explanation!.context_transaction_ids,
    );
    const edges = graphEdgesForView(
      demo.graph,
      new Set([demo.focus_edge_id]),
      true,
      context,
      demo.focus_edge_id,
      true,
    );
    expect(edges.length).toBe(context.size + 1);
    expect(edges.some((e) => context.has(e.id))).toBe(true);
  });
});
