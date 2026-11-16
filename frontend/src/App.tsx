import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  Download,
  FolderOpen,
  GitBranch,
  Info,
  LoaderCircle,
  Network as NetworkIcon,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Upload,
  X,
  Play,
} from "lucide-react";
import { api, graphForBatches, jsonPost } from "./api";
import { demoAnalysis, withContext, visibleExport } from "./domain";
import {
  label,
  type Analysis,
  type Batch,
  type Dataset,
  type Graph,
  type Investigation,
  type ModelDemo,
  type ModelName,
} from "./types";
import Inspector, { type InspectorTab } from "./Inspector";
import Ledger from "./Ledger";
import UploadPanel from "./UploadPanel";
import { useUpload } from "./useUpload";
const Network = lazy(() => import("./Network"));
const EMPTY: Graph = { nodes: [], edges: [] };
function stored<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}
function initialInvestigations(): Investigation[] {
  const saved = stored<Investigation[]>("dolos:investigations", []);
  if (
    Array.isArray(saved) &&
    saved.every(
      (x) =>
        x &&
        typeof x.id === "string" &&
        typeof x.name === "string" &&
        Array.isArray(x.batchIds) &&
        x.batchIds.every((id) => typeof id === "string"),
    )
  )
    return saved;
  return [];
}
function Brand() {
  return (
    <span className="brand">
      <span className="brand-symbol">
        <span />
      </span>
      dolos<span className="brand-stop">.</span>
    </span>
  );
}
async function loadAsset<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok)
    throw new Error("Could not load the example data. Refresh or try again.");
  return response.json();
}
export default function App() {
  const [selection, setSelection] = useState(() => {
    const saved = stored<unknown>("dolos:selection", "demo");
    return typeof saved === "string" &&
      saved.length < 150 &&
      (saved === "demo" ||
        saved.startsWith("example:") ||
        saved.startsWith("investigation:"))
      ? saved
      : "demo";
  });
  const [view, setView] = useState<"workspace" | "upload" | "about">(
    "workspace",
  );
  const [investigations, setInvestigations] = useState<Investigation[]>(
    initialInvestigations,
  );
  const model: ModelName = "hybrid";
  const [selected, setSelected] = useState<string | null>(null),
    [pinned, setPinned] = useState<string | null>(null),
    [focus, setFocus] = useState<string | null>(null);
  const [search, setSearch] = useState(""),
    [onlyFlags, setOnlyFlags] = useState(false),
    [contextView, setContextView] = useState(false),
    [expanded, setExpanded] = useState(false),
    [tab, setTab] = useState<InspectorTab>("analysis");
  const [after, setAfter] = useState("");
  const [draftAfter, setDraftAfter] = useState("");
  const [before, setBefore] = useState(""),
    [draftBefore, setDraftBefore] = useState("");
  const [result, setResult] = useState<{
    key: string;
    analysis: Analysis;
    execution: string;
  } | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [documentBatchIds, setDocumentBatchIds] = useState<string[]>([]);
  const queryClient = useQueryClient();
  const mode =
    selection === "demo" || selection.startsWith("example:")
      ? "demo"
      : selection.startsWith("case:")
        ? "historical"
        : "uploaded";
  const datasetQuery = useQuery({
    queryKey: ["cases"],
    queryFn: () => loadAsset<Dataset>("/cases.json"),
    staleTime: Infinity,
  });
  const demoQuery = useQuery({
    queryKey: ["model-demo"],
    queryFn: () => loadAsset<ModelDemo>("/model-demo.json"),
    staleTime: Infinity,
  });
  const selectedExample = selection.startsWith("example:")
    ? demoQuery.data?.examples?.find(
        (example) => example.id === selection.slice(8),
      )
    : undefined;
  const activeDemo = useMemo<ModelDemo | undefined>(
    () =>
      demoQuery.data && selectedExample
        ? { ...demoQuery.data, ...selectedExample }
        : demoQuery.data,
    [demoQuery.data, selectedExample],
  );
  useEffect(() => {
    if (
      selection.startsWith("example:") &&
      demoQuery.data &&
      !selectedExample
    ) {
      setSelection("demo");
      setResult(null);
      setSelected(null);
      setPinned(null);
      setContextView(false);
    }
  }, [selection, demoQuery.data, selectedExample]);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () =>
      api<{ model_configured: boolean; available_models?: string[] }>(
        "/health",
      ),
    retry: false,
    staleTime: 30000,
    refetchInterval: 30000,
  });
  const demoStatus = useQuery({
    queryKey: ["demo-status"],
    queryFn: () => api<{ live_available: boolean }>("/demo/model/status"),
    retry: false,
    staleTime: 30000,
  });
  const currentCase = datasetQuery.data?.cases.find(
    (c) => c.id === selection.slice(5),
  );
  const investigation = investigations.find(
    (i) => selection === `investigation:${i.id}`,
  );
  const batchIds = investigation?.batchIds || [];
  const upload = useUpload((jobId, batchId) => {
    setInvestigations((prev) => {
      const existing = prev.find((i) => i.id === jobId);
      return existing
        ? prev.map((i) =>
            i.id === jobId
              ? { ...i, batchIds: [...new Set([...i.batchIds, batchId])] }
              : i,
          )
        : [
            ...prev,
            {
              id: jobId,
              name: `Statement investigation ${prev.length + 1}`,
              batchIds: [batchId],
              createdAt: new Date().toISOString(),
            },
          ];
    });
    void queryClient.invalidateQueries({ queryKey: ["batches"] });
  });
  const uploadInvestigation = investigations.find((i) => i.id === upload.jobId);
  const activeBatchIds =
    view === "upload"
      ? documentBatchIds.length
        ? documentBatchIds
        : uploadInvestigation?.batchIds || []
      : batchIds;
  const batches = useQuery({
    queryKey: ["batches", activeBatchIds],
    queryFn: () =>
      Promise.all(
        activeBatchIds.map((id) =>
          api<Batch>(`/batches/${encodeURIComponent(id)}`),
        ),
      ),
    enabled: activeBatchIds.length > 0,
    refetchInterval: (q) =>
      q.state.data?.every((b) =>
        ["completed", "needs_review"].includes(b.status),
      )
        ? false
        : 2000,
  });
  const graphQuery = useQuery({
    queryKey: [
      "graph",
      batchIds,
      before,
      after,
      batches.data
        ?.map((b) => b.documents.map((d) => d.id + ":" + d.status).join("|"))
        .join(";"),
    ],
    queryFn: () => graphForBatches(batchIds, before, after),
    enabled: mode === "uploaded" && batchIds.length > 0 && view === "workspace",
  });
  const baseGraph = useMemo<Graph>(
    () =>
      mode === "demo"
        ? activeDemo?.graph || EMPTY
        : mode === "historical"
          ? currentCase
            ? { nodes: currentCase.nodes, edges: currentCase.edges }
            : EMPTY
          : graphQuery.data || EMPTY,
    [mode, activeDemo, currentCase, graphQuery.data],
  );
  const analysisKey = JSON.stringify([
    selection,
    model,
    before,
    after,
    baseGraph.edges.map((e) => e.id),
  ]);
  const keyRef = useRef(analysisKey);
  keyRef.current = analysisKey;
  const analysis = result?.key === analysisKey ? result.analysis : null;
  const execution = result?.key === analysisKey ? result.execution : "";
  const prediction = analysis?.model.scores?.find(
    (s) => s.transaction_id === pinned,
  );
  const contextRows = prediction?.explanation?.context_transactions || [];
  const graph = useMemo(
    () => withContext(baseGraph, contextRows),
    [baseGraph, prediction],
  );
  const context = useMemo(
    () => new Set(prediction?.explanation?.context_transaction_ids || []),
    [prediction],
  );
  const flags = useMemo(
    () => new Set(analysis?.findings.map((f) => f.edge_id) || []),
    [analysis],
  );
  const docs = batches.data?.flatMap((b) => b.documents) || [];
  const processing = docs.some(
    (d) => d.status === "queued" || d.status === "processing",
  );
  const hasCanonical = baseGraph.edges.some(
    (e) => e.amount_paid != null && e.payment_currency && e.timestamp,
  );
  const availableModels = health.data?.available_models || [];
  const eligibleModels = availableModels.filter((name) => name === "hybrid");
  const trainedAvailable = hasCanonical && availableModels.includes(model);
  const liveDemo = !!demoStatus.data?.live_available;
  const pendingBatches = activeBatchIds.length > 0 && batches.isPending;
  const analyze = useMutation({
    mutationFn: async (input: {
      key: string;
      selection: string;
      model: ModelName;
      batchIds: string[];
      edgeIds: string[];
    }) => {
      if (
        input.selection === "demo" ||
        input.selection.startsWith("example:")
      ) {
        if (!demoQuery.data)
          throw new Error("The teaching fixture has not loaded. Try again.");
        const example = input.selection.startsWith("example:")
          ? demoQuery.data.examples?.find(
              (item) => item.id === input.selection.slice(8),
            )
          : undefined;
        if (input.selection.startsWith("example:") && !example)
          throw new Error(
            "This example is no longer available. Choose another example.",
          );
        const response = await api<Analysis["model"]>(
          `/demo/model/analyze?model=${input.model}${example ? `&example=${encodeURIComponent(example.id)}` : ""}`,
          { method: "POST" },
        );
        const targets = new Set(
          example?.focus_edge_ids || [demoQuery.data.focus_edge_id],
        );
        if (
          response.run_id !== demoQuery.data.hybrid_run_id ||
          response.scores?.length !== targets.size ||
          response.scores.some((score) => !targets.has(score.transaction_id))
        )
          throw new Error(
            "The API and this example do not match. Deploy the API and frontend from the same revision, then reload.",
          );
        return demoAnalysis(
          example ? { ...demoQuery.data, ...example } : demoQuery.data,
          response,
        );
      }
      return jsonPost<Analysis>(
        "/analyze",
        input.selection.startsWith("case:")
          ? { case_id: input.selection.slice(5), model: input.model }
          : {
              batch_ids: input.batchIds,
              edge_ids: input.edgeIds,
              model: input.model,
            },
      );
    },
    onSuccess: (response, input) => {
      if (input.key !== keyRef.current) return;
      setResult({
        key: input.key,
        analysis: response,
        execution:
          input.selection === "demo" || input.selection.startsWith("example:")
            ? "Live model run"
            : "Live analysis",
      });
      const first =
        response.model.scores?.find((s) => s.explanation)?.transaction_id ||
        response.findings[0]?.edge_id ||
        null;
      const clicked = requestedEdge.current;
      const scored = response.model.scores?.some(
        (s) => s.transaction_id === clicked,
      );
      setPinned(clicked ? (scored ? clicked : null) : first);
      setSelected(clicked || first);
      setTab(clicked && !scored ? "evidence" : "analysis");
      setContextView(false);
    },
  });
  const isAnalyzing =
    analyze.isPending && analyze.variables?.key === analysisKey;
  const analysisError =
    analyze.isError && analyze.variables?.key === analysisKey
      ? analyze.error.message
      : "";
  const requestedEdge = useRef<string | null>(null);
  function run() {
    requestedEdge.current = null;
    analyze.mutate({
      key: analysisKey,
      selection,
      model,
      batchIds,
      edgeIds: baseGraph.edges.map((e) => e.id),
    });
  }
  function clearSelection() {
    setSelected(null);
    setPinned(null);
    setFocus(null);
    setSearch("");
    setOnlyFlags(false);
    setContextView(false);
    setResult(null);
    setTab("analysis");
    setBefore("");
    setDraftBefore("");
    setAfter("");
    setDraftAfter("");
    analyze.reset();
  }
  function choose(next: string) {
    clearSelection();
    setSelection(next);
    setView("workspace");
  }
  function selectGraphEdge(id: string) {
    requestedEdge.current = id;
    setSelected(id);
    setFocus(null);
    const scored = analysis?.model.scores?.some((s) => s.transaction_id === id);
    setPinned(scored ? id : null);
    setTab(scored ? "analysis" : "evidence");
  }
  function inspect(id: string) {
    setSelected(id);
    setFocus(null);
    setTab("evidence");
  }
  function openUpload() {
    setDocumentError("");
    setDocumentBatchIds([]);
    setView("upload");
  }
  function viewDocumentDetails() {
    setDocumentError("");
    setDocumentBatchIds(batchIds);
    setView("upload");
  }
  function openUploaded() {
    if (upload.jobId) choose(`investigation:${upload.jobId}`);
  }
  useEffect(() => {
    try {
      sessionStorage.setItem("dolos:selection", JSON.stringify(selection));
      sessionStorage.setItem(
        "dolos:investigations",
        JSON.stringify(investigations),
      );
    } catch {
      /* Browsers may disable session storage. */
    }
  }, [selection, investigations]);
  const title =
    mode === "demo"
      ? activeDemo?.title || "The pass-through network"
      : mode === "historical"
        ? currentCase?.title.split(":")[0] || "Historical investigation"
        : investigation?.name || "Uploaded statements";
  const notice =
    mode === "historical"
      ? currentCase?.caveat
      : mode === "demo"
        ? activeDemo?.notice
        : undefined;
  const sourceLabel =
    mode === "demo"
      ? "Synthetic teaching data"
      : mode === "historical"
        ? "Historical reconstruction"
        : `${docs.length} statements`;
  const action =
    mode === "historical"
      ? "Review source evidence"
      : mode === "uploaded" && !trainedAvailable
        ? "Run pattern checks"
        : "Analyze network";
  const loadingGraph =
    mode === "demo"
      ? demoQuery.isPending
      : mode === "historical"
        ? datasetQuery.isPending
        : graphQuery.isPending && batchIds.length > 0;
  const graphError =
    mode === "demo"
      ? demoQuery.error?.message
      : mode === "historical"
        ? datasetQuery.error?.message
        : graphQuery.error?.message;
  async function retryDocument(id: string) {
    setDocumentError("");
    try {
      await api(`/documents/${id}/retry`, { method: "POST" });
      void batches.refetch();
    } catch (e) {
      setDocumentError((e as Error).message);
    }
  }
  async function rowErrors(id: string) {
    try {
      const response = await api<{
        errors: { source_row: number; error: string }[];
      }>(`/documents/${id}/errors`);
      setDocumentError(
        response.errors
          .map((r) => `Row ${r.source_row}: ${r.error}`)
          .join("\n"),
      );
    } catch (e) {
      setDocumentError((e as Error).message);
    }
  }
  function toggleContext() {
    setContextView((v) => !v);
    setOnlyFlags(false);
  }
  function exportVisible() {
    const visible = visibleExport(
      graph,
      analysis,
      flags,
      onlyFlags,
      context,
      pinned,
      contextView,
    );
    const payload = {
      title,
      exported_at: new Date().toISOString(),
      scope: {
        kind: "visible_graph_subset",
        loaded_base_relationships: baseGraph.edges.length,
        exported_relationships: visible.graph.edges.length,
        filters: {
          onlyFlagged: onlyFlags,
          modelContext: contextView,
          entityFocus: focus,
          searchQuery: search,
          focusAndSearchBehavior: "Dim other graph items; do not hide them",
        },
        context_relationships: graph.edges.length - baseGraph.edges.length,
        has_more: !!baseGraph.has_more,
        before: before || null,
        after: after || null,
        batch_ids: batchIds,
      },
      data_type: sourceLabel,
      execution,
      notice,
      graph: visible.graph,
      analysis: visible.analysis,
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "dolos-visible-investigation.json";
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="app-shell">
      <header className="app-header">
        <button
          className="brand-button"
          aria-label="Dolos workspace"
          onClick={() => choose("demo")}
        >
          <Brand />
        </button>
        <label className="investigation-picker">
          <FolderOpen size={16} />
          <select
            aria-label="Choose investigation"
            value={selection}
            onChange={(e) => choose(e.target.value)}
          >
            <optgroup label="Live model examples">
              <option value="demo">Pass-through network</option>
              {demoQuery.data?.examples
                ?.filter((example) => example.id !== "pass-through")
                .map((example) => (
                  <option key={example.id} value={`example:${example.id}`}>
                    {example.title}
                  </option>
                ))}
            </optgroup>
            {!!investigations.length && (
              <optgroup label="Your investigations">
                {investigations.map((i) => (
                  <option key={i.id} value={`investigation:${i.id}`}>
                    {i.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {datasetQuery.isError && (
          <button
            className="text-button"
            onClick={() => datasetQuery.refetch()}
          >
            Reload cases
          </button>
        )}
        <div className="header-navigation">
          <button
            onClick={() =>
              setView(view === "workspace" ? "about" : "workspace")
            }
          >
            {view === "workspace" ? "How it works" : "Back to graph"}
          </button>
        </div>
        <button
          className="button secondary small upload-entry"
          aria-label={
            upload.running ? "View active upload" : "Upload statements"
          }
          onClick={openUpload}
        >
          {upload.running ? (
            <LoaderCircle className="spin" size={15} />
          ) : (
            <Upload size={15} />
          )}
          <span className="upload-entry-label">
            {upload.running ? "View upload" : "Upload statements"}
          </span>
        </button>
      </header>
      <div className="shell-body">
        <main className="main-content">
          {view === "about" ? (
            <div className="about-page">
              <h1>Look beyond a single transaction.</h1>
              <p className="about-lead">
                Dolos connects financial records into a directional graph, then
                makes the analysis inspectable.
              </p>
              <section>
                <NetworkIcon size={27} />
                <div>
                  <h2>GraphSAGE learns from the neighborhood.</h2>
                  <p>
                    The graph neural network encodes historical incoming and
                    outgoing context. Its sender and receiver embeddings feed
                    XGBoost alongside the transaction fields. Select a
                    prediction to inspect its actual sampled history.
                  </p>
                </div>
              </section>
              <section>
                <GitBranch size={27} />
                <div>
                  <h2>XGBoost makes the final decision.</h2>
                  <p>
                    One hybrid pipeline produces one score. Inspect how
                    GraphSAGE-derived features and transaction fields contribute
                    to that score, then follow the actual tree branches. These
                    attributions are not causal evidence or confidence
                    percentages.
                  </p>
                </div>
              </section>
              <section>
                <BookOpen size={27} />
                <div>
                  <h2>Evidence stays attached.</h2>
                  <p>
                    Uploaded records keep source rows and available page
                    evidence. Partial PDF relationships use labeled pattern
                    checks when canonical model fields are missing.
                  </p>
                </div>
              </section>
              <div className="about-demo-note">
                <Info size={19} />
                <p>
                  The bundled graph model demonstration uses generated teaching
                  data and recalculates model predictions on each run. It is not
                  a real investigation or a real-world accuracy benchmark.
                  Compatible hybrid artifacts and inference dependencies must be
                  installed on the API.
                </p>
              </div>
              <button className="button primary" onClick={() => choose("demo")}>
                Explore the graph model
                <ArrowRight size={16} />
              </button>
            </div>
          ) : view === "upload" ? (
            <>
              <UploadPanel
                upload={upload}
                onOpen={openUploaded}
                batches={batches.data || []}
                onRetry={retryDocument}
                onErrors={rowErrors}
              />
              {documentError && (
                <div className="document-error-banner" role="alert">
                  <pre>{documentError}</pre>
                  <button
                    aria-label="Dismiss document details"
                    onClick={() => setDocumentError("")}
                  >
                    <X size={16} />
                  </button>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="workspace-heading">
                <div>
                  <h1>The anti-money laundering & unauthorized transactions model of tomorrow.</h1>
                  <div className="workspace-description">
                    <span
                      className={`dataset-tag ${mode === "demo" ? "synthetic" : ""}`}
                    >
                      {sourceLabel}
                    </span>
                    {mode === "historical" && <span>{currentCase?.hook}</span>}
                    {mode === "demo" && <span>{activeDemo?.description}</span>}
                  </div>
                </div>
                <button
                  aria-label="Export visible relationships"
                  className="button secondary export-button"
                  disabled={!baseGraph.edges.length}
                  onClick={exportVisible}
                >
                  <Download size={15} />
                  <span>Export visible</span>
                </button>
              </div>
              {mode === "historical" && currentCase && (
                <div className="case-provenance">
                  <div>
                    <strong>
                      {currentCase.id === "1mdb"
                        ? "The Hollywood connection"
                        : "Behind the example"}
                    </strong>
                    <p>
                      {currentCase.id === "1mdb"
                        ? "The DOJ complaint alleges that diverted 1MDB funds helped finance The Wolf of Wall Street. The film is a destination of the money—not a depiction of this scheme."
                        : currentCase.summary}
                    </p>
                  </div>
                  <div className="case-source-links">
                    <span>Source records</span>
                    {datasetQuery.data?.sources
                      .filter((source) =>
                        currentCase.source_ids.includes(source.id),
                      )
                      .map((source) => (
                        <a
                          key={source.id}
                          href={source.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {source.publisher} · {source.title} ↗
                        </a>
                      ))}
                  </div>
                </div>
              )}
              <div className="analysis-toolbar">
                <div className="method-control">
                  <span className="method-icon">
                    {mode === "historical" ? (
                      <BookOpen size={17} />
                    ) : (
                      <NetworkIcon size={18} />
                    )}
                  </span>
                  {mode === "historical" ? (
                    <strong>Source evidence review</strong>
                  ) : mode === "uploaded" &&
                    (!hasCanonical || !eligibleModels.length) ? (
                    <strong>Pattern checks</strong>
                  ) : (
                    <strong>GraphSAGE → XGBoost</strong>
                  )}
                  <span className="method-description">
                    {mode === "historical"
                      ? "Citations, not model predictions"
                      : mode === "demo"
                        ? "One hybrid decision · credit for each stage"
                        : trainedAvailable
                          ? "Trained model available"
                          : "For partial or unscored records"}
                  </span>
                </div>
                <div className="analysis-actions">
                  <span
                    className={`availability ${mode === "demo" && !liveDemo ? "recorded" : ""}`}
                  >
                    <i />
                    {mode === "historical"
                      ? "Public sources"
                      : mode === "demo"
                        ? liveDemo
                          ? "Live inference available"
                          : "Live inference not ready"
                        : trainedAvailable
                          ? "Live inference available"
                          : "Pattern analysis available"}
                  </span>
                  <button
                    className="button primary"
                    disabled={
                      isAnalyzing ||
                      loadingGraph ||
                      !baseGraph.edges.length ||
                      processing ||
                      pendingBatches
                    }
                    onClick={() => run()}
                  >
                    {isAnalyzing ? (
                      <LoaderCircle className="spin" size={15} />
                    ) : mode === "historical" ? (
                      <BookOpen size={15} />
                    ) : (
                      <Play size={14} fill="currentColor" />
                    )}
                    {isAnalyzing ? "Analyzing…" : action}
                  </button>
                </div>
              </div>
              {mode === "uploaded" && !trainedAvailable && hasCanonical && (
                <div className="inline-notice">
                  <Info size={15} />
                  <span>
                    Pattern checks are available. Trained ML needs a compatible
                    artifact configured on the API.
                  </span>
                </div>
              )}
              {(analysisError ||
                graphError ||
                (batches.isError && mode === "uploaded")) && (
                <div className="error-message page-error" role="alert">
                  <span>
                    {analysisError || graphError || batches.error?.message}
                  </span>
                  <button
                    className="text-button"
                    onClick={() => {
                      if (analysisError) run();
                      else {
                        void graphQuery.refetch();
                        void demoQuery.refetch();
                        void datasetQuery.refetch();
                        void batches.refetch();
                      }
                    }}
                  >
                    Retry
                  </button>
                </div>
              )}
              {mode === "uploaded" && docs.length > 0 && (
                <div className="processing-strip" role="status">
                  {processing ? (
                    <LoaderCircle size={15} className="spin" />
                  ) : (
                    <Check size={15} />
                  )}
                  <span>
                    {processing
                      ? "Processing statements"
                      : "Processing finished"}{" "}
                    ·{" "}
                    {
                      docs.filter((d) =>
                        ["completed", "needs_review", "failed"].includes(
                          d.status,
                        ),
                      ).length
                    }{" "}
                    of {docs.length} documents ·{" "}
                    {docs.filter((d) => d.status === "failed").length} failed
                  </span>
                  <button onClick={viewDocumentDetails}>
                    Upload & document details
                    <ArrowUpRight size={12} />
                  </button>
                </div>
              )}
              <div className={`desk ${expanded ? "inspector-expanded" : ""}`}>
                <section
                  className="network-canvas"
                  aria-label="Transaction network"
                >
                  <div className="canvas-topbar">
                    <div className="canvas-view-label">
                      <NetworkIcon size={15} />
                      <span>
                        {contextView ? "Model context" : "Transaction network"}
                      </span>
                      {contextView && (
                        <button
                          aria-label="Show full network"
                          onClick={() => setContextView(false)}
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                    <label className="graph-search">
                      <Search size={15} />
                      <input
                        aria-label="Find an entity"
                        placeholder="Find an entity…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                      {search && (
                        <button
                          aria-label="Clear search"
                          onClick={() => setSearch("")}
                        >
                          <X size={13} />
                        </button>
                      )}
                    </label>
                  </div>
                  {loadingGraph ? (
                    <div className="canvas-empty" role="status">
                      <LoaderCircle size={30} className="spin" />
                      <h2>Loading the network</h2>
                    </div>
                  ) : (
                    <Suspense
                      fallback={
                        <div className="canvas-empty" role="status">
                          <LoaderCircle className="spin" size={28} />
                          <p>Preparing the graph…</p>
                        </div>
                      }
                    >
                      <Network
                        graph={graph}
                        flags={flags}
                        selected={selected}
                        pinned={pinned}
                        focus={focus}
                        search={search}
                        onlyFlags={onlyFlags}
                        context={context}
                        contextOnly={contextView}
                        onSelect={selectGraphEdge}
                        onNode={(id) => {
                          setFocus(focus === id ? null : id);
                          setTab("entities");
                        }}
                      />
                    </Suspense>
                  )}
                  {search && (
                    <div className="canvas-search-results">
                      {graph.nodes
                        .filter((n) =>
                          label(n).toLowerCase().includes(search.toLowerCase()),
                        )
                        .slice(0, 8)
                        .map((n) => (
                          <button
                            key={n.id}
                            onClick={() => {
                              setFocus(n.id);
                              setTab("entities");
                            }}
                          >
                            {label(n)}
                            <ArrowUpRight size={12} />
                          </button>
                        ))}
                      {!graph.nodes.some((n) =>
                        label(n).toLowerCase().includes(search.toLowerCase()),
                      ) && (
                        <span>No matching entities. Try an account name.</span>
                      )}
                    </div>
                  )}
                  <div className="canvas-bottom">
                    <div className="graph-legend">
                      <span>
                        <i className="legend-line" />
                        Transfer
                      </span>
                      <span>
                        <i className="legend-line context" />
                        Model context
                      </span>
                      <span>
                        <i className="legend-line flagged" />
                        Flagged for review
                      </span>
                    </div>
                    <span className="canvas-count">
                      {graph.nodes.length} entities · {graph.edges.length}{" "}
                      transfers
                    </span>
                  </div>
                  {focus && (
                    <button
                      className="clear-focus"
                      onClick={() => setFocus(null)}
                    >
                      Clear entity focus
                      <X size={12} />
                    </button>
                  )}
                </section>
                <Inspector
                  graph={graph}
                  analysis={analysis}
                  selected={selected}
                  pinned={pinned}
                  tab={tab}
                  onTab={setTab}
                  onInspect={inspect}
                  onPin={(id) => {
                    setPinned(id);
                    setContextView(false);
                  }}
                  onFocus={(id) => setFocus(focus === id ? null : id)}
                  focus={focus}
                  sources={datasetQuery.data?.sources || []}
                  pending={isAnalyzing}
                  mode={mode}
                  notice={notice}
                  execution={execution}
                  contextView={contextView}
                  onContextView={toggleContext}
                  expanded={expanded}
                  onExpand={() => setExpanded((v) => !v)}
                  demo={mode === "demo" ? activeDemo : undefined}
                />
              </div>
              <div className="graph-scopebar">
                <div>
                  <ShieldCheck size={14} />
                  <span>
                    {mode === "demo"
                      ? `Teaching fixture · ${demoQuery.data?.source_rows.toLocaleString() || "—"} source rows · ${baseGraph.edges.length} relationships in this local example`
                      : mode === "historical"
                        ? "Reconstructed from public sources. Relationships are not original bank records."
                        : baseGraph.has_more
                          ? "Preview limited to 2,000 relationships. Narrow the scope to investigate more."
                          : `${baseGraph.edges.length} relationships in this investigation view.`}
                  </span>
                </div>
                <div className="scope-actions">
                  <button
                    className={onlyFlags ? "active" : ""}
                    disabled={!analysis}
                    onClick={() => {
                      setOnlyFlags((v) => !v);
                      setContextView(false);
                    }}
                  >
                    <SlidersHorizontal size={13} />
                    {onlyFlags ? "Show all" : "Flagged only"}
                  </button>
                  {mode === "uploaded" && (
                    <details className="scope-filter">
                      <summary>
                        <SlidersHorizontal size={13} />
                        Date scope
                      </summary>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          setAfter(
                            draftAfter
                              ? new Date(draftAfter + "Z").toISOString()
                              : "",
                          );
                          setBefore(
                            draftBefore
                              ? new Date(draftBefore + "Z").toISOString()
                              : "",
                          );
                          setResult(null);
                        }}
                      >
                        <label>
                          Records from (UTC)
                          <input
                            type="datetime-local"
                            value={draftAfter}
                            onChange={(e) => setDraftAfter(e.target.value)}
                          />
                        </label>
                        <label>
                          Only records before (UTC)
                          <input
                            type="datetime-local"
                            value={draftBefore}
                            onChange={(e) => setDraftBefore(e.target.value)}
                          />
                        </label>
                        <button className="button primary small" type="submit">
                          Apply scope
                        </button>
                        <button
                          className="text-button"
                          type="button"
                          onClick={() => {
                            setDraftBefore("");
                            setBefore("");
                            setDraftAfter("");
                            setAfter("");
                          }}
                        >
                          Reset
                        </button>
                      </form>
                    </details>
                  )}
                </div>
              </div>
              <Ledger
                graph={baseGraph}
                flags={flags}
                search={search}
                onlyFlags={onlyFlags}
                focus={focus}
                selected={selected}
                onSelect={inspect}
                scored={
                  new Set(
                    analysis?.model.scores?.map((s) => s.transaction_id) || [],
                  )
                }
                analyzed={!!analysis}
                modelApplied={analysis?.model.status === "completed"}
                onClear={() => {
                  setSearch("");
                  setOnlyFlags(false);
                  setFocus(null);
                }}
              />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
