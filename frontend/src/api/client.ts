import { authHeaders } from 'src/api/auth';

/**
 * The HTTP boundary. `apiFetch` unwraps `{ data, meta }` and turns a non-2xx
 * status into an `ApiError`.
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
 * FastAPI's `detail` is a string, or an array of validation errors. With no body
 * and no status text (HTTP/2 has none), name the status rather than show nothing.
 */
export async function errorDetail(response: Response): Promise<string> {
  const body: unknown = await response.json().catch(() => null);
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: unknown) => (item as { msg?: unknown }).msg)
      .filter((msg): msg is string => typeof msg === 'string')
      // Pydantic prefixes a validator's own message with its category.
      .map((msg) => msg.replace(/^Value error, /, ''));
    if (messages.length > 0) return messages.join('; ');
  }
  return response.statusText || `Request failed (${String(response.status)})`;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // `Headers` rather than object spread: `init.headers` may be a `Headers` or an
  // array, which spreading silently drops. Content type, then auth, then the caller's.
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
