import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ArrowUpRight,
  TriangleAlert,
  Search,
} from "lucide-react";
import { label, money, type Graph } from "./types";
import { filterEdges, pageRange, dateLabel } from "./domain";
export default function Ledger({
  graph,
  flags,
  search,
  onlyFlags,
  focus,
  selected,
  onSelect,
  onClear,
  scored = new Set<string>(),
  analyzed = false,
  modelApplied = false,
}: {
  graph: Graph;
  flags: Set<string>;
  search: string;
  onlyFlags: boolean;
  focus: string | null;
  selected: string | null;
  onSelect: (id: string) => void;
  onClear: () => void;
  scored?: Set<string>;
  analyzed?: boolean;
  modelApplied?: boolean;
}) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [graph, search, onlyFlags, focus]);
  const filtered = filterEdges(graph, search, flags, onlyFlags, focus),
    range = pageRange(filtered, page, 10);
  return (
    <section className="ledger">
      <div className="section-title">
        <div>
          <h2>Transaction ledger</h2>
          <p>Every relationship, with a record behind it.</p>
        </div>
        <span className="quiet-count">
          {filtered.length}{" "}
          {search || focus || onlyFlags ? "matching" : "visible"} connections
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>From / to</th>
              <th>Amount / relationship</th>
              <th>Date</th>
              <th>Review status</th>
              <th>
                <span className="sr-only">Inspect</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {range.items.map((edge) => (
              <tr
                key={edge.id}
                className={selected === edge.id ? "selected" : ""}
              >
                <td>
                  <button
                    className="ledger-route"
                    aria-label={`View transfer from ${label(graph.nodes.find((n) => n.id === edge.source))} to ${label(graph.nodes.find((n) => n.id === edge.target))}`}
                    onClick={() => onSelect(edge.id)}
                  >
                    <strong>
                      {label(graph.nodes.find((n) => n.id === edge.source))}
                    </strong>
                    <span>
                      <ChevronRight size={13} />
                      {label(graph.nodes.find((n) => n.id === edge.target))}
                    </span>
                  </button>
                </td>
                <td>
                  <span className="amount">{money(edge)}</span>
                  <small>
                    {edge.document_id === "synthetic-transfers.csv"
                      ? "Synthetic source record"
                      : edge.document_id
                        ? `Source row ${edge.source_row || "—"}`
                        : "Public case source"}
                  </small>
                </td>
                <td>
                  {dateLabel(
                    edge.timestamp || edge.event_date,
                    edge.timestamp_precision,
                    edge.date_qualifier,
                  )}
                </td>
                <td>
                  {flags.has(edge.id) ? (
                    <span className="status-tag review">
                      <TriangleAlert size={12} />
                      Review
                    </span>
                  ) : (
                    <span className="table-neutral">
                      {!analyzed
                        ? "Not analyzed"
                        : modelApplied && !scored.has(edge.id)
                          ? "Context · not scored"
                          : "Not flagged by this method"}
                    </span>
                  )}
                </td>
                <td>
                  <button
                    className="icon-button"
                    aria-label={`Inspect transfer from ${label(graph.nodes.find((n) => n.id === edge.source))} to ${label(graph.nodes.find((n) => n.id === edge.target))}`}
                    onClick={() => onSelect(edge.id)}
                  >
                    <ArrowUpRight size={17} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!filtered.length && (
        <div className="ledger-empty">
          <Search size={22} />
          <strong>No matching relationships</strong>
          <p>Try another entity or clear your filters.</p>
          <button className="text-button" onClick={onClear}>
            Clear filters
          </button>
        </div>
      )}
      <div className="pagination">
        <span>
          {range.start}–{range.end} of {filtered.length} relationships
          {graph.has_more ? " · preview limited to 2,000" : ""}
        </span>
        <div>
          <button
            aria-label="Previous ledger page"
            disabled={range.page === 0}
            onClick={() => setPage(range.page - 1)}
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            Page {range.page + 1} of {range.max + 1}
          </span>
          <button
            aria-label="Next ledger page"
            disabled={range.page >= range.max}
            onClick={() => setPage(range.page + 1)}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </section>
  );
}
