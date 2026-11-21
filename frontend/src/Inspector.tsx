import { useId, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  GitBranch,
  Info,
  LoaderCircle,
  Network,
  Pin,
  ShieldCheck,
  TriangleAlert,
  Check,
  Maximize2,
} from "lucide-react";
import {
  label,
  money,
  human,
  type Analysis,
  type Edge,
  type Graph,
  type Source,
  type ModelDemo,
} from "./types";
import { dateLabel, modelNames, contextEdge } from "./domain";
export type InspectorTab = "analysis" | "evidence" | "entities";
export default function Inspector({
  graph,
  analysis,
  selected,
  pinned,
  tab,
  onTab,
  onInspect,
  onPin,
  onFocus,
  focus,
  sources,
  pending,
  mode,
  notice,
  execution,
  contextView,
  onContextView,
  expanded,
  onExpand,
  demo,
}: {
  graph: Graph;
  analysis: Analysis | null;
  selected: string | null;
  pinned: string | null;
  tab: InspectorTab;
  onTab: (tab: InspectorTab) => void;
  onInspect: (id: string) => void;
  onPin: (id: string) => void;
  onFocus: (id: string) => void;
  focus: string | null;
  sources: Source[];
  pending: boolean;
  mode: "demo" | "historical" | "uploaded";
  notice?: string;
  execution?: string;
  contextView: boolean;
  onContextView: () => void;
  expanded: boolean;
  onExpand: () => void;
  demo?: ModelDemo;
}) {
  const id = useId();
  const tabs: InspectorTab[] = ["analysis", "evidence", "entities"];
  const edge = graph.edges.find((e) => e.id === selected);
  const pinEdge = graph.edges.find((e) => e.id === pinned);
  const prediction = analysis?.model.scores?.find((s) => s.transaction_id === pinned);
  const explanation = prediction?.explanation;
  const componentCredits =
    prediction?.component_contributions || explanation?.component_contributions;
  const baseMargin = prediction?.base_margin ?? explanation?.base_margin;
  const contexts = explanation?.context_transactions || [];
  const finding = analysis?.findings.find((f) => f.edge_id === selected);
  const [visibleFindings, setVisibleFindings] = useState(10);
  return (
    <aside id="investigation-details" tabIndex={-1} className={`inspector ${expanded ? "expanded" : ""}`} aria-label="Investigation details">
      <div className="inspector-top">
        <div className="inspector-tabs" role="tablist" aria-label="Investigation details">
          {tabs.map((name, index) => (
            <button
              key={name}
              id={`${id}-${name}`}
              role="tab"
              aria-controls={`${id}-panel-${name}`}
              aria-selected={tab === name}
              tabIndex={tab === name ? 0 : -1}
              onClick={() => onTab(name)}
              onKeyDown={(e) => {
                if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
                  e.preventDefault();
                  const next =
                    e.key === "Home"
                      ? 0
                      : e.key === "End"
                        ? 2
                        : (index + (e.key === "ArrowRight" ? 1 : -1) + 3) % 3;
                  onTab(tabs[next]);
                  document.getElementById(`${id}-${tabs[next]}`)?.focus();
                }
              }}
            >
              {name === "analysis" ? "Analysis" : name === "evidence" ? "Evidence" : "Entities"}
            </button>
          ))}
        </div>
        <button
          className="icon-button"
          onClick={onExpand}
          aria-label={expanded ? "Reduce explanation width" : "Expand explanation width"}
          aria-pressed={expanded}
        >
          <Maximize2 size={15} />
        </button>
      </div>
      <div
        className="inspector-scroll"
        id={`${id}-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`${id}-${tab}`}
        tabIndex={0}
      >
        {tab === "analysis" ? (
          <>
            {pending ? (
              <div className="analysis-wait" role="status">
                <LoaderCircle className="spin" size={26} />
                <h2>
                  {mode === "historical" ? "Reviewing source evidence" : "Running the hybrid"}
                </h2>
                <p>
                  You can continue exploring the graph while the model runs.
                </p>
              </div>
            ) : !analysis ? (
              <div className="analysis-intro">
                <h2>
                  {mode === "demo"
                    ? "Follow the score."
                    : mode === "historical"
                      ? "Inspect the source evidence."
                      : "Analyze your statements."}
                </h2>
                <p>
                  {mode === "demo"
                    ? "Choose Analyze network, then inspect a transfer to see the earlier activity and transaction fields that affected its score."
                    : mode === "historical"
                      ? "This is a reconstruction from public sources. Evidence review highlights cited relationships; it does not run an ML model on historical claims."
                      : "When processing finishes, analyze the network. Complete records support the hybrid pipeline; partial records use pattern checks."}
                </p>
              </div>
            ) : (
              <>
                <div
                  className={`analysis-verdict ${analysis.findings.length ? "flagged" : ""}`}
                  role="status"
                >
                  <span>
                    {analysis.findings.length ? <TriangleAlert size={21} /> : <Check size={21} />}
                  </span>
                  <div>
                    <h2>
                      {analysis.findings.length
                        ? `${analysis.findings.length} ${analysis.findings.length === 1 ? "connection" : "connections"} to review`
                        : "No flags in this analysis"}
                    </h2>
                    <p>
                      {mode === "historical"
                        ? "Source-linked case evidence"
                        : analysis.model.status === "completed" && !!analysis.model.scores?.length
                          ? `${modelNames[analysis.model.name]} · ${execution || "Live analysis"}`
                          : "Transparent pattern checks"}
                    </p>
                  </div>
                </div>
                {(analysis.model.status !== "completed" || !analysis.model.scores?.length) && (
                  <p className="method-disclosure">
                    {analysis.model.reason || "No compatible transactions were scored."}
                  </p>
                )}
                {pinEdge && prediction && (
                  <section className="pinned-prediction">
                    <div className="pin-label">
                      <Pin size={13} />
                      Prediction being explained
                    </div>
                    <Route edge={pinEdge} graph={graph} />
                    <div className="margin-readout">
                      <div>
                        <span>Final decision margin</span>
                        <strong>
                          {prediction.score > 0 ? "+" : ""}
                          {prediction.score.toFixed(3)}
                        </strong>
                      </div>
                      <span className={`status-tag ${prediction.score > 0 ? "review" : "neutral"}`}>
                        {prediction.score > 0 ? "Model flagged" : "Not flagged"}
                      </span>
                    </div>
                    <p className="interpretation">
                      Above 0 is a positive model decision. This is not a confidence percentage or a
                      finding of wrongdoing.
                    </p>
                    {selected !== pinned && (
                      <button
                        className="text-button"
                        onClick={() => {
                          onInspect(pinned!);
                          onTab("analysis");
                        }}
                      >
                        Return to this prediction
                        <ArrowUpRight size={13} />
                      </button>
                    )}
                  </section>
                )}
                {analysis.model.name === "hybrid" && prediction && componentCredits && (
                  <section className="explanation-section hybrid-credit">
                    <div className="explanation-heading">
                      <GitBranch size={18} />
                      <h3>Credit for this finding</h3>
                    </div>
                    <p>
                      <strong>GraphSAGE:</strong> neighborhood encoder.{" "}
                      <strong>XGBoost:</strong> final classifier and TreeSHAP attribution.
                      Signed credits group the final tree model’s inputs; they are not separate
                      model votes.
                    </p>
                    <dl className="evidence-facts">
                      <div>
                        <dt>Base margin</dt>
                        <dd>{baseMargin?.toFixed(3)}</dd>
                      </div>
                      {componentCredits.map((component) => (
                        <div key={component.name}>
                          <dt>{component.name}</dt>
                          <dd>
                            {component.contribution >= 0 ? "+" : ""}
                            {component.contribution.toFixed(3)}
                          </dd>
                        </div>
                      ))}
                      <div>
                        <dt>Final decision</dt>
                        <dd>{prediction?.score.toFixed(3)}</dd>
                      </div>
                    </dl>
                    <p className="interpretation">
                      Base margin + both component credits = final margin, before rounding.
                      Credits describe this model decision, not criminal responsibility or which
                      historical edge caused it.
                    </p>
                    {!explanation && (
                      <p className="method-disclosure">
                        Component credits are available for every score; detailed context and
                        tree paths are included for the ten highest-ranked transfers.
                      </p>
                    )}
                  </section>
                )}
                {(explanation?.kind === "graph_context" || explanation?.kind === "hybrid") && (
                  <section className="explanation-section">
                    <div className="explanation-heading">
                      <Network size={18} />
                      <h3>The neighborhood behind the score</h3>
                    </div>
                    <p>
                      These are the actual prior transactions sampled by GraphSAGE. The model uses
                      incoming and outgoing history across two layers.
                    </p>
                    <button
                      className={`button context-action ${contextView ? "active" : ""}`}
                      onClick={onContextView}
                    >
                      <Network size={15} />
                      {contextView ? "Show the full network" : "Isolate model context"}
                    </button>
                    {explanation.cutoff_us && (
                      <div className="context-cutoff">
                        <span>History cutoff</span>
                        <strong>
                          {dateLabel(new Date(explanation.cutoff_us / 1000).toISOString())}
                        </strong>
                      </div>
                    )}
                    {[1, 2].map((hop) => {
                      const rows = contexts.filter((r) => r.hop === hop);
                      return rows.length ? (
                        <div key={hop} className="context-group">
                          <h4>
                            {hop === 1 ? "Direct historical context" : "Second-layer context"}
                            <span>{rows.length}</span>
                          </h4>
                          {rows.map((row) => {
                            const item = contextEdge(row);
                            return (
                              <button
                                key={row.id}
                                className={`context-record ${selected === row.id ? "selected" : ""}`}
                                onClick={() => onInspect(row.id)}
                              >
                                <span className="context-record-mark" />
                                <div>
                                  <strong>
                                    {label(graph.nodes.find((n) => n.id === row.sender_id))}
                                    <ArrowRight size={11} />
                                    {label(graph.nodes.find((n) => n.id === row.receiver_id))}
                                  </strong>
                                  <span>
                                    {money(item)} · {dateLabel(row.timestamp)}
                                  </span>
                                </div>
                                <ArrowUpRight size={13} />
                              </button>
                            );
                          })}
                        </div>
                      ) : null;
                    })}
                    {!contexts.length && (
                      <p className="empty-copy">
                        No historical neighbors were returned for this prediction.
                      </p>
                    )}
                    <div className="explanation-note">
                      <Info size={14} />
                      <p>
                        Sampled context shows what the model could use. It does not identify which
                        neighbor caused the prediction.
                      </p>
                    </div>
                  </section>
                )}
                {(explanation?.kind === "tree_shap" || explanation?.kind === "hybrid") && (
                  <section className="explanation-section">
                    <div className="explanation-heading">
                      <GitBranch size={18} />
                      <h3>What moved the decision</h3>
                    </div>
                    <p>
                      Actual feature contributions from the tree ensemble. Positive values raise the
                      model margin; negative values lower it.
                    </p>
                    <div className="contribution-axis">
                      <span>Lowers margin</span>
                      <span>Raises margin</span>
                    </div>
                    {explanation.features?.map((feature, index) => {
                      const max = Math.max(
                        ...explanation.features!.map((f) => Math.abs(f.contribution)),
                        0.001,
                      );
                      return (
                        <div className="feature-contribution" key={index}>
                          <div>
                            <span>{feature.name}</span>
                            <strong>
                              {feature.contribution > 0 ? "+" : ""}
                              {feature.contribution.toFixed(3)}
                            </strong>
                          </div>
                          <div className="contribution-track">
                            <i
                              className={feature.contribution >= 0 ? "positive" : "negative"}
                              style={{
                                width: `${(Math.abs(feature.contribution) / max) * 48}%`,
                              }}
                            />
                          </div>
                        </div>
                      );
                    })}
                    <p className="tree-base">
                      Base margin {explanation.base_margin?.toFixed(3)} + all contributions{" "}
                      {explanation.contribution_sum?.toFixed(3)} = {prediction?.score.toFixed(3)}
                    </p>
                    <details className="tree-disclosure">
                      <summary>
                        Follow the actual tree paths
                        <GitBranch size={14} />
                      </summary>
                      <p>
                        First {explanation.tree_paths?.length} of {explanation.tree_count} trees.
                        These paths are examples from the ensemble, not the whole decision.
                      </p>
                      {explanation.tree_paths?.map((tree) => (
                        <div className="decision-tree" key={tree.tree}>
                          <h4>Tree {tree.tree}</h4>
                          {tree.steps.map((step, index) => (
                            <div className="tree-step" key={index}>
                              <span>{step.feature}</span>
                              <code>
                                {step.value.toFixed(3)} {step.branch === "less_than" ? "<" : "≥"}{" "}
                                {step.threshold.toFixed(3)}
                              </code>
                            </div>
                          ))}
                          <div className="tree-leaf">
                            Leaf contribution <strong>{tree.leaf_margin.toFixed(4)}</strong>
                          </div>
                        </div>
                      ))}
                    </details>
                    <p className="interpretation">{explanation.note}</p>
                  </section>
                )}
                {!explanation &&
                  analysis.model.name !== "hybrid" &&
                  analysis.model.status === "completed" &&
                  (analysis.model.scores?.length || 0) > 1 && (
                    <p className="method-disclosure">
                      Detailed explanations are available for the ten highest model margins. Select
                      one from the scored transfers below.
                    </p>
                  )}
                {(!prediction || analysis.findings.length > 1) && (
                  <section className="review-list">
                    <h3>
                      {mode === "historical" ? "Evidence to inspect" : "Review queue"}
                      <span>{analysis.findings.length}</span>
                    </h3>
                    {analysis.findings.slice(0, visibleFindings).map((f) => {
                      const item = graph.edges.find((e) => e.id === f.edge_id);
                      return item ? (
                        <button
                          key={f.edge_id}
                          className={selected === f.edge_id ? "selected" : ""}
                          onClick={() => {
                            onInspect(f.edge_id);
                            if (
                              analysis.model.scores?.some((s) => s.transaction_id === f.edge_id)
                            ) {
                              onPin(f.edge_id);
                              onTab("analysis");
                            }
                          }}
                        >
                          <span className="review-item-dot" />
                          <div>
                            <strong>{label(graph.nodes.find((n) => n.id === f.source))}</strong>
                            <span>
                              <ArrowRight size={12} />
                              {label(graph.nodes.find((n) => n.id === f.target))}
                            </span>
                            <small>{f.reasons.map((r) => r.rule).join(" · ")}</small>
                          </div>
                          <ArrowUpRight size={14} />
                        </button>
                      ) : null;
                    })}
                    {!analysis.findings.length && (
                      <p className="empty-copy">
                        No available prediction or configured rule flagged this view. That does not
                        certify the network as safe.
                      </p>
                    )}
                    {analysis.findings.length > visibleFindings && (
                      <button
                        className="text-button"
                        onClick={() => setVisibleFindings((v) => v + 10)}
                      >
                        Show more review candidates
                        <ChevronDown size={14} />
                      </button>
                    )}
                  </section>
                )}

                {analysis.model.scores && analysis.model.scores.length > 1 && (
                  <details className="scored-transfers">
                    <summary>All scored transfers ({analysis.model.scores.length})</summary>
                    {analysis.model.scores.map((item) => (
                      <button
                        key={item.transaction_id}
                        onClick={() => {
                          onPin(item.transaction_id);
                          onInspect(item.transaction_id);
                          onTab("analysis");
                        }}
                      >
                        <span>
                          {money(
                            graph.edges.find((e) => e.id === item.transaction_id) || {
                              id: "",
                              source: "",
                              target: "",
                              relation: "transfer",
                            },
                          )}
                        </span>
                        <strong>{item.score.toFixed(3)}</strong>
                      </button>
                    ))}
                  </details>
                )}
                <div className="method-footnote">
                  <ShieldCheck size={15} />
                  <p>
                    {analysis.limitation}
                    {analysis.rule_scope ? " " + analysis.rule_scope : ""}
                  </p>
                </div>
                {analysis.model.run_id && (
                  <details className="run-details">
                    <summary>Run provenance</summary>
                    <dl>
                      <div>
                        <dt>Model</dt>
                        <dd>{modelNames[analysis.model.name]}</dd>
                      </div>
                      <div>
                        <dt>Run ID</dt>
                        <dd>{analysis.model.run_id}</dd>
                      </div>
                      <div>
                        <dt>Execution</dt>
                        <dd>{execution || "Live analysis"}</dd>
                      </div>
                      {demo && (
                        <>
                          <div>
                            <dt>Source</dt>
                            <dd>
                              Project-generated teaching fixture ·{" "}
                              {demo.source_rows.toLocaleString()} rows
                            </dd>
                          </div>
                          <div>
                            <dt>Data SHA-256</dt>
                            <dd>{demo.source_sha256}</dd>
                          </div>
                        </>
                      )}
                    </dl>
                  </details>
                )}
              </>
            )}
            {notice && !(mode === "demo" && analysis) && (
              <div className="case-notice">
                <BookOpen size={15} />
                <p>{notice}</p>
              </div>
            )}
          </>
        ) : tab === "evidence" ? (
          edge ? (
            <>
              <div className="evidence-heading">
                <span className="status-tag neutral">
                  {edge.document_id === "synthetic-transfers.csv"
                    ? "Synthetic source record"
                    : edge.document_id
                      ? "Uploaded statement"
                      : "Public source evidence"}
                </span>
                <h2>Read the connection.</h2>
              </div>
              <Route edge={edge} graph={graph} />
              <dl className="evidence-facts">
                <div>
                  <dt>Amount</dt>
                  <dd>
                    {edge.amount ?? edge.amount_paid ?? "Not established"}{" "}
                    {edge.currency || edge.payment_currency || ""}
                  </dd>
                </div>
                <div>
                  <dt>Relationship</dt>
                  <dd>{human(edge.relation)}</dd>
                </div>
                <div>
                  <dt>Date</dt>
                  <dd>
                    {dateLabel(
                      edge.timestamp || edge.event_date,
                      edge.timestamp_precision,
                      edge.date_qualifier,
                    )}
                  </dd>
                </div>
                {edge.document_id && (
                  <div>
                    <dt>Source record</dt>
                    <dd>
                      {edge.document_id === "synthetic-transfers.csv"
                        ? edge.document_id
                        : edge.document_id.slice(0, 14) + "…"}{" "}
                      · row {edge.source_row || "—"}
                      {edge.evidence?.page ? ` · page ${edge.evidence.page}` : ""}
                    </dd>
                  </div>
                )}
                {edge.amount_scope && (
                  <div>
                    <dt>Amount scope</dt>
                    <dd>{human(edge.amount_scope)}</dd>
                  </div>
                )}
                {edge.evidence?.attachment && (
                  <div>
                    <dt>Extraction source</dt>
                    <dd>
                      {edge.evidence.attachment} · row {edge.evidence.row}
                      <br />
                      {edge.evidence.note}
                    </dd>
                  </div>
                )}
                {edge.amount_qualifier && (
                  <div>
                    <dt>Qualifier</dt>
                    <dd>{edge.amount_qualifier}</dd>
                  </div>
                )}
                {edge.evidence_status && (
                  <div>
                    <dt>Evidence status</dt>
                    <dd>{human(edge.evidence_status)}</dd>
                  </div>
                )}
              </dl>
              {edge.description && <p className="evidence-description">{edge.description}</p>}
              {edge.evidence?.quote && <blockquote>{edge.evidence.quote}</blockquote>}
              {finding && (
                <section className="evidence-reasons">
                  <h3>Why it was highlighted</h3>
                  {finding.reasons.map((r, index) => (
                    <div key={index}>
                      <strong>
                        <TriangleAlert size={14} />
                        {r.rule}
                      </strong>
                      <p>{r.detail}</p>
                    </div>
                  ))}
                </section>
              )}
              {analysis?.model.scores?.some((s) => s.transaction_id === edge.id) && (
                <button
                  className="button secondary full-width"
                  onClick={() => {
                    onPin(edge.id);
                    onTab("analysis");
                  }}
                >
                  <Network size={15} />
                  Explain this prediction
                  <ArrowRight size={15} />
                </button>
              )}
              {pinned && selected !== pinned && (
                <button
                  className="text-button return-pin"
                  onClick={() => {
                    onInspect(pinned);
                    onTab("analysis");
                  }}
                >
                  <Pin size={13} />
                  Back to pinned prediction
                </button>
              )}
              <section className="source-links">
                <h3>Source evidence</h3>
                {(edge.citations || edge.evidence?.citations || []).map((citation, index) => {
                  const source = sources.find((s) => s.id === citation.source_id);
                  const url = citation.url || source?.url;
                  return url && /^https?:\/\//.test(url) ? (
                    <a href={url} key={index} target="_blank" rel="noreferrer">
                      <BookOpen size={17} />
                      <div>
                        <strong>{source?.publisher || "Source document"}</strong>
                        <span>{source?.title || url}</span>
                        <small>{citation.locator}</small>
                      </div>
                      <ExternalLink size={14} />
                    </a>
                  ) : null;
                })}
                {!(edge.citations || edge.evidence?.citations || []).length && (
                  <p>
                    {edge.document_id === "synthetic-transfers.csv"
                      ? "This record was generated for the teaching demo. The downloadable source fixture matches the recorded run’s data fingerprint."
                      : "Review the original statement before drawing conclusions. Extracted text and model output may contain errors."}
                  </p>
                )}
                {edge.document_id === "synthetic-transfers.csv" && (
                  <a className="fixture-link" href="/demo-transfers.csv" download>
                    Download synthetic source fixture
                    <ArrowUpRight size={14} />
                  </a>
                )}
              </section>
            </>
          ) : (
            <div className="inspector-empty">
              <FileRecord />
              <h2>Every edge has a story.</h2>
              <p>Select a transfer in the graph or ledger to inspect its source record.</p>
            </div>
          )
        ) : (
          <>
            <h2>Connected entities</h2>
            <p className="panel-lead">
              Select an entity to focus its transfers. A connection is not a finding of
              responsibility.
            </p>
            <div className="entity-list">
              {[...graph.nodes]
                .sort(
                  (a, b) =>
                    (analysis?.entities.find((e) => e.id === b.id)?.flagged_connections || 0) -
                    (analysis?.entities.find((e) => e.id === a.id)?.flagged_connections || 0),
                )
                .map((n) => {
                  const flagged =
                    analysis?.entities.find((e) => e.id === n.id)?.flagged_connections || 0;
                  return (
                    <button
                      className={focus === n.id ? "selected" : ""}
                      key={n.id}
                      onClick={() => onFocus(n.id)}
                    >
                      <span className={`entity-list-dot ${flagged ? "review" : ""}`} />
                      <div>
                        <strong>{label(n)}</strong>
                        <span>
                          {human(n.kind || "account")}
                          {flagged ? ` · ${flagged} flagged connections` : ""}
                        </span>
                      </div>
                      <ChevronRight size={15} />
                    </button>
                  );
                })}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
function Route({ edge, graph }: { edge: Edge; graph: Graph }) {
  return (
    <div className="evidence-route">
      <span>{label(graph.nodes.find((n) => n.id === edge.source))}</span>
      <ArrowRight size={16} />
      <span>{label(graph.nodes.find((n) => n.id === edge.target))}</span>
    </div>
  );
}
function FileRecord() {
  return <BookOpen size={32} strokeWidth={1.4} />;
}
