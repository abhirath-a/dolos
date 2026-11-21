import { expect, it } from "vitest";
import { computeNetworkLayout } from "./networkLayout";

it("keeps separate routes for parallel, reverse, and self transfers in a dense layout", () => {
  const edges = [
    { id: "first", source: "a", target: "b" },
    { id: "second", source: "a", target: "b" },
    { id: "reverse", source: "b", target: "a" },
    { id: "self", source: "a", target: "a" },
  ];
  const layout = computeNetworkLayout({
    ids: ["a", "b", "isolated"],
    edges,
    dense: true,
  });
  expect(layout.positions.size).toBe(3);
  expect(layout.routes.size).toBe(edges.length);
  expect(layout.routes.get("first")).not.toEqual(layout.routes.get("second"));
  for (const route of layout.routes.values()) {
    expect(route.length).toBeGreaterThan(1);
    expect(
      route.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y)),
    ).toBe(true);
  }
});
