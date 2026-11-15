import type { Batch, Graph } from "./types";
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function timedFetch(url: string, init: RequestInit = {}) {
  const signal = init.signal
    ? AbortSignal.any([init.signal, AbortSignal.timeout(120_000)])
    : AbortSignal.timeout(120_000);
  return fetch(url, { ...init, signal });
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await timedFetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail =
        typeof body.detail === "string"
          ? body.detail
          : "Some supplied fields are invalid. Check the selected documents and retry.";
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(
      detail || `Request failed (${response.status})`,
      response.status,
    );
  }
  return response.json();
}
export const jsonPost = <T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
) =>
  api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
export function partitionFiles(files: File[]): File[][] {
  if (!files.length) throw new Error("Choose at least one document.");
  const batches: File[][] = [];
  let current: File[] = [];
  let size = 0;
  for (const file of files) {
    if (!/\.(pdf|csv|json)$/i.test(file.name))
      throw new Error(`${file.name}: use a PDF, CSV, or JSON file.`);
    if (!file.size || file.size > 25 * 1024 * 1024)
      throw new Error(`${file.name}: file must be between 1 byte and 25 MiB.`);
    if (current.length === 100 || size + file.size > 100 * 1024 * 1024) {
      batches.push(current);
      current = [];
      size = 0;
    }
    current.push(file);
    size += file.size;
  }
  if (current.length) batches.push(current);
  return batches;
}
export async function uploadGroup(
  files: File[],
  progress: (name: string) => void,
  options: { signal?: AbortSignal; onFile?: (bytes: number) => void } = {},
): Promise<Batch> {
  const { signal } = options;
  const metadata = [];
  for (const file of files) {
    signal?.throwIfAborted();
    progress(`Preparing ${file.name}`);
    const hash = await crypto.subtle.digest(
      "SHA-256",
      await file.arrayBuffer(),
    );
    signal?.throwIfAborted();
    metadata.push({
      filename: file.name,
      size: file.size,
      sha256: Array.from(new Uint8Array(hash), (n) =>
        n.toString(16).padStart(2, "0"),
      ).join(""),
    });
  }
  let signed: {
    uploads: { url: string; headers: Record<string, string> }[];
    complete_url: string;
  };
  try {
    signed = await jsonPost("/uploads/presign", { files: metadata }, signal);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409) throw error;
    progress("Uploading this batch");
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    const batch = await api<Batch>("/batches", {
      method: "POST",
      body: form,
      signal,
    });
    options.onFile?.(files.reduce((n, f) => n + f.size, 0));
    return batch;
  }
  for (let i = 0; i < files.length; i++) {
    signal?.throwIfAborted();
    progress(`Uploading ${files[i].name}`);
    const response = await timedFetch(signed.uploads[i].url, {
      method: "PUT",
      headers: signed.uploads[i].headers,
      body: files[i],
      signal,
    });
    if (!response.ok)
      throw new Error(
        `Could not upload ${files[i].name}. Retry the remaining batch; accepted batches are preserved.`,
      );
    options.onFile?.(files[i].size);
  }
  progress("Verifying documents before processing");
  return api<Batch>(signed.complete_url, { method: "POST", signal });
}
export async function graphForBatches(
  ids: string[],
  before?: string,
  after?: string,
): Promise<Graph> {
  const nodes = new Map(),
    edges = new Map();
  let truncated = false;
  for (let index = 0; index < ids.length; index++) {
    const id = ids[index];
    let offset = 0;
    while (edges.size < 2000) {
      const page = await api<Graph>(
        `/graph?batch_id=${encodeURIComponent(id)}&limit=${Math.min(1000, 2000 - edges.size)}&offset=${offset}${before ? `&before=${encodeURIComponent(before)}` : ""}${after ? `&after=${encodeURIComponent(after)}` : ""}`,
      );
      page.nodes.forEach((n) => nodes.set(n.id, n));
      page.edges.forEach((e) => edges.set(e.id, e));
      if (!page.has_more) {
        if (edges.size === 2000 && index < ids.length - 1) truncated = true;
        break;
      }
      if (edges.size >= 2000) {
        truncated = true;
        break;
      }
      offset = page.next_offset!;
    }
    if (edges.size >= 2000) break;
  }
  return {
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    has_more: truncated,
  };
}
