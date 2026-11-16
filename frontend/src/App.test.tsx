import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import App from "./App";
describe("graph-first entry", () => {
  it("opens the compatible synthetic network with live model actions", () => {
    const dataset = JSON.parse(
      readFileSync(new URL("../public/cases.json", import.meta.url), "utf8"),
    );
    const demo = JSON.parse(
      readFileSync(
        new URL("../public/model-demo.json", import.meta.url),
        "utf8",
      ),
    );
    const client = new QueryClient();
    client.setQueryData(["cases"], dataset);
    client.setQueryData(["model-demo"], demo);
    const html = renderToString(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    expect(html).toContain("The pass-through network");
    expect(html).toContain("Synthetic teaching data");
    expect(html).toContain("Analyze network");
    expect(html).not.toContain("View recorded run");
    expect(html).not.toContain('value="case:1mdb"');
    expect(html).toContain("Export visible");
    expect(html).toContain("Transaction ledger");
    expect(html).not.toContain("99%");
  });
});
