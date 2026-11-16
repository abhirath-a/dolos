// @vitest-environment jsdom
import { it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import source from "../public/model-demo.json";
import cases from "../public/cases.json";
import type { ModelDemo, Analysis } from "./types";
vi.mock("./Network", () => ({ default: () => <div>Test graph surface</div> }));
const demo = source as ModelDemo;
const hybrid: Analysis["model"] = demo.models.hybrid;
const fixture = demo;
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});
function renderDemo(data: ModelDemo = fixture) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["cases"], cases);
  client.setQueryData(["model-demo"], data);
  client.setQueryData(["health"], {
    model_configured: false,
    available_models: [],
  });
  client.setQueryData(["demo-status"], { live_available: true });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}
it("offers one combined pipeline without a model selector or archived historical cases", () => {
  sessionStorage.setItem("dolos:selection", JSON.stringify("case:1mdb"));
  renderDemo();
  expect(
    (
      screen.getByRole("combobox", {
        name: "Choose investigation",
      }) as HTMLSelectElement
    ).value,
  ).toBe("demo");
  expect(screen.queryByRole("combobox", { name: "Analysis model" })).toBeNull();
  expect(screen.getByText("GraphSAGE → XGBoost")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Analyze network" })).toBeTruthy();
  for (const item of cases.cases)
    expect(
      screen.queryByRole("option", {
        name: new RegExp(item.title.split(":")[0]),
      }),
    ).toBeNull();
});
it("recalculates the one hybrid on every run and exposes both sources of credit", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(hybrid)));
  vi.stubGlobal("fetch", fetch);
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await screen.findByText("Dolos hybrid · Live model run");
  expect(fetch).toHaveBeenCalledWith(
    "/api/demo/model/analyze?model=hybrid",
    expect.objectContaining({ method: "POST" }),
  );
  expect(
    screen.getByRole("heading", { name: "Credit for this finding" }),
  ).toBeTruthy();
  expect(screen.getByText("GraphSAGE neighborhood")).toBeTruthy();
  expect(screen.getByText("Transaction fields")).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "The neighborhood behind the score" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "What moved the decision" }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await waitFor(() => expect(fetch.mock.calls).toHaveLength(2));
});
it("shows live errors without substituting stored scores", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: "Hybrid artifacts are not installed." }),
          { status: 503 },
        ),
    ),
  );
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await screen.findByText("Hybrid artifacts are not installed.");
  expect(screen.queryByRole("button", { name: /recorded/i })).toBeNull();
  expect(screen.queryByText("Final decision margin")).toBeNull();
});
const judgeDemo: ModelDemo = {
  ...fixture,
  examples: [
    {
      id: "pass-through",
      title: "The pass-through network",
      description: "Default routing example.",
      focus_edge_id: demo.focus_edge_id,
      focus_edge_ids: [demo.focus_edge_id],
      graph: demo.graph,
    },
    {
      id: "incoming-collection",
      title: "Incoming collection",
      description: "Two simulated senders transfer into one collector.",
      focus_edge_id: demo.focus_edge_id,
      focus_edge_ids: [demo.focus_edge_id],
      graph: demo.graph,
    },
    {
      id: "ordinary-payments",
      title: "Ordinary payments",
      description: "Generated ordinary payment comparison.",
      focus_edge_id: demo.focus_edge_id,
      focus_edge_ids: [demo.focus_edge_id],
      graph: demo.graph,
    },
    {
      id: "mixed-network",
      title: "Mixed network",
      description: "Generated routing and ordinary transfers together.",
      focus_edge_id: demo.focus_edge_id,
      focus_edge_ids: [demo.focus_edge_id],
      graph: demo.graph,
    },
  ],
};
it("offers four examples, clears findings on change and runs the selected example through the hybrid", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(hybrid)));
  vi.stubGlobal("fetch", fetch);
  renderDemo(judgeDemo);
  const picker = screen.getByRole("combobox", { name: "Choose investigation" });
  expect(
    screen
      .getByRole("group", { name: "Live model examples" })
      .querySelectorAll("option"),
  ).toHaveLength(4);
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await screen.findByText("Dolos hybrid · Live model run");
  fireEvent.change(picker, {
    target: { value: "example:incoming-collection" },
  });
  expect(
    screen.getByRole("heading", { name: "Incoming collection" }),
  ).toBeTruthy();
  expect(screen.queryByText("Dolos hybrid · Live model run")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await screen.findByText("Dolos hybrid · Live model run");
  expect(fetch).toHaveBeenCalledWith(
    "/api/demo/model/analyze?model=hybrid&example=incoming-collection",
    expect.objectContaining({ method: "POST" }),
  );
});
it("restores a saved example and falls back for a removed one", async () => {
  sessionStorage.setItem(
    "dolos:selection",
    JSON.stringify("example:mixed-network"),
  );
  renderDemo(judgeDemo);
  expect(screen.getByRole("heading", { name: "Mixed network" })).toBeTruthy();
  cleanup();
  sessionStorage.setItem("dolos:selection", JSON.stringify("example:retired"));
  renderDemo(judgeDemo);
  await waitFor(() =>
    expect(
      (
        screen.getByRole("combobox", {
          name: "Choose investigation",
        }) as HTMLSelectElement
      ).value,
    ).toBe("demo"),
  );
});
it("rejects scores for a different example", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            ...hybrid,
            scores: hybrid.scores!.map((score) => ({
              ...score,
              transaction_id: "another-example:1",
            })),
          }),
        ),
    ),
  );
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await screen.findByText(
    "The API and this example do not match. Deploy the API and frontend from the same revision, then reload.",
  );
  expect(screen.queryByText("Dolos hybrid · Live model run")).toBeNull();
});
it("preserves selected documents when navigating away and reopening uploads", () => {
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Upload statements" }));
  fireEvent.change(screen.getByLabelText("Choose statement files"), {
    target: {
      files: [
        new File(["statement"], "selected-statement.pdf", {
          type: "application/pdf",
        }),
      ],
    },
  });
  expect(screen.getByText("selected-statement.pdf")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back to graph" }));
  fireEvent.click(screen.getByRole("button", { name: "Upload statements" }));
  expect(screen.getByText("selected-statement.pdf")).toBeTruthy();
});

it("uses the hybrid for compatible uploaded statements without offering standalone models", async () => {
  sessionStorage.setItem(
    "dolos:selection",
    JSON.stringify("investigation:test"),
  );
  sessionStorage.setItem(
    "dolos:investigations",
    JSON.stringify([
      {
        id: "test",
        name: "Test investigation",
        batchIds: ["b1"],
        createdAt: "2026-01-01",
      },
    ]),
  );
  const batches = [
    {
      id: "b1",
      status: "completed",
      documents: [
        {
          id: "d1",
          filename: "sample.csv",
          status: "completed",
          accepted: 1,
          rejected: 0,
        },
      ],
    },
  ];
  const graph = {
    nodes: [
      { id: "a", label: "Origin" },
      { id: "b", label: "Destination" },
    ],
    edges: [
      {
        id: "e1",
        source: "a",
        target: "b",
        relation: "transfer",
        amount_paid: "100",
        payment_currency: "USD",
        timestamp: "2026-01-01T00:00:00Z",
      },
    ],
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["cases"], cases);
  client.setQueryData(["model-demo"], fixture);
  client.setQueryData(["health"], {
    model_configured: true,
    available_models: ["hybrid"],
  });
  client.setQueryData(["demo-status"], { live_available: true });
  client.setQueryData(["batches", ["b1"]], batches);
  client.setQueryData(["graph", ["b1"], "", "", "d1:completed"], graph);
  const fetch = vi.fn(async (_input: string, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body)).model).toBe("hybrid");
    return new Response(
      JSON.stringify({
        model: { status: "completed", name: "hybrid", scores: [] },
        findings: [],
        entities: [],
        scope: { relationships: 1, model_eligible: 1, historical: false },
        method: "trained_model",
        summary: "No flags",
        limitation: "Teaching fixture",
      }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  expect(screen.queryByRole("combobox", { name: "Analysis model" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/analyze",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

it("shows per-finding credit even when detailed context and paths are not returned", async () => {
  const first = hybrid.scores![0];
  const details = first.explanation!;
  const limited = {
    ...hybrid,
    scores: [
      {
        ...first,
        explanation: undefined,
        base_margin: details.base_margin,
        contribution_sum: details.contribution_sum,
        component_contributions: details.component_contributions,
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(limited))),
  );
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Analyze network" }));
  await screen.findByRole("heading", { name: "Credit for this finding" });
  expect(screen.getByText("GraphSAGE neighborhood")).toBeTruthy();
  expect(screen.getByText("Transaction fields")).toBeTruthy();
  expect(
    screen.getByText(
      "Component credits are available for every score; detailed context and tree paths are included for the ten highest-ranked transfers.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByRole("heading", {
      name: "The neighborhood behind the score",
    }),
  ).toBeNull();
  expect(
    screen.queryByRole("heading", { name: "What moved the decision" }),
  ).toBeNull();
});
