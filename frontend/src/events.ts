/**
 * What a change notice means for the console's queries. Pure, no React.
 *
 * The server says *what* moved — a transfer, a task, a finding — and never
 * what it now contains. This decides which queries to re-read as a result,
 * which is the whole of the browser's side of the live path. Keeping it a
 * function of data means the mapping can be tested without a stream, a
 * renderer, or a clock.
 */

import { ledgerKeys } from 'src/api/ledger';

/**
 * The families a change can belong to.
 *
 * Mirrors `Topic` in `backend/app/wire.py`. The backend's structural suite
 * reads this array and fails when the two differ, because a topic the browser
 * has not heard of is a change it silently never shows.
 */
export const TOPICS = ['transfers', 'tasks', 'findings'] as const;

export type Topic = (typeof TOPICS)[number];

export interface ChangeEvent {
  id: string;
  topic: Topic;
  transferId: null | string;
}

type QueryKey = readonly string[];

/** Whether a parsed frame is a change notice this console understands. */
export function isChangeEvent(value: unknown): value is ChangeEvent {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.id === 'string' &&
    (TOPICS as readonly unknown[]).includes(candidate.topic) &&
    (candidate.transferId === null || typeof candidate.transferId === 'string')
  );
}

/**
 * The query keys to invalidate for a batch of changes: deduplicated, and with
 * any key already covered by a shorter one dropped.
 *
 * **The overview goes with everything.** Its balances move when a transfer
 * does, and its "needs attention" count moves when a task dead-letters or a
 * finding appears. Refreshing it in the same batch as the list is what keeps a
 * settled transfer from sitting beside a float balance from before it
 * settled — the bug the shared polling cadence was built to prevent, now
 * prevented the same way: one moment, every view.
 */
export function keysToInvalidate(events: readonly ChangeEvent[]): QueryKey[] {
  const keys: QueryKey[] = [];
  for (const event of events) {
    keys.push(ledgerKeys.overview);
    switch (event.topic) {
      case 'findings': {
        keys.push(ledgerKeys.findings);
        break;
      }
      case 'tasks': {
        keys.push(ledgerKeys.tasks);
        // A task's transfer may have its history open. Its row in the list has
        // not changed — a task moving is not a transfer moving — so only that
        // one detail is re-read, not the whole family.
        if (event.transferId !== null) keys.push(ledgerKeys.transfer(event.transferId));
        break;
      }
      case 'transfers': {
        // A run's progress is its transfers' statuses, so a transfer moving is
        // a run moving. No topic of its own: there is nothing on a run to change.
        keys.push(ledgerKeys.transfers, ledgerKeys.runs);
        break;
      }
    }
  }
  return prune(keys);
}

/** Drop duplicates, and any key whose prefix is also being invalidated. */
function prune(keys: readonly QueryKey[]): QueryKey[] {
  const unique = [...new Map(keys.map((key) => [JSON.stringify(key), key])).values()];
  const isPrefixOf = (shorter: QueryKey, key: QueryKey) =>
    shorter.length < key.length && shorter.every((part, index) => part === key[index]);
  return unique.filter((key) => unique.every((other) => !isPrefixOf(other, key)));
}
