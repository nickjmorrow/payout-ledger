import { describe, expect, it } from 'vitest';
import { parseSse } from 'src/sse';

describe('parseSse', () => {
  it('reads a named event with JSON data', () => {
    expect(parseSse('event: change\ndata: {"id":"1"}\n\n')).toEqual({
      events: [{ data: '{"id":"1"}', event: 'change' }],
      rest: '',
    });
  });

  it('keeps an unfinished frame for the next chunk', () => {
    const first = parseSse('event: change\ndata: {"id"');
    expect(first.events).toEqual([]);

    const second = parseSse(`${first.rest}:"1"}\n\n`);
    expect(second.events).toEqual([{ data: '{"id":"1"}', event: 'change' }]);
  });

  it('survives a chunk boundary between the two newlines that end a frame', () => {
    const first = parseSse('event: ready\ndata: {}\n');
    expect(first.events).toEqual([]);
    expect(parseSse(`${first.rest}\n`).events).toEqual([{ data: '{}', event: 'ready' }]);
  });

  it('ignores the heartbeat, which is a comment', () => {
    expect(parseSse(': heartbeat\n\n')).toEqual({ events: [], rest: '' });
  });

  it('reads several frames from one chunk, in order', () => {
    const { events } = parseSse('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n');
    expect(events.map((event) => event.event)).toEqual(['a', 'b']);
  });

  it('joins multi-line data and defaults the name to message', () => {
    expect(parseSse('data: one\ndata: two\n\n').events).toEqual([
      { data: 'one\ntwo', event: 'message' },
    ]);
  });

  it('accepts CRLF line endings', () => {
    expect(parseSse('event: ready\r\ndata: {}\r\n\r\n').events).toEqual([
      { data: '{}', event: 'ready' },
    ]);
  });
});
