/**
 * Small display formatters. Pure, no React — the same reason `turns.ts` sits
 * out here rather than in `components/`.
 */

const SECOND_MS = 1000;
const MINUTE_MS = 60 * SECOND_MS;

/** How long a tool took, at the precision a human cares about. */
export function formatDuration(ms: number): string {
  if (ms < SECOND_MS) return `${String(Math.round(ms))}ms`;
  if (ms < MINUTE_MS) return `${(ms / SECOND_MS).toFixed(1)}s`;

  const minutes = Math.floor(ms / MINUTE_MS);
  const seconds = Math.round((ms % MINUTE_MS) / SECOND_MS);
  return `${String(minutes)}m ${String(seconds)}s`;
}

/** Clock time for a list row, in the reader's own locale and zone. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/**
 * Clock time to the second, for a history where the order of events matters.
 *
 * A send and its settlement check can be three seconds apart; to the minute
 * they are the same moment, and a reader cannot tell which came first.
 */
export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * How far a moment is from `now`, in the coarsest unit that still says
 * something: "in 12s", "3m ago", "just now".
 *
 * `now` is a parameter rather than `Date.now()` so the function is a pure
 * one of its inputs and a test can pin an answer. A scheduled task's `run_at`
 * is the thing this exists for — an operator wants "in 25s" beside a settlement
 * check, not a timestamp to subtract in their head.
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

/**
 * Tool arguments on one line, for the collapsed header.
 *
 * Long values are cut rather than wrapped: the header is a glance, and the
 * expanded view below it has the whole thing.
 */
export function summarizeToolInput(input: Record<string, unknown>, limit = 80): string {
  const entries = Object.entries(input);
  if (entries.length === 0) return 'no arguments';

  const rendered = entries
    .map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(', ');

  return rendered.length > limit ? `${rendered.slice(0, limit)}…` : rendered;
}
