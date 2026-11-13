import type {
  Batch,
  ChannelInfo,
  Complaint,
  ComplaintDetail,
  DirectoryResult,
  Envelope,
  FilterOptions,
  Filters,
  IssueDetail,
  IssueSummary,
  Overview,
  SegmentsResponse,
  SimilarComplaint,
  Trends,
  UploadResult,
} from "../types/api";

const BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  details?: unknown;
  constructor(status: number, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

/** Serialize filters (+extra params) into a query string. Arrays become comma-separated values. */
export function buildQuery(filters: Filters = {}, extra: Record<string, string | number | boolean | undefined> = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (Array.isArray(value)) {
      if (value.length) params.set(key, value.join(","));
    } else if (value) {
      params.set(key, String(value));
    }
  }
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "error" in body) {
    const err = (body as { error: { message?: unknown } }).error;
    if (typeof err?.message === "string") return err.message;
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<Envelope<T>> {
  const res = await fetch(`${BASE}${path}`, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, errorMessage(body, `Request failed (${res.status})`), body?.error?.details);
  }
  return body as Envelope<T>;
}

export const api = {
  overview: (f: Filters) => request<Overview>(`/analytics/overview${buildQuery(f)}`),
  trends: (f: Filters, granularity: "D" | "W" = "D") =>
    request<Trends>(`/analytics/trends${buildQuery(f, { granularity })}`),
  segments: (f: Filters, dimension?: string) =>
    request<SegmentsResponse>(`/analytics/segments${buildQuery(f, { dimension })}`),
  filterOptions: () => request<FilterOptions>(`/analytics/filters`),
  issues: (f: Filters, page = 1, pageSize = 50) =>
    request<IssueSummary[]>(`/issues${buildQuery(f, { page, page_size: pageSize })}`),
  top10: (f: Filters) => request<IssueSummary[]>(`/issues/top10${buildQuery(f)}`),
  issue: (id: string, f: Filters) => request<IssueDetail>(`/issues/${encodeURIComponent(id)}${buildQuery(f)}`),
  patchIssue: (id: string, body: Partial<Pick<IssueDetail, "title" | "description" | "status" | "severity">>) =>
    request<IssueDetail>(`/issues/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  complaints: (f: Filters, opts: { page?: number; pageSize?: number; issueId?: string; search?: string }) =>
    request<Complaint[]>(
      `/complaints${buildQuery(f, { page: opts.page ?? 1, page_size: opts.pageSize ?? 25, issue_id: opts.issueId, search: opts.search })}`,
    ),
  complaint: (id: string) => request<ComplaintDetail>(`/complaints/${encodeURIComponent(id)}`),
  similar: (id: string, topK = 5) => request<SimilarComplaint[]>(`/complaints/${encodeURIComponent(id)}/similar?top_k=${topK}`),
  batch: (id: string) => request<Batch>(`/batches/${encodeURIComponent(id)}`),
  batches: () => request<Batch[]>(`/batches`),
  channels: () => request<ChannelInfo[]>(`/ingest/channels`),
  recluster: () => request<{ queued: boolean }>(`/pipeline/recluster`, { method: "POST" }),
  process: (batchId: string) => request<{ queued: boolean }>(`/pipeline/process/${encodeURIComponent(batchId)}`, { method: "POST" }),
};

/** Multipart upload with progress reporting (fetch has no upload progress events, so XHR is used). */
export function uploadFiles<T>(
  path: string,
  files: File[],
  field: "file" | "files",
  onProgress: (fraction: number) => void,
): Promise<Envelope<T>> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}${path}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON error body */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Envelope<T>);
      else {
        const details = (body as { error?: { details?: unknown } } | null)?.error?.details;
        reject(new ApiError(xhr.status, errorMessage(body, `Upload failed (${xhr.status})`), details));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error - is the backend running on port 8000?"));
    const form = new FormData();
    for (const f of files) form.append(field, f);
    xhr.send(form);
  });
}

export const uploadCsv = (file: File, onProgress: (fraction: number) => void) =>
  uploadFiles<UploadResult>("/complaints/upload", [file], "file", onProgress);

export const uploadChannel = (channel: string, files: File[], onProgress: (fraction: number) => void) =>
  uploadFiles<UploadResult>(`/ingest/${encodeURIComponent(channel)}`, files, "files", onProgress);

export const uploadDirectory = (file: File, onProgress: (fraction: number) => void) =>
  uploadFiles<DirectoryResult>("/customers/directory", [file], "file", onProgress);
