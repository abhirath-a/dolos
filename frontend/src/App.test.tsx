import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import App from "./App";
describe("graph-first entry", () => {
  it("opens the sourced 1MDB case with its Hollywood connection", () => {
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
    expect(html).toContain("<h1>1MDB</h1>");
    expect(html).toContain("The Hollywood connection");
    expect(html).toContain("Review source evidence");
    expect(html).toContain("U.S. Department of Justice");
    expect(html).toContain("Export visible");
    expect(html).toContain("Transaction ledger");
    expect(html).not.toContain("99%");
  });
});
