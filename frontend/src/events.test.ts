import { describe, expect, it } from 'vitest';
import { type ChangeEvent, isChangeEvent, keysToInvalidate } from 'src/events';

const change = (topic: ChangeEvent['topic'], transferId: null | string = null): ChangeEvent => ({
  id: 'x',
  topic,
  transferId,
});

describe('keysToInvalidate', () => {
  it('refreshes the overview with every change, so balances never lag the list', () => {
    for (const topic of ['transfers', 'tasks', 'findings'] as const) {
      expect(keysToInvalidate([change(topic)])).toContainEqual(['overview']);
    }
  });

  it('refreshes the transfer family when a transfer moves', () => {
    expect(keysToInvalidate([change('transfers')])).toEqual([['overview'], ['transfers']]);
  });

  it('refreshes only the one history when a task moves, not the whole list', () => {
    expect(keysToInvalidate([change('tasks', 't-1')])).toEqual([
      ['overview'],
      ['tasks'],
      ['transfers', 't-1'],
    ]);
  });

  it('collapses a burst into one refresh per key', () => {
    const burst = Array.from({ length: 20 }, () => change('transfers'));
    expect(keysToInvalidate(burst)).toEqual([['overview'], ['transfers']]);
  });

  it('drops a detail key when its whole family is already being refreshed', () => {
    const keys = keysToInvalidate([change('tasks', 't-1'), change('transfers')]);
    expect(keys).toContainEqual(['transfers']);
    expect(keys).not.toContainEqual(['transfers', 't-1']);
  });
});

describe('isChangeEvent', () => {
  it('accepts a notice in a known topic', () => {
    expect(isChangeEvent({ id: 'a', topic: 'findings', transferId: null })).toBe(true);
  });

  it('refuses a topic this console has not heard of', () => {
    expect(isChangeEvent({ id: 'a', topic: 'refunds', transferId: null })).toBe(false);
  });

  it('refuses anything that is not a notice', () => {
    expect(isChangeEvent(null)).toBe(false);
    expect(isChangeEvent('transfers')).toBe(false);
    expect(isChangeEvent({ topic: 'transfers' })).toBe(false);
  });
});
