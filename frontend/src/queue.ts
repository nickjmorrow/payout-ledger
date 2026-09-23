/**
 * What an unfinished task is doing, in a phrase. Pure, no React.
 *
 * `now` is a parameter so a countdown can be tested without a clock; the
 * component passes it from `useNow`.
 */

import type { Task } from 'src/api/ledger';
import { formatRelative } from 'src/format';

/**
 * `"on 7f3a…:12"` while a worker holds it, `"due now"` once it may run, and
 * `"in 12s"` until then.
 *
 * Naming the worker is the point of the first form: with two workers the
 * queue shows them taking different rows, which is `FOR UPDATE SKIP LOCKED`
 * visibly holding. The container id is cut to its first few characters; the
 * pid after the colon is what tells two processes on one host apart.
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
