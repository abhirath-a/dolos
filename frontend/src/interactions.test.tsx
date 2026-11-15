// @vitest-environment jsdom
import { useState } from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import demoData from "../public/model-demo.json";
import Ledger from "./Ledger";
import Inspector, { type InspectorTab } from "./Inspector";
import { demoAnalysis } from "./domain";
import type { ModelDemo, Graph } from "./types";
const demo = demoData as ModelDemo;
afterEach(cleanup);
describe("investigation interactions", () => {
  it("paginates to records beyond the old 100-row limit", () => {
    const graph: Graph = {
      nodes: [
        { id: "a", label: "Origin" },
        { id: "b", label: "Destination" },
      ],
      edges: Array.from({ length: 115 }, (_, i) => ({
        id: `row-${i}`,
        source: "a",
        target: "b",
        relation: "transfer",
        amount_paid: String(i),
        payment_currency: "USD",
      })),
    };
    const selected = vi.fn();
    render(
      <Ledger
        graph={graph}
        flags={new Set()}
        search=""
        onlyFlags={false}
        focus={null}
        selected={null}
        onSelect={selected}
        onClear={() => {}}
      />,
    );
    for (let i = 0; i < 10; i++)
      fireEvent.click(screen.getByRole("button", { name: "Next ledger page" }));
    expect(screen.getByText("101–110 of 115 relationships")).toBeTruthy();
    fireEvent.click(
      screen.getAllByRole("button", {
        name: "View transfer from Origin to Destination",
      })[0],
    );
    expect(selected).toHaveBeenCalledWith("row-100");
  });
  it("keeps the originating prediction pinned while inspecting sampled neighbors", () => {
    const analysis = demoAnalysis(demo, demo.models.graphsage);
    const pin = demo.focus_edge_id;
    const context =
      analysis.model.scores![0].explanation!.context_transactions![0].id;
    const inspect = vi.fn(),
      onPin = vi.fn();
    render(
      <Inspector
        graph={demo.graph}
        analysis={analysis}
        selected={context}
        pinned={pin}
        tab="analysis"
        onTab={() => {}}
        onInspect={inspect}
        onPin={onPin}
        onFocus={() => {}}
        focus={null}
        sources={[]}
        pending={false}
        mode="demo"
        execution="Recorded model run"
        contextView={false}
        onContextView={() => {}}
        expanded={false}
        onExpand={() => {}}
        demo={demo}
      />,
    );
    expect(screen.getByText("Prediction being explained")).toBeTruthy();
    expect(screen.getByText("Return to this prediction")).toBeTruthy();
    fireEvent.click(screen.getByText("Return to this prediction"));
    expect(inspect).toHaveBeenCalledWith(pin);
    expect(onPin).not.toHaveBeenCalled();
  });
  it("supports arrow-key tab navigation with associated tabpanels", () => {
    function Harness() {
      const [tab, setTab] = useState<InspectorTab>("analysis");
      return (
        <Inspector
          graph={demo.graph}
          analysis={null}
          selected={null}
          pinned={null}
          tab={tab}
          onTab={setTab}
          onInspect={() => {}}
          onPin={() => {}}
          onFocus={() => {}}
          focus={null}
          sources={[]}
          pending={false}
          mode="demo"
          contextView={false}
          onContextView={() => {}}
          expanded={false}
          onExpand={() => {}}
        />
      );
    }
    render(<Harness />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Analysis" }), {
      key: "ArrowRight",
    });
    const tab = screen.getByRole("tab", { name: "Evidence" });
    expect(tab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(
      tab.id,
    );
  });
  it("does not label historical source review as live model inference", () => {
    render(
      <Inspector
        graph={demo.graph}
        analysis={null}
        selected={null}
        pinned={null}
        tab="analysis"
        onTab={() => {}}
        onInspect={() => {}}
        onPin={() => {}}
        onFocus={() => {}}
        focus={null}
        sources={[]}
        pending={false}
        mode="historical"
        contextView={false}
        onContextView={() => {}}
        expanded={false}
        onExpand={() => {}}
      />,
    );
    expect(screen.getByText(/does not run an ML model/)).toBeTruthy();
  });
  it("distinguishes unscored context from a negative model prediction", () => {
    render(
      <Ledger
        graph={demo.graph}
        flags={new Set([demo.focus_edge_id])}
        scored={new Set([demo.focus_edge_id])}
        analyzed
        modelApplied
        search=""
        onlyFlags={false}
        focus={null}
        selected={null}
        onSelect={() => {}}
        onClear={() => {}}
      />,
    );
    expect(screen.getAllByText("Context · not scored").length).toBeGreaterThan(
      0,
    );
  });
});
