/**
 * Server-Sent Events, parsed by hand. Pure, no React.
 *
 * By hand because `EventSource` cannot send an auth header. Incremental: a frame
 * can be split across chunks, so the caller keeps `rest` for the next one.
 */

export interface SseEvent {
  data: string;
  event: string;
}

/**
 * Every complete event in `buffer`, and whatever is left over.
 *
 * Handles `event:` and `data:` fields, multi-line data, and `:` comment lines
 * (the heartbeat). `id:` and `retry:` are not used here.
 */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  // The spec allows CRLF and bare CR as line endings too.
  const normalised = buffer.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
  const blocks = normalised.split('\n\n');
  // The last block has no terminating blank line yet, so it is not an event.
  const rest = blocks.pop() ?? '';

  const events: SseEvent[] = [];
  for (const block of blocks) {
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line === '' || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      // One optional space after the colon is part of the syntax, not the value.
      const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'event') event = value;
      else if (field === 'data') data.push(value);
    }
    // A block of only comments dispatches nothing.
    if (data.length > 0) events.push({ data: data.join('\n'), event });
  }
  return { events, rest };
}
