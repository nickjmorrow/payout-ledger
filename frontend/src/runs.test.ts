import { describe, expect, it } from 'vitest';
import { describeProgress, progressOf, segmentWidths } from 'src/runs';

describe('progressOf', () => {
  it('counts each status and knows when nothing more will happen', () => {
    const done = progressOf({ failed: 1, succeeded: 3 });
    expect(done).toMatchObject({ failed: 1, paid: 3, total: 4 });
    expect(done.isDone).toBe(true);
  });

  it('is not done while anything is queued or in flight', () => {
    expect(progressOf({ pending: 1, succeeded: 3 }).isDone).toBe(false);
    expect(progressOf({ processing: 1 }).isDone).toBe(false);
  });

  it('treats a missing status as none of them', () => {
    expect(progressOf({}).total).toBe(0);
  });
});

describe('describeProgress', () => {
  it('reads as one clause when the run went cleanly', () => {
    expect(describeProgress(progressOf({ succeeded: 24 }))).toBe('24 of 24 paid');
  });

  it('names every state that is not zero, failures last', () => {
    expect(
      describeProgress(progressOf({ failed: 2, pending: 1, processing: 3, succeeded: 18 })),
    ).toBe('18 of 24 paid · 3 in flight · 1 queued · 2 failed');
  });
});

describe('segmentWidths', () => {
  it('splits the bar by share of the run', () => {
    expect(segmentWidths(progressOf({ failed: 1, succeeded: 3 }))).toEqual({
      failed: 25,
      inFlight: 0,
      paid: 75,
      queued: 0,
    });
  });

  it('draws nothing for an empty run rather than dividing by zero', () => {
    expect(segmentWidths(progressOf({}))).toEqual({ failed: 0, inFlight: 0, paid: 0, queued: 0 });
  });
});
