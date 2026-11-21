import dagre from "@dagrejs/dagre";
import type { Edge } from "./types";

export type Point = { x: number; y: number };
export type LayoutEdge = Pick<Edge, "id" | "source" | "target">;
export type LayoutInput = {
  ids: string[];
  edges: LayoutEdge[];
  dense: boolean;
};
export type Layout = {
  positions: Map<string, Point>;
  routes: Map<string, Point[]>;
};

export function computeNetworkLayout({
  ids,
  edges,
  dense,
}: LayoutInput): Layout {
  const graph = new dagre.graphlib.Graph({ multigraph: true });
  graph.setGraph({
    rankdir: "LR",
    ranker: dense ? "tight-tree" : "network-simplex",
    nodesep: 90,
    ranksep: 165,
    edgesep: 30,
    marginx: 70,
    marginy: 80,
  });
  for (const id of ids) graph.setNode(id, { width: 150, height: 96 });
  const pairs = new Map<string, Map<string, LayoutEdge[]>>();
  for (const edge of edges) {
    let targets = pairs.get(edge.source);
    if (!targets) pairs.set(edge.source, (targets = new Map()));
    let group = targets.get(edge.target);
    if (!group) targets.set(edge.target, (group = []));
    group.push(edge);
    if (!dense || group.length === 1)
      graph.setEdge(
        edge.source,
        edge.target,
        { width: 100, height: 24, labelpos: "c" },
        edge.id,
      );
  }
  dagre.layout(graph);
  const routes = new Map<string, Point[]>();
  for (const targets of pairs.values()) {
    for (const group of targets.values()) {
      group.forEach((edge, index) => {
        const route = graph.edge({
          v: edge.source,
          w: edge.target,
          name: dense ? group[0].id : edge.id,
        }).points as Point[];
        const lane =
          dense && group.length > 1
            ? (index / (group.length - 1) - 0.5) *
              Math.min(72, (group.length - 1) * 10)
            : 0;
        routes.set(
          edge.id,
          lane === 0
            ? route
            : route.map((point, i) =>
                i === 0 || i === route.length - 1
                  ? point
                  : { x: point.x, y: point.y + lane },
              ),
        );
      });
    }
  }
  return { positions: new Map(ids.map((id) => [id, graph.node(id)])), routes };
}
