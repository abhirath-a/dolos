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

function renderDemo() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["cases"], cases);
  client.setQueryData(["model-demo"], demo);
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
it("keeps every historical case reachable through the grouped picker", () => {
  renderDemo();
  const picker = screen.getByRole("combobox", { name: "Choose investigation" });
  for (const item of cases.cases)
    expect(
      screen.getByRole("option", {
        name: new RegExp(item.title.split(":")[0]),
      }),
    ).toBeTruthy();
  expect(
    screen.getAllByRole("button", { name: "Upload statements" }),
  ).toHaveLength(1);
  fireEvent.change(picker, { target: { value: "case:1mdb" } });
  expect(
    screen.getByRole("button", { name: "Review source evidence" }),
  ).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Analysis model" })).toBeNull();
  fireEvent.change(picker, { target: { value: "demo" } });
  expect(
    (
      screen.getByRole("combobox", {
        name: "Analysis model",
      }) as HTMLSelectElement
    ).value,
  ).toBe("graphsage");
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
