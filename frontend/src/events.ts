/**
 * Which queries a change notice re-reads. Pure, no React.
 *
 * The server says what moved, never what it now contains.
 */

import { ledgerKeys } from 'src/api/ledger';

/**
 * What a change can be about. Must match `Topic` in backend/app/wire.py; a
 * structural test checks it.
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
 * The query keys to invalidate for a batch of changes, deduplicated, with any
 * key covered by a shorter one dropped.
 *
 * The overview is refreshed with every change, so balances never lag the list.
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
        // Refresh only that transfer's history: a task moving is not a transfer moving.
        if (event.transferId !== null) keys.push(ledgerKeys.transfer(event.transferId));
        break;
      }
      case 'transfers': {
        // A run's progress is its transfers' statuses, so it moves with them.
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
