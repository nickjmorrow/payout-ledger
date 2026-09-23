/**
 * How often the console re-reads when nobody tells it to. Pure, no React.
 *
 * **Polling is the fallback now, not the mechanism.** Changes arrive as they
 * happen over the event stream (`api/events.ts`), and each one re-reads
 * exactly the queries it affects. What is left for a timer is the case where
 * the stream is not there: before it connects, after it drops, or behind a
 * proxy that will not hold one open. Then the console polls as it always did,
 * quickly while something is in flight and slowly once nothing is.
 *
 * While the stream is up, a slow backstop remains. A notice can be lost — the
 * design says that must cost a moment of staleness and nothing worse — and the
 * backstop is what bounds the moment.
 *
 * **One cadence, shared.** The balances and the transfer list are separate
 * queries, and if they refreshed independently the console could show a
 * transfer as settled beside a float balance from before it settled — which,
 * for a ledger, reads as the books not adding up. Deriving every interval from
 * this one function keeps them in step when polling, and `events.ts` does the
 * same job for the live path by refreshing the overview with every change.
 */

/** Polling, while something is still moving. */
export const LIVE_MS = 2000;

/** Polling, once everything has settled. */
export const IDLE_MS = 15_000;

/** With the stream connected: only a safety net for a lost notice. */
export const BACKSTOP_MS = 60_000;

interface Pollable {
  status: string;
}

/**
 * The interval to use given what is on screen and whether the stream is up.
 *
 * Undefined rows count as moving: nothing has loaded yet, so the console does
 * not know whether anything is in flight, and guessing idle would make the
 * first update take fifteen seconds.
 */
export function pollIntervalFor(rows: Pollable[] | undefined, isLive = false): number {
  if (isLive) return BACKSTOP_MS;
  if (rows === undefined) return LIVE_MS;
  const isMoving = rows.some((row) => row.status === 'pending' || row.status === 'processing');
  return isMoving ? LIVE_MS : IDLE_MS;
}
