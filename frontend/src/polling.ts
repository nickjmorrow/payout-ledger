/**
 * How often the console re-reads without being told to. Pure, no React.
 *
 * A fallback: while the event stream is live, only a slow backstop remains. One
 * shared cadence keeps every view in step. See AGENTS.md > Frontend.
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
 * The interval, given what is on screen and whether the stream is up.
 *
 * Not-yet-loaded counts as moving, so the first update is not slow.
 */
export function pollIntervalFor(rows: Pollable[] | undefined, isLive = false): number {
  if (isLive) return BACKSTOP_MS;
  if (rows === undefined) return LIVE_MS;
  const isMoving = rows.some((row) => row.status === 'pending' || row.status === 'processing');
  return isMoving ? LIVE_MS : IDLE_MS;
}
