import { describe, expect, it } from 'vitest';
import { IDLE_MS, LIVE_MS, pollIntervalFor } from 'src/polling';

describe('pollIntervalFor', () => {
  it('polls quickly while anything is in flight', () => {
    expect(pollIntervalFor([{ status: 'pending' }])).toBe(LIVE_MS);
    expect(pollIntervalFor([{ status: 'succeeded' }, { status: 'processing' }])).toBe(LIVE_MS);
  });

  it('backs off once everything has settled', () => {
    expect(pollIntervalFor([{ status: 'succeeded' }, { status: 'failed' }])).toBe(IDLE_MS);
    expect(pollIntervalFor([])).toBe(IDLE_MS);
  });

  it('treats not-yet-loaded as live', () => {
    // Guessing idle here would make the very first update take fifteen seconds.
    expect(pollIntervalFor(undefined)).toBe(LIVE_MS);
  });
});
