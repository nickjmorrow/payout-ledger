import { describe, expect, it } from 'vitest';
import type { Task } from 'src/api/ledger';
import { timingOf } from 'src/queue';

const NOW = Date.UTC(2026, 8, 23, 12, 0, 0);
const at = (offsetMs: number) => new Date(NOW + offsetMs).toISOString();

const task = (overrides: Partial<Task>): Task => ({
  attempts: 0,
  claimedBy: null,
  createdAt: new Date(NOW).toISOString(),
  error: null,
  id: 't',
  kind: 'settle_transfer',
  maxAttempts: 3,
  runAt: new Date(NOW).toISOString(),
  status: 'pending',
  transferId: null,
  updatedAt: new Date(NOW).toISOString(),
  ...overrides,
});

describe('timingOf', () => {
  it('names the worker holding a running task, briefly', () => {
    expect(timingOf(task({ claimedBy: 'e175f28154af:110', status: 'running' }), NOW)).toBe(
      'on e175f2…:110',
    );
  });

  it('says a task is due once its time has come', () => {
    const due = task({ runAt: at(-5000) });
    expect(timingOf(due, NOW)).toBe('due now');
  });

  it('counts down to a scheduled task', () => {
    const scheduled = task({ runAt: at(12_000) });
    expect(timingOf(scheduled, NOW)).toBe('in 12s');
  });
});
