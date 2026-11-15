import {
  label,
  type Analysis,
  type ContextTransaction,
  type Edge,
  type Graph,
  type ModelDemo,
} from "./types";
export function contextEdge(row: ContextTransaction): Edge {
  return {
    id: row.id,
    source: row.sender_id,
    target: row.receiver_id,
    relation: "transfer",
    timestamp: row.timestamp,
    amount_paid: row.amount_paid,
    payment_currency: row.payment_currency,
    document_id: row.document_id,
    source_row: row.source_row,
  };
}
export function withContext(graph: Graph, rows: ContextTransaction[]): Graph {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n])),
    edges = new Map(graph.edges.map((e) => [e.id, e]));
  for (const row of rows) {
    edges.set(row.id, edges.get(row.id) || contextEdge(row));
    for (const [id, account, bank] of [
      [row.sender_id, row.from_account, row.from_bank],
      [row.receiver_id, row.to_account, row.to_bank],
    ])
      if (!nodes.has(id!))
        nodes.set(id!, {
          id: id!,
          account,
          bank,
          kind: "account",
          label: account ? `Account ${account}` : id,
        });
  }
  return {
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    has_more: graph.has_more,
  };
}
export function demoAnalysis(
  demo: ModelDemo,
  model: Analysis["model"],
): Analysis {
  const edges = new Map(demo.graph.edges.map((e) => [e.id, e]));
  const findings = (model.scores || [])
    .filter((s) => s.score > 0 && edges.has(s.transaction_id))
    .map((s) => {
      const edge = edges.get(s.transaction_id)!;
      return {
        edge_id: edge.id,
        source: edge.source,
        target: edge.target,
        reasons: [
          {
            rule: "Model positive decision",
            detail: `Raw margin ${s.score.toFixed(4)} is above the classifier boundary of zero. This is a review signal, not proof of laundering.`,
          },
        ],
      };
    });
  const counts = new Map<string, number>();
  findings.forEach((f) =>
    new Set([f.source, f.target]).forEach((id) =>
      counts.set(id, (counts.get(id) || 0) + 1),
    ),
  );
  return {
    method: "trained_model",
    summary: findings.length
      ? "The selected transfer was flagged by the model."
      : "The selected transfer was not flagged by the model.",
    limitation:
      "Synthetic teaching data and a small demonstration model. These outputs do not establish real-world accuracy or criminal responsibility.",
    findings,
    entities: [...counts].map(([id, flagged_connections]) => ({
      id,
      flagged_connections,
    })),
    model,
    scope: {
      relationships: demo.graph.edges.length,
      model_eligible: model.scores?.length || 0,
      historical: false,
    },
  };
}
export function filterEdges(
  graph: Graph,
  search: string,
  flagged: Set<string>,
  onlyFlagged: boolean,
  account: string | null,
) {
  const needle = search.trim().toLowerCase();
  const names = new Map(graph.nodes.map((n) => [n.id, label(n).toLowerCase()]));
  return graph.edges.filter(
    (e) =>
      (!onlyFlagged || flagged.has(e.id)) &&
      (!account || e.source === account || e.target === account) &&
      (!needle ||
        [
          names.get(e.source),
          names.get(e.target),
          e.description,
          e.reference,
          e.id,
        ].some((value) => value?.toLowerCase().includes(needle))),
  );
}
export function pageRange<T>(rows: T[], page: number, size = 10) {
  const max = Math.max(0, Math.ceil(rows.length / size) - 1),
    index = Math.min(page, max);
  return {
    items: rows.slice(index * size, index * size + size),
    page: index,
    max,
    start: rows.length ? index * size + 1 : 0,
    end: Math.min((index + 1) * size, rows.length),
  };
}
export const modelNames: Record<string, string> = {
  graphsage: "GraphSAGE",
  graph_xgboost: "Graph XGBoost",
  transaction_xgboost: "Transaction XGBoost",
};
export function dateLabel(
  value?: string | null,
  precision?: string | null,
  qualifier?: string | null,
) {
  if (!value) return "Date not provided";
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value) || precision === "date";
  const at = new Date(
    /^\d{4}-\d{2}-\d{2}$/.test(value) ? value + "T00:00:00Z" : value,
  );
  if (Number.isNaN(at.getTime())) return "Date not provided";
  const formatted = dateOnly
    ? at.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      })
    : at.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
      }) + " UTC";
  return qualifier ? `${qualifier} ${formatted}` : formatted;
}

export function graphEdgesForView(
  graph: Graph,
  flags: Set<string>,
  onlyFlags: boolean,
  context: Set<string>,
  pinned: string | null | undefined,
  contextOnly: boolean | undefined,
) {
  return graph.edges.filter((e) =>
    contextOnly
      ? context.has(e.id) || e.id === pinned
      : !onlyFlags || flags.has(e.id),
  );
}
export function visibleExport(
  graph: Graph,
  analysis: Analysis | null,
  flags: Set<string>,
  onlyFlags: boolean,
  context: Set<string>,
  pinned: string | null,
  contextOnly: boolean,
) {
  const edges = graphEdgesForView(
      graph,
      flags,
      onlyFlags,
      context,
      pinned,
      contextOnly,
    ),
    ids = new Set(edges.map((e) => e.id)),
    nodes = new Set(edges.flatMap((e) => [e.source, e.target]));
  const model = analysis
    ? {
        ...analysis.model,
        scores: analysis.model.scores
          ?.filter((s) => ids.has(s.transaction_id))
          .map((s) => {
            const detail = s.explanation;
            return {
              ...s,
              explanation: detail
                ? {
                    ...detail,
                    context_transaction_ids:
                      detail.context_transaction_ids?.filter((id) =>
                        ids.has(id),
                      ),
                    context_transactions: detail.context_transactions?.filter(
                      (row) => ids.has(row.id),
                    ),
                    context_export_scope: detail.context_transaction_ids
                      ? {
                          sampled_count: detail.context_transaction_ids.length,
                          exported_count: detail.context_transaction_ids.filter(
                            (id) => ids.has(id),
                          ).length,
                          note: "Only context records visible in this export are included.",
                        }
                      : undefined,
                  }
                : undefined,
            };
          }),
      }
    : null;
  return {
    graph: {
      ...graph,
      edges,
      nodes: graph.nodes.filter((n) => nodes.has(n.id)),
    },
    analysis: analysis
      ? {
          ...analysis,
          model,
          entities: analysis.entities.filter((e) => nodes.has(e.id)),
          findings: analysis.findings
            .filter((f) => ids.has(f.edge_id))
            .map((f) => ({
              ...f,
              reasons: f.reasons.map((r) => ({
                ...r,
                related_edges: r.related_edges?.filter((id) => ids.has(id)),
              })),
            })),
          scope: {
            ...analysis.scope,
            relationships: edges.length,
            export_note:
              "Findings and scores restricted to exported relationships.",
          },
        }
      : null,
  };
}
