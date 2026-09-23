/** Display formatters. Pure, no React. */

const SECOND_MS = 1000;
const MINUTE_MS = 60 * SECOND_MS;

/** `"Sep 23, 6:24 PM"`, in the reader's zone. The date matters in a list that outlives a day. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    month: 'short',
  });
}

/** Time to the second, for a history where events seconds apart must read in order. */
export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * How far a moment is from `now`: "in 12s", "3m ago", "just now".
 *
 * `now` is a parameter so a test can pin it.
 */
export function formatRelative(iso: string, now: number): string {
  const delta = new Date(iso).getTime() - now;
  const magnitude = Math.abs(delta);
  if (magnitude < SECOND_MS) return 'just now';

  const span =
    magnitude < MINUTE_MS
      ? `${String(Math.round(magnitude / SECOND_MS))}s`
      : `${String(Math.round(magnitude / MINUTE_MS))}m`;
  return delta > 0 ? `in ${span}` : `${span} ago`;
}

/** `+12025550101` -> `"(202) 555-0101"`. Other countries' numbers are shown as stored. */
export function formatPhone(msisdn: string): string {
  const us = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(msisdn);
  return us ? `(${us[1] ?? ''}) ${us[2] ?? ''}-${us[3] ?? ''}` : msisdn;
}
