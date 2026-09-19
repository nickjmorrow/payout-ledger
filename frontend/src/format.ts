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

/** Clock time for a transcript row, in the reader's own locale and zone. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
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
