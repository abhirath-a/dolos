import { memo, useMemo, useEffect } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  BaseEdge,
  EdgeLabelRenderer,
  useReactFlow,
  useNodesInitialized,
  type NodeProps,
  type Node,
  type EdgeProps,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import { graphEdgesForView } from "./domain";
import {
  Building2,
  UserRound,
  Landmark,
  Network as NetworkIcon,
  ArrowRight,
} from "lucide-react";
import { label, money, type Graph, type Entity } from "./types";
type EntityNode = Node<
  {
    entity: Entity;
    flagged: boolean;
    focused: boolean;
    muted: boolean;
    degree: number;
  },
  "entity"
>;
const EntityCard = memo(({ data }: NodeProps<EntityNode>) => {
  const Icon =
    data.entity.kind === "person"
      ? UserRound
      : data.entity.kind === "account"
        ? Landmark
        : Building2;
  return (
    <div
      className={`graph-entity ${data.flagged ? "flagged" : ""} ${data.focused ? "focused" : ""} ${data.muted ? "muted" : ""}`}
    >
      <Handle id="in-left" type="target" position={Position.Left} />
      <Handle id="in-right" type="target" position={Position.Right} />
      <div className="entity-disc">
        <Icon size={22} strokeWidth={1.5} />
        {data.flagged && (
          <span
            className="entity-review-dot"
            aria-label="Connected to a flagged edge"
          />
        )}
      </div>
      <strong>{label(data.entity)}</strong>
      <span>
        {data.entity.kind === "person"
          ? "Person"
          : data.entity.kind === "account"
            ? "Account"
            : data.entity.kind?.replaceAll("_", " ") || "Entity"}
      </span>
      <Handle id="out-right" type="source" position={Position.Right} />
      <Handle id="out-left" type="source" position={Position.Left} />
    </div>
  );
});
type Point = { x: number; y: number };
// Preserve Dagre's obstacle-aware routing, rounding only the corners. The old
// midpoint quadratic discarded the layout and cut straight across other nodes.
function roundedRoute(points: Point[]) {
  let path = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const previous = points[i - 1],
      current = points[i],
      next = points[i + 1];
    const before = Math.hypot(current.x - previous.x, current.y - previous.y);
    const after = Math.hypot(next.x - current.x, next.y - current.y);
    const radius = Math.min(18, before / 2, after / 2);
    const start = {
      x: current.x + ((previous.x - current.x) * radius) / (before || 1),
      y: current.y + ((previous.y - current.y) * radius) / (before || 1),
    };
    const end = {
      x: current.x + ((next.x - current.x) * radius) / (after || 1),
      y: current.y + ((next.y - current.y) * radius) / (after || 1),
    };
    path += ` L${start.x},${start.y} Q${current.x},${current.y} ${end.x},${end.y}`;
  }
  const last = points[points.length - 1];
  return path + ` L${last.x},${last.y}`;
}
function routeMidpoint(points: Point[]) {
  const lengths = points
    .slice(1)
    .map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
  let remaining = lengths.reduce((sum, length) => sum + length, 0) / 2;
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i]) {
      const fraction = remaining / (lengths[i] || 1);
      return {
        x: points[i].x + (points[i + 1].x - points[i].x) * fraction,
        y: points[i].y + (points[i + 1].y - points[i].y) * fraction,
      };
    }
    remaining -= lengths[i];
  }
  return points[0];
}
function TransferEdge(props: EdgeProps) {
  const routed = props.data?.points as Point[] | undefined;
  const points = [
    { x: props.sourceX, y: props.sourceY },
    ...(routed?.slice(1, -1) || []),
    { x: props.targetX, y: props.targetY },
  ];
  const midpoint = routeMidpoint(points);
  return (
    <>
      <BaseEdge {...props} path={roundedRoute(points)} interactionWidth={28} />
      {props.data?.showLabel && (
        <EdgeLabelRenderer>
          <div
            className={`transfer-label ${props.data?.flagged ? "flagged" : ""}`}
            style={{
              opacity: props.style?.opacity,
              transform: `translate(-50%,-50%) translate(${midpoint.x}px,${midpoint.y}px)`,
            }}
          >
            {props.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
const nodeTypes = { entity: EntityCard },
  edgeTypes = { transfer: TransferEdge };
export default function Network({
  graph,
  flags,
  selected,
  focus,
  onSelect,
  onNode,
  search,
  onlyFlags,
  context,
  pinned,
  contextOnly,
}: {
  graph: Graph;
  flags: Set<string>;
  selected: string | null;
  focus: string | null;
  onSelect: (id: string) => void;
  onNode: (id: string) => void;
  search: string;
  onlyFlags: boolean;
  context: Set<string>;
  pinned?: string | null;
  contextOnly?: boolean;
}) {
  const visible = useMemo(
    () =>
      graphEdgesForView(graph, flags, onlyFlags, context, pinned, contextOnly),
    [graph, onlyFlags, flags, contextOnly, context, pinned],
  );
  const arranged = useMemo(() => {
    const connected = new Set(visible.flatMap((e) => [e.source, e.target]));
    const ns = graph.nodes.filter(
      (n) => connected.has(n.id) || (!onlyFlags && !contextOnly),
    );
    const layout = new dagre.graphlib.Graph({ multigraph: true });
    layout.setGraph({
      rankdir: "LR",
      nodesep: 90,
      ranksep: 165,
      edgesep: 30,
      marginx: 70,
      marginy: 80,
    });
    layout.setDefaultEdgeLabel(() => ({}));
    ns.forEach((n) => layout.setNode(n.id, { width: 150, height: 96 }));
    // Lay out every transfer: multigraph routes separate parallel and reverse
    // transfers rather than drawing all of them on the same invented curve.
    visible.forEach((e) =>
      layout.setEdge(
        e.source,
        e.target,
        { width: 100, height: 24, labelpos: "c" },
        e.id,
      ),
    );
    dagre.layout(layout);
    return {
      ns,
      positions: new Map(ns.map((n) => [n.id, layout.node(n.id)])),
      routes: new Map(
        visible.map((e) => [
          e.id,
          layout.edge({ v: e.source, w: e.target, name: e.id })
            .points as Point[],
        ]),
      ),
    };
  }, [graph, visible, onlyFlags, contextOnly]);
  const { nodes, edges } = useMemo(() => {
    const flaggedNodes = new Set(
      visible
        .filter((e) => flags.has(e.id))
        .flatMap((e) => [e.source, e.target]),
    );
    const selectedEdge = visible.find((e) => e.id === selected);
    const highlightedNodes = focus
      ? new Set([
          focus,
          ...visible
            .filter((e) => e.source === focus || e.target === focus)
            .flatMap((e) => [e.source, e.target]),
        ])
      : selectedEdge
        ? new Set([
            selectedEdge.source,
            selectedEdge.target,
            ...visible
              .filter((e) => context.has(e.id))
              .flatMap((e) => [e.source, e.target]),
          ])
        : null;
    return {
      nodes: arranged.ns.map((n) => ({
        id: n.id,
        type: "entity",
        ariaLabel: label(n),
        position: {
          x: arranged.positions.get(n.id)!.x - 75,
          y: arranged.positions.get(n.id)!.y - 48,
        },
        data: {
          entity: n,
          flagged: flaggedNodes.has(n.id),
          focused: focus === n.id,
          muted:
            (!!search &&
              !label(n).toLowerCase().includes(search.toLowerCase())) ||
            (!!highlightedNodes && !highlightedNodes.has(n.id)),
          degree: visible.filter((e) => e.source === n.id || e.target === n.id)
            .length,
        },
      })),
      edges: visible.map((e) => {
        const route = arranged.routes.get(e.id)!;
        const sourcePosition = arranged.positions.get(e.source)!;
        const targetPosition = arranged.positions.get(e.target)!;
        const sourceSide = route[0].x < sourcePosition.x ? "left" : "right";
        const targetSide =
          route[route.length - 1].x < targetPosition.x ? "left" : "right";
        const incident = !!focus && (e.source === focus || e.target === focus);
        const dimmed = focus
          ? !incident
          : !!selected &&
            e.id !== selected &&
            e.id !== pinned &&
            !context.has(e.id);
        const isContext = context.has(e.id),
          flag = flags.has(e.id),
          hot = e.id === selected || e.id === pinned || flag || incident;
        const color =
          e.id === pinned || flag
            ? "#f07860"
            : isContext
              ? "#88adff"
              : e.id === selected || incident
                ? "#c1d6ed"
                : "#7c929f";
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: "transfer",
          sourceHandle: `out-${sourceSide}`,
          targetHandle: `in-${targetSide}`,
          zIndex: e.id === selected ? 10 : hot ? 5 : 0,
          ariaLabel: `${label(graph.nodes.find((n) => n.id === e.source))} to ${label(graph.nodes.find((n) => n.id === e.target))}: ${money(e)}`,
          label: money(e),
          data: {
            points: route,
            showLabel: hot || visible.length <= 12,
            flagged: flag,
            hot,
          },
          style: {
            stroke: color,
            strokeWidth: hot ? 2.5 : isContext ? 1.8 : 1.1,
            strokeDasharray: isContext && !hot ? "5 5" : undefined,
            opacity: dimmed ? 0.2 : 1,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color,
            width: 20,
            height: 20,
          },
        };
      }),
    };
  }, [
    arranged,
    graph,
    visible,
    flags,
    selected,
    focus,
    search,
    context,
    pinned,
  ]);
  if (!nodes.length)
    return (
      <div className="canvas-empty">
        <NetworkIcon size={36} />
        <h3>
          {onlyFlags
            ? "No flagged connections"
            : "Waiting for the first connection"}
        </h3>
        <p>
          {onlyFlags
            ? "Show all transfers to explore this network."
            : "Your graph will appear as statements finish processing."}
        </p>
        <ArrowRight size={18} />
      </div>
    );
  return (
    <ReactFlow
      key={graph.nodes.map((n) => n.id).join("|") + onlyFlags + contextOnly}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1.15 }}
      minZoom={0.15}
      maxZoom={2}
      onEdgeClick={(_, e) => onSelect(e.id)}
      onNodeClick={(_, n) => onNode(n.id)}
      nodesDraggable={false}
      nodesConnectable={false}
      onlyRenderVisibleElements
      colorMode="dark"
      proOptions={{ hideAttribution: true }}
    >
      <FocusViewport id={focus} />
      <Background color="#3b4752" gap={28} size={1} />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        nodeColor={(n) =>
          (n.data as EntityNode["data"]).flagged ? "#ee8069" : "#82929d"
        }
        maskColor="#15212ac9"
      />
    </ReactFlow>
  );
}

function FocusViewport({ id }: { id: string | null }) {
  const { fitView } = useReactFlow();
  const initialized = useNodesInitialized();
  useEffect(() => {
    if (!initialized) return;
    void fitView({
      nodes: id ? [{ id }] : undefined,
      padding: id ? 1 : 0.22,
      maxZoom: id ? 1.3 : 1.15,
      duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? 0
        : 200,
    });
  }, [id, initialized, fitView]);
  return null;
}
