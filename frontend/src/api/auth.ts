/**
 * Where the access token comes from: a slot for any OIDC provider's SDK.
 *
 * Call `setAccessTokenProvider` once at startup and every request carries the
 * bearer token:
 *
 *     setAccessTokenProvider(() => auth.getToken());
 *
 * The default returns null and sends no header, which is what the backend wants
 * while `OIDC_ISSUER` is unset.
 */

type AccessTokenProvider = () => Promise<null | string> | null | string;

let provider: AccessTokenProvider = () => null;

export function setAccessTokenProvider(next: AccessTokenProvider): void {
  provider = next;
}

/** The `Authorization` header, or nothing. Async because most SDKs refresh. */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = await provider();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
