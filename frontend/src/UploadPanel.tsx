import { useRef, useState } from "react";
import {
  Upload,
  FileText,
  ArrowRight,
  Trash2,
  X,
  LoaderCircle,
  Check,
  FolderUp,
  RefreshCw,
} from "lucide-react";
import { partitionFiles } from "./api";
import { useUpload } from "./useUpload";
import type { Batch } from "./types";
import { human } from "./types";
type UploadState = ReturnType<typeof useUpload>;
export default function UploadPanel({
  upload,
  onOpen,
  batches,
  onRetry,
  onErrors,
}: {
  upload: UploadState;
  onOpen: () => void;
  batches: Batch[];
  onRetry: (id: string) => void;
  onErrors: (id: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const total = upload.files.reduce((n, f) => n + f.size, 0);
  const groups = upload.files.length ? partitionFiles(upload.files).length : 0;
  const finished = groups > 0 && upload.completed === groups;
  const accepted = batches.flatMap((b) => b.documents);
  return (
    <div className="ingest-page">
      <div className="ingest-heading">
        <div>
          <h1>Upload statements</h1>
        </div>
      </div>
      <div className="ingest-columns">
        <section className="ingest-main">
          <button
            className={`drop-zone ${drag ? "dragging" : ""}`}
            disabled={upload.running || upload.completed > 0}
            onClick={() => input.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              upload.add(Array.from(e.dataTransfer.files));
            }}
          >
            <span className="upload-emblem">
              <FolderUp size={32} strokeWidth={1.4} />
            </span>
            <strong>Drop files here</strong>
            <span>or browse files</span>
            <small>PDF, CSV, or JSON · 25 MiB per file</small>
          </button>
          {upload.files.length === 0 && (
            <p className="sample-upload">
              <a href="/dolos-video-statements.zip" download>Download sample PDFs</a>
              <span> Unzip the archive, then upload the four synthetic statements.</span>
            </p>
          )}
          <input
            ref={input}
            className="sr-only"
            type="file"
            multiple
            accept=".pdf,.csv,.json"
            aria-label="Choose statement files"
            onChange={(e) => {
              upload.add(Array.from(e.target.files || []));
              e.target.value = "";
            }}
          />
          {upload.files.length > 0 && (
            <>
              <div className="files-summary">
                <strong>{upload.files.length} documents</strong>
                <span>
                  {(total / 1024 / 1024).toFixed(1)} MiB · {groups} batches
                </span>
                {!upload.running && (
                  <button className="text-button" onClick={upload.reset}>
                    {upload.completed ? "New investigation" : "Clear selection"}
                  </button>
                )}
              </div>
              <div className="selected-files">
                {upload.files.map((file, index) => (
                  <div key={`${file.name}-${index}`}>
                    <FileText size={17} />
                    <span>
                      {file.name}
                      <small>{(file.size / 1024).toFixed(0)} KiB</small>
                    </span>
                    <button
                      className="icon-button"
                      disabled={upload.running || upload.completed > 0}
                      aria-label={`Remove ${file.name}`}
                      onClick={() => upload.remove(index)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
          {upload.error && (
            <div className="error-message" role="alert">
              {upload.error}
            </div>
          )}
          {(upload.running || upload.status) && (
            <div className="upload-status" role="status">
              <div>
                {upload.running ? (
                  <LoaderCircle className="spin" size={17} />
                ) : finished ? (
                  <Check size={17} />
                ) : (
                  <X size={17} />
                )}
                <strong>{upload.status}</strong>
              </div>
              <progress
                aria-label="Upload transfer progress"
                max={Math.max(total, 1)}
                value={Math.min(upload.transferred, total)}
              />
              <span>
                {upload.completed} of {groups} batches accepted ·{" "}
                {Math.min(100, Math.round((upload.transferred / Math.max(total, 1)) * 100))}%
                transferred
              </span>
            </div>
          )}
          <div className="upload-actions">
            {upload.running ? (
              <button className="button secondary" onClick={upload.cancel}>
                <X size={15} />
                Cancel remaining uploads
              </button>
            ) : (
              <button
                className="button primary"
                disabled={!upload.files.length || finished}
                onClick={upload.start}
              >
                <Upload size={16} />
                {upload.completed ? "Resume remaining batches" : "Upload & build graph"}
              </button>
            )}
            {upload.completed > 0 && (
              <button className="button secondary" onClick={onOpen}>
                Open investigation
                <ArrowRight size={15} />
              </button>
            )}
          </div>
          <p className="upload-note">
            Uploads continue while you use the workspace. Cancelling keeps batches you already uploaded.
          </p>
          {accepted.length > 0 && (
            <section className="document-list">
              <h2>Documents</h2>
              {accepted.map((d) => (
                <div key={d.id}>
                  <FileText size={17} />
                  <div>
                    <strong>{d.filename}</strong>
                    <span>
                      {d.accepted} accepted relationships · {d.rejected} rejected rows
                    </span>
                    {d.error && <p className="document-error">{d.error}</p>}
                    <div className="document-actions">
                      {d.status === "failed" && (
                        <button onClick={() => onRetry(d.id)}>
                          <RefreshCw size={12} />
                          Retry processing
                        </button>
                      )}
                      {d.rejected > 0 && (
                        <button onClick={() => onErrors(d.id)}>Review rejected rows</button>
                      )}
                    </div>
                  </div>
                  <span className={`status-tag ${d.status === "failed" ? "review" : ""}`}>
                    {human(d.status)}
                  </span>
                </div>
              ))}
            </section>
          )}
        </section>
        <div className="upload-help">
          <details>
            <summary>What happens to my documents?</summary>
            <p>
              Uploaded files stay in your configured storage. PDFs without an embedded export may
              be sent to the configured extraction provider.
            </p>
          </details>
          <details>
            <summary>CSV and JSON format</summary>
            <p>
              CSV requires timestamp, from_bank, from_account, to_bank, to_account, amount_received,
              receiving_currency, amount_paid, payment_currency, payment_format. JSON accepts an
              array of the same fields.
            </p>
            <a href="/sample-statement.csv" download>
              Download a sample CSV
              <ArrowRight size={13} />
            </a>
          </details>
        </div>
      </div>
    </div>
  );
}
