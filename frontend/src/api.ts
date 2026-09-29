const BASE = "/api";

export class ApiError extends Error {
  status: number;
  code: string;
  extra: Record<string, any>;
  constructor(status: number, code: string, message: string, extra: Record<string, any> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

let token: string | null = localStorage.getItem("opsdesk.token");
let onUnauthorized: () => void = () => {};

export function setToken(t: string | null) {
  token = t;
  if (t) localStorage.setItem("opsdesk.token", t);
  else localStorage.removeItem("opsdesk.token");
}
export function getToken() {
  return token;
}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

interface Options {
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  /** Makes a mutating request safe to repeat: the server executes it at most once per key. */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  // Retrying after a network failure is only safe when the server can de-duplicate,
  // i.e. when we sent an idempotency key. Without one we fail fast instead of risking a double write.
  const attempts = opts.idempotencyKey ? 3 : 1;

  for (let attempt = 1; ; attempt++) {
    let res: Response;
    try {
      res = await fetch(BASE + path, {
        method: opts.method ?? "GET",
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: opts.signal,
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      if (attempt < attempts) {
        await sleep(300 * 2 ** (attempt - 1));
        continue;
      }
      throw new ApiError(
        0,
        "network",
        opts.method && opts.method !== "GET"
          ? "Could not reach the server. Your change may not have been saved; check and try again."
          : "Could not reach the server.",
      );
    }

    if (res.ok) return (await res.json()) as T;

    let err: { code?: string; message?: string; [k: string]: any } = {};
    try {
      err = (await res.json()).error ?? {};
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401 && path !== "/auth/login") onUnauthorized();
    const { code, message, ...extra } = err;
    throw new ApiError(res.status, code ?? "error", message ?? `Request failed (${res.status})`, extra);
  }
}

export function newKey(): string {
  return crypto.randomUUID();
}
