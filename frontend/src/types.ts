export interface Entity {
  id: string;
  label?: string;
  name?: string;
  kind?: string;
  account?: string;
  bank?: string;
  identity_basis?: string;
  resolution_status?: string;
}
export interface Citation {
  source_id?: string;
  url?: string;
  locator?: string;
}
export interface Source {
  id: string;
  title: string;
  publisher: string;
  url: string;
}
export interface Edge {
  id: string;
  source: string;
  target: string;
  relation: string;
  reference?: string;
  description?: string;
  amount?: string | null;
  currency?: string | null;
  amount_paid?: string;
  payment_currency?: string;
  timestamp?: string | null;
  event_date?: string | null;
  timestamp_precision?: string | null;
  date_qualifier?: string | null;
  evidence_status?: string;
  amount_scope?: string;
  amount_qualifier?: string | null;
  citations?: Citation[];
  document_id?: string;
  source_row?: number;
  evidence?: {
    page?: number;
    quote?: string;
    citations?: Citation[];
    attachment?: string;
    row?: number;
    note?: string;
  };
  review_reasons?: string[];
}
export interface Graph {
  nodes: Entity[];
  edges: Edge[];
  has_more?: boolean;
  next_offset?: number | null;
}
export interface Case {
  id: string;
  title: string;
  hook: string;
  summary: string;
  period: string;
  caveat: string;
  nodes: Entity[];
  edges: Edge[];
  source_ids: string[];
  metrics: {
    label?: string;
    value?: string;
    amount?: string;
    currency?: string;
  }[];
}
export interface Dataset {
  cases: Case[];
  sources: Source[];
  notes: string[];
}
export interface Document {
  id: string;
  filename: string;
  status: string;
  error?: string;
  accepted: number;
  rejected: number;
}
export interface Batch {
  id: string;
  status: string;
  documents: Document[];
}
export interface Reason {
  rule: string;
  detail: string;
  related_edges?: string[];
  entity_id?: string;
}
export interface Explanation {
  kind: string;
  tree_count?: number;
  tree_paths?: {
    tree: number;
    steps: {
      feature: string;
      value: number;
      threshold: number;
      branch: string;
    }[];
    leaf_margin: number;
  }[];
  base_margin?: number;
  contribution_sum?: number;
  features?: { name: string; value: number; contribution: number }[];
  context_transaction_ids?: string[];
  cutoff_us?: number;
  context_transactions?: ContextTransaction[];
  note: string;
}
export interface Analysis {
  method: string;
  rule_scope?: string;
  summary: string;
  limitation: string;
  findings: {
    edge_id: string;
    source: string;
    target: string;
    reasons: Reason[];
  }[];
  entities: { id: string; flagged_connections: number }[];
  model: {
    status: string;
    name: string;
    reason?: string;
    run_id?: string;
    threshold?: number;
    scores?: {
      transaction_id: string;
      score: number;
      explanation?: Explanation;
    }[];
  };
  scope: { relationships: number; model_eligible: number; historical: boolean };
}
export const label = (node?: Entity) =>
  node?.label || node?.name || node?.account || node?.id || "Unknown entity";
export const human = (value?: string) =>
  (value || "Not provided").replaceAll("_", " ");
export function money(edge: Edge) {
  const amount = edge.amount ?? edge.amount_paid;
  const currency = edge.currency ?? edge.payment_currency;
  if (amount == null) return human(edge.relation);
  const n = Number(amount);
  return `${currency || ""} ${Number.isFinite(n) ? new Intl.NumberFormat("en", { notation: Math.abs(n) >= 1e6 ? "compact" : "standard", maximumFractionDigits: 2 }).format(n) : amount}`.trim();
}

export interface ContextTransaction {
  id: string;
  sender_id: string;
  receiver_id: string;
  timestamp: string;
  amount_paid: string;
  payment_currency: string;
  amount_received?: string;
  receiving_currency?: string;
  from_account?: string;
  to_account?: string;
  from_bank?: string;
  to_bank?: string;
  document_id?: string;
  source_row?: number;
  hop?: number;
}
export interface ModelExample {
  id: string;
  title: string;
  description: string;
  focus_edge_id: string;
  focus_edge_ids: string[];
  graph: Graph;
}
export interface ModelDemo {
  id: string;
  title: string;
  description: string;
  notice: string;
  data_label: string;
  execution_label: string;
  source_rows: number;
  source_sha256: string;
  run_id: string;
  generated_at: string;
  focus_edge_id: string;
  graph: Graph;
  models: Record<string, Analysis["model"]>;
  generator: string;
  provenance: { kind: string; training_policy: string };
  examples?: ModelExample[];
}
export interface Investigation {
  id: string;
  name: string;
  batchIds: string[];
  createdAt: string;
}
export type ModelName = "graphsage" | "graph_xgboost";
