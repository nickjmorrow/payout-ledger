import { describe, expect, it } from 'vitest';

import { formatDuration, formatRelative, summarizeToolInput } from 'src/format';

// `formatTime` and `formatClock` are deliberately untested: they format in the
// reader's locale and zone, so any assertion here is an assertion about the
// machine running it.

describe('formatDuration', () => {
  it('uses whole milliseconds below a second', () => {
    expect(formatDuration(0)).toBe('0ms');
    expect(formatDuration(999)).toBe('999ms');
  });

  it('switches to seconds at one second', () => {
    expect(formatDuration(1000)).toBe('1.0s');
    expect(formatDuration(59_940)).toBe('59.9s');
  });

  it('switches to minutes at one minute', () => {
    expect(formatDuration(60_000)).toBe('1m 0s');
    expect(formatDuration(90_000)).toBe('1m 30s');
  });
});

describe('summarizeToolInput', () => {
  it('says so when there are no arguments at all', () => {
    expect(summarizeToolInput({})).toBe('no arguments');
  });

  it('renders strings bare and everything else as JSON', () => {
    expect(summarizeToolInput({ count: 3, name: 'Tokyo' })).toBe('count: 3, name: Tokyo');
  });

  it('truncates rather than wrapping, because the header is a glance', () => {
    const summary = summarizeToolInput({ body: 'x'.repeat(200) }, 20);
    expect(summary).toHaveLength(21);
    expect(summary.endsWith('…')).toBe(true);
  });
});

describe('formatRelative', () => {
  const now = Date.UTC(2026, 8, 21, 12, 0, 0);
  const at = (offsetMs: number) => new Date(now + offsetMs).toISOString();

  it('says just now inside a second either way', () => {
    expect(formatRelative(at(0), now)).toBe('just now');
    expect(formatRelative(at(-400), now)).toBe('just now');
  });

  it('counts seconds below a minute, in both directions', () => {
    expect(formatRelative(at(12_000), now)).toBe('in 12s');
    expect(formatRelative(at(-3000), now)).toBe('3s ago');
  });

  it('switches to minutes at a minute', () => {
    expect(formatRelative(at(60_000), now)).toBe('in 1m');
    expect(formatRelative(at(-150_000), now)).toBe('3m ago');
  });
});
