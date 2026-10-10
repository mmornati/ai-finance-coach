import { qs } from "./utils";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let csrf: string | null = null;
let csrfPending: Promise<string> | null = null;

async function loadCsrf(): Promise<string> {
  const res = await fetch("/api/v1/session", { credentials: "same-origin" });
  if (!res.ok) throw await toError(res);
  const j = (await res.json()) as { csrf_token: string };
  csrf = j.csrf_token;
  return csrf;
}

function getCsrf(): Promise<string> {
  if (csrf) return Promise.resolve(csrf);
  csrfPending ??= loadCsrf().finally(() => {
    csrfPending = null;
  });
  return csrfPending;
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const j = await res.json();
    const e = j.error ?? j;
    return new ApiError(res.status, e.code ?? "error", e.message ?? res.statusText, e.details);
  } catch {
    return new ApiError(res.status, "error", res.statusText || `HTTP ${res.status}`);
  }
}

/** Fired when the server says there is no (longer a) session: the app shows the login screen. */
export const UNAUTHORIZED_EVENT = "coach:unauthorized";

let exchanging: Promise<void> | null = null;

/** Trade the one-time login token (from the URL fragment) for the session cookie. Deduplicated: React StrictMode runs
 *  effects twice and the token is single-use. */
export function exchangeToken(token: string): Promise<void> {
  exchanging ??= (async () => {
    const res = await fetch("/api/v1/session/exchange", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ token }),
    });
    if (!res.ok) throw await toError(res);
    csrf = null;
  })();
  return exchanging;
}

export function resetExchangeForTests() {
  exchanging = null;
}

/** The few calls that need no session (E16): what the login page may offer, and a passkey sign-in. No CSRF token (there is no
 *  session yet), no 401 event (the page IS the login page). */
export async function publicGet<T>(path: string): Promise<T> {
  const res = await fetch(`/api/v1${path}`, { credentials: "same-origin" });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export async function publicPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await toError(res);
  csrf = null;                                   // a sign-in may have produced a new cookie
  return (await res.json()) as T;
}

export interface RequestOpts {
  params?: Record<string, unknown>;
  body?: unknown;
  signal?: AbortSignal;
}

export async function request<T>(method: string, path: string, o: RequestOpts = {}, retry = true): Promise<T> {
  const headers: Record<string, string> = {};
  if (o.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = await getCsrf();
  const res = await fetch(`/api/v1${path}${qs(o.params)}`, {
    method,
    headers,
    credentials: "same-origin",
    body: o.body !== undefined ? JSON.stringify(o.body) : undefined,
    signal: o.signal,
  });
  if (!res.ok) {
    const err = await toError(res);
    if (res.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    if (retry && method !== "GET" && err.code === "csrf") {
      csrf = null;
      return request<T>(method, path, o, false);
    }
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, params?: Record<string, unknown>, signal?: AbortSignal) => request<T>("GET", path, { params, signal }),
  post: <T>(path: string, body?: unknown, params?: Record<string, unknown>) => request<T>("POST", path, { body, params }),
  put: <T>(path: string, body?: unknown, params?: Record<string, unknown>) => request<T>("PUT", path, { body, params }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  delete: <T>(path: string) => request<T>("DELETE", path),
  logout: () => request<{ ok: boolean }>("POST", "/session/logout", { body: {} }),
  /** Same-origin URL for a download (the session cookie authorises it). */
  url: (path: string, params?: Record<string, unknown>) => `/api/v1${path}${qs(params)}`,
};

export function resetCsrfForTests() {
  csrf = null;
  csrfPending = null;
}

/** Server-sent events over fetch (the stream endpoint is a POST, which EventSource cannot do). */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": await getCsrf() },
    credentials: "same-origin",
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw await toError(res);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let ev = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) ev = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        try {
          onEvent(ev, JSON.parse(data));
        } catch {
          onEvent(ev, data);
        }
      }
    }
  }
}
