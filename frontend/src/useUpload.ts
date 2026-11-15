import { useRef, useState } from "react";
import { partitionFiles, uploadGroup } from "./api";
export function useUpload(
  onAccepted: (jobId: string, batchId: string) => void,
) {
  const [files, setFiles] = useState<File[]>([]),
    [running, setRunning] = useState(false),
    [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [completed, setCompleted] = useState(0),
    [transferred, setTransferred] = useState(0);
  const controller = useRef<AbortController | null>(null),
    done = useRef(0),
    jobId = useRef<string | null>(null);
  function add(incoming: File[]) {
    if (running || done.current) return;
    try {
      partitionFiles(incoming);
      setFiles((prev) => [
        ...prev,
        ...incoming.filter(
          (f) =>
            !prev.some(
              (p) =>
                p.name === f.name &&
                p.size === f.size &&
                p.lastModified === f.lastModified,
            ),
        ),
      ]);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function start() {
    if (running) return;
    setRunning(true);
    setError("");
    controller.current = new AbortController();
    jobId.current ||= crypto.randomUUID();
    let bytes = files
      .slice(
        0,
        partitionFiles(files)
          .slice(0, done.current)
          .reduce((n, g) => n + g.length, 0),
      )
      .reduce((n, f) => n + f.size, 0);
    setTransferred(bytes);
    try {
      const groups = partitionFiles(files);
      for (let i = done.current; i < groups.length; i++) {
        controller.current.signal.throwIfAborted();
        setStatus(`Uploading batch ${i + 1} of ${groups.length}`);
        const batch = await uploadGroup(groups[i], setStatus, {
          signal: controller.current.signal,
          onFile: (n) => {
            bytes += n;
            setTransferred(bytes);
          },
        });
        done.current = i + 1;
        setCompleted(done.current);
        onAccepted(jobId.current, batch.id);
      }
      setStatus(
        "All batches accepted. Document processing continues in the background.",
      );
    } catch (e) {
      if (controller.current.signal.aborted)
        setStatus(
          "Remaining uploads cancelled. Accepted batches are preserved.",
        );
      else {
        setError(
          (e as Error).name === "TimeoutError"
            ? "The upload timed out. Retry the remaining batch; accepted batches are preserved."
            : (e as Error).message,
        );
        setStatus("Upload paused. Your completed batches are safe.");
      }
    } finally {
      setRunning(false);
    }
  }
  function reset() {
    if (running) return;
    setFiles([]);
    done.current = 0;
    jobId.current = null;
    setCompleted(0);
    setTransferred(0);
    setError("");
    setStatus("");
  }
  return {
    files,
    setFiles,
    add,
    start,
    reset,
    running,
    error,
    status,
    completed,
    transferred,
    jobId: jobId.current,
    cancel: () => controller.current?.abort(),
    remove: (index: number) => {
      if (!running && !done.current)
        setFiles((prev) => prev.filter((_, i) => i !== index));
    },
  };
}
