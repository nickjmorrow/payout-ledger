/** What an unfinished task is doing, in a phrase. Pure, no React. */

import type { Task } from 'src/api/ledger';
import { formatRelative } from 'src/format';

/**
 * `"on 7f3a…:12"` while a worker holds it, `"due now"` once it may run, and
 * `"in 12s"` until then. The worker is named so two workers can be seen taking
 * different rows.
 */
export function timingOf(task: Task, now: number): string {
  if (task.status === 'running') {
    if (task.claimedBy === null) return 'running';
    const [host = '', pid] = task.claimedBy.split(':', 2);
    const short = host.length > 6 ? `${host.slice(0, 6)}…` : host;
    return `on ${pid === undefined ? short : `${short}:${pid}`}`;
  }
  if (new Date(task.runAt).getTime() <= now) return 'due now';
  return formatRelative(task.runAt, now);
}
