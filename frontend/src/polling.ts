/**
 * How often the console re-reads. Pure, no React.
 *
 * A transfer changes state a handful of times over a few seconds and then
 * never again, which is not worth an open connection — so the console polls,
 * and backs off once nothing is in flight rather than asking twice a second
 * for an answer that will not change.
 *
 * **One cadence, shared by every view.** The balances and the transfer list
 * are separate queries, and if they polled independently the console could
 * show a transfer as settled beside a float balance from before it settled —
 * which, for a ledger, reads as the books not adding up. Deriving both
 * intervals from the same function keeps them in step.
 */

/** While something is still moving. */
export const LIVE_MS = 2000;

/** And when everything has settled. */
export const IDLE_MS = 15_000;

interface Pollable {
  status: string;
}

/**
 * The interval to use given what is currently on screen.
 *
 * Undefined counts as live: nothing has loaded yet, so the console does not
 * know whether anything is moving, and guessing idle would make the first
 * update take fifteen seconds.
 */
export function pollIntervalFor(rows: Pollable[] | undefined): number {
  if (rows === undefined) return LIVE_MS;
  const isMoving = rows.some((row) => row.status === 'pending' || row.status === 'processing');
  return isMoving ? LIVE_MS : IDLE_MS;
}
