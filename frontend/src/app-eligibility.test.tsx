// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  cleanup,
  fireEvent,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import demo from "../public/model-demo.json";
import cases from "../public/cases.json";
import type { ModelDemo } from "./types";
vi.mock("./Network", () => ({ default: () => <div>Test graph surface</div> }));
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});
describe("model eligibility", () => {
  it("selects available Graph XGBoost when GraphSAGE is not configured", async () => {
    const investigation = {
      id: "test",
      name: "Test investigation",
      batchIds: ["b1"],
      createdAt: "2026-01-01",
    };
    sessionStorage.setItem(
      "dolos:selection",
      JSON.stringify("investigation:test"),
    );
    sessionStorage.setItem(
      "dolos:investigations",
      JSON.stringify([investigation]),
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
    client.setQueryData(["cases"], { cases: [], sources: [] });
    client.setQueryData(["model-demo"], demo);
    client.setQueryData(["health"], {
      model_configured: true,
      available_models: ["graph_xgboost"],
    });
    client.setQueryData(["demo-status"], { live_available: false });
    client.setQueryData(["batches", ["b1"]], batches);
    client.setQueryData(["graph", ["b1"], "", "", "d1:completed"], graph);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (input: string) =>
          new Response(
            JSON.stringify(
              input.includes("/graph")
                ? graph
                : input.includes("/batches/")
                  ? batches[0]
                  : { status: "ok", available_models: ["graph_xgboost"] },
            ),
          ),
      ),
    );
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(
        (
          screen.getByRole("combobox", {
            name: "Analysis model",
          }) as HTMLSelectElement
        ).value,
      ).toBe("graph_xgboost"),
    );
    expect(
      (screen.getByRole("option", { name: "GraphSAGE" }) as HTMLOptionElement)
        .disabled,
    ).toBe(true);
    expect(
      screen.getByRole("button", { name: "Run Graph XGBoost" }),
    ).toBeTruthy();
  });
});

function renderDemo(data: ModelDemo = demo) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["cases"], cases);
  client.setQueryData(["model-demo"], data);
  client.setQueryData(["health"], {
    model_configured: false,
    available_models: [],
  });
  client.setQueryData(["demo-status"], { live_available: false });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}
it("excludes historical reconstructions from model examples and clears stale selections", () => {
  sessionStorage.setItem("dolos:selection", JSON.stringify("case:1mdb"));
  renderDemo();
  const picker = screen.getByRole("combobox", { name: "Choose investigation" });
  for (const item of cases.cases)
    expect(
      screen.queryByRole("option", {
        name: new RegExp(item.title.split(":")[0]),
      }),
    ).toBeNull();
  expect((picker as HTMLSelectElement).value).toBe("demo");
  expect(
    screen.getAllByRole("button", { name: "Upload statements" }),
  ).toHaveLength(1);
  expect(
    (
      screen.getByRole("combobox", {
        name: "Analysis model",
      }) as HTMLSelectElement
    ).value,
  ).toBe("graphsage");
  expect(screen.getByRole("option", { name: "Graph XGBoost" })).toBeTruthy();
});

it("requests fresh inference on every example run, including both models", async () => {
  const fetch = vi.fn(async (input: string) => {
    const model = input.includes("graph_xgboost")
      ? "graph_xgboost"
      : "graphsage";
    return new Response(JSON.stringify(demo.models[model]));
  });
  vi.stubGlobal("fetch", fetch);
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Run GraphSAGE" }));
  await screen.findByText("GraphSAGE · Live model run");
  expect(fetch).toHaveBeenCalledWith(
    "/api/demo/model/analyze?model=graphsage",
    expect.objectContaining({ method: "POST" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Run GraphSAGE" }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.filter(([url]) =>
        url.includes("/demo/model/analyze?model=graphsage"),
      ),
    ).toHaveLength(2),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("combobox", {
          name: "Analysis model",
        }) as HTMLSelectElement
      ).disabled,
    ).toBe(false),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Analysis model" }), {
    target: { value: "graph_xgboost" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run Graph XGBoost" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/demo/model/analyze?model=graph_xgboost",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

it("shows live inference errors without substituting stored scores", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: "Model artifacts are not installed." }),
          { status: 503 },
        ),
    ),
  );
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Run GraphSAGE" }));
  await screen.findByText("Model artifacts are not installed.");
  expect(screen.queryByRole("button", { name: /recorded/i })).toBeNull();
  expect(screen.queryByText("Raw model margin")).toBeNull();
});

const judgeDemo: ModelDemo = {
  ...demo,
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
      description: "A comparison of generated ordinary payments.",
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

it("offers all judge examples without duplicating the default and clears results on switch", async () => {
  const fetch = vi.fn(
    async () => new Response(JSON.stringify(demo.models.graphsage)),
  );
  vi.stubGlobal("fetch", fetch);
  renderDemo(judgeDemo);
  const picker = screen.getByRole("combobox", { name: "Choose investigation" });
  expect(
    screen
      .getByRole("group", { name: "Live model examples" })
      .querySelectorAll("option"),
  ).toHaveLength(4);
  for (const item of judgeDemo.examples!.filter(
    (entry) => entry.id !== "pass-through",
  ))
    expect(screen.getByRole("option", { name: item.title })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Run GraphSAGE" }));
  await screen.findByText("GraphSAGE · Live model run");
  fireEvent.change(picker, {
    target: { value: "example:incoming-collection" },
  });
  expect(
    screen.getByRole("heading", { name: "Incoming collection" }),
  ).toBeTruthy();
  expect(
    screen.getByText("Two simulated senders transfer into one collector."),
  ).toBeTruthy();
  expect(screen.queryByText("GraphSAGE · Live model run")).toBeNull();
  expect(screen.queryByText("Raw model margin")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Run GraphSAGE" }));
  await screen.findByText("GraphSAGE · Live model run");
  expect(fetch).toHaveBeenCalledWith(
    "/api/demo/model/analyze?model=graphsage&example=incoming-collection",
    expect.objectContaining({ method: "POST" }),
  );
  fireEvent.change(picker, { target: { value: "example:ordinary-payments" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Analysis model" }), {
    target: { value: "graph_xgboost" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run Graph XGBoost" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/demo/model/analyze?model=graph_xgboost&example=ordinary-payments",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

it("restores a valid saved example and falls back for a removed example", async () => {
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
  expect(
    screen.getByRole("heading", { name: "The pass-through network" }),
  ).toBeTruthy();
});
it("rejects live scores for a different example instead of displaying them", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            ...demo.models.graphsage,
            scores: demo.models.graphsage.scores.map((score) => ({
              ...score,
              transaction_id: "another-example:1",
            })),
          }),
        ),
    ),
  );
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Run GraphSAGE" }));
  await screen.findByText(
    "The API and this example do not match. Deploy the API and frontend from the same revision, then reload.",
  );
  expect(screen.queryByText("GraphSAGE · Live model run")).toBeNull();
});
it("preserves selected documents when navigating away and reopening uploads", () => {
  renderDemo();
  fireEvent.click(screen.getByRole("button", { name: "Upload statements" }));
  const file = new File(["statement"], "selected-statement.pdf", {
    type: "application/pdf",
  });
  fireEvent.change(screen.getByLabelText("Choose statement files"), {
    target: { files: [file] },
  });
  expect(screen.getByText("selected-statement.pdf")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Back to graph" }));
  fireEvent.click(screen.getByRole("button", { name: "Upload statements" }));
  expect(screen.getByText("selected-statement.pdf")).toBeTruthy();
});
