// The one place the browser talks to the API.
//
// Requests go to the same origin (/api/*), which Next.js proxies to FastAPI, so
// the httpOnly session cookie is first-party. This module adds the CSRF token to
// unsafe methods, selects the workspace, and turns every failure into ApiError.

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly requestId: string | null = null,
    readonly details: Array<{ field: string; message: string }> = []
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Store = {
  csrf: string | null;
  workspaceId: string | null;
  onUnauthorized: (() => void) | null;
};

export const apiStore: Store = { csrf: null, workspaceId: null, onUnauthorized: null };

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

type Options = {
  method?: string;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
};

function headersFor(method: string, hasJson: boolean): HeadersInit {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (hasJson) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method) && apiStore.csrf) headers["X-CSRF-Token"] = apiStore.csrf;
  if (apiStore.workspaceId) headers["X-Workspace-Id"] = apiStore.workspaceId;
  return headers;
}

async function send(path: string, { method = "GET", json, form, signal }: Options): Promise<Response> {
  try {
    return await fetch(path, {
      method,
      credentials: "same-origin",
      headers: headersFor(method, json !== undefined),
      body: form ?? (json !== undefined ? JSON.stringify(json) : undefined),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Cannot reach cermat. Check your connection and try again.", "NETWORK_ERROR", 0);
  }
}

async function toError(response: Response): Promise<ApiError> {
  const payload = await response.json().catch(() => null);
  const error = payload?.error;
  if (response.status === 401) apiStore.onUnauthorized?.();
  return new ApiError(
    typeof error?.message === "string" ? error.message : `Request failed (${response.status}).`,
    typeof error?.code === "string" ? error.code : "INTERNAL_ERROR",
    response.status,
    error?.request_id ?? response.headers.get("x-request-id"),
    Array.isArray(error?.details) ? error.details : []
  );
}

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const response = await send(path, options);
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** POST a JSON body and read NDJSON events until the stream ends. */
export async function apiStream<E>(
  path: string,
  json: unknown,
  onEvent: (event: E) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await send(path, { method: "POST", json, signal });
  if (!response.ok) throw await toError(response);
  if (!response.body) throw new ApiError("Empty response.", "INTERNAL_ERROR", response.status);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as E);
      newline = buffer.indexOf("\n");
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as E);
}

/** Plain links (source files, CSV) cannot send headers, so the workspace goes in the query. */
export function workspaceUrl(path: string): string {
  const separator = path.includes("?") ? "&" : "?";
  return apiStore.workspaceId ? `${path}${separator}workspace_id=${apiStore.workspaceId}` : path;
}

export function errorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.name !== "AbortError") return error.message;
  return fallback;
}
