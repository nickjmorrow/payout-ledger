import { authHeaders } from 'src/api/auth';

/**
 * The HTTP boundary.
 *
 * Every non-streaming request goes through `apiFetch`, which knows two things
 * so no caller has to: responses are wrapped in `{ data, meta }`, and a
 * non-2xx status is an error rather than a value.
 */

export interface ApiEnvelope<T> {
  data: T;
  meta: { status: string };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * The human-readable message out of an error response.
 *
 * FastAPI puts it in `detail` — but only for `HTTPException`. A 422 from
 * request validation puts an *array* of per-field errors there instead, and a
 * failure from in front of the app (a proxy, a dead upstream) has no JSON at
 * all. Anything that is not a plain string falls back to the status text,
 * because `[object Object]` in an error banner is worse than "Bad Request".
 */
export async function errorDetail(response: Response): Promise<string> {
  const body: unknown = await response.json().catch(() => null);
  const detail = (body as { detail?: unknown } | null)?.detail;
  return typeof detail === 'string' ? detail : response.statusText;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // Built with `Headers` rather than object spread. `RequestInit['headers']`
  // is allowed to be a `Headers` instance or an array of [name, value] pairs
  // as well as a plain object, and spreading either of those into an object
  // literal silently yields nothing useful instead of headers. Precedence is
  // unchanged: content type, then auth, then whatever the caller passed.
  const headers = new Headers({ 'Content-Type': 'application/json' });
  const auth = await authHeaders();
  for (const [name, value] of Object.entries(auth)) headers.set(name, value);
  new Headers(init?.headers).forEach((value, name) => headers.set(name, value));

  const response = await fetch(`/api${path}`, { ...init, headers });

  if (!response.ok) {
    throw new ApiError(await errorDetail(response), response.status);
  }

  const envelope = (await response.json()) as ApiEnvelope<T>;
  return envelope.data;
}
