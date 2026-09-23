/**
 * Server-Sent Events, parsed by hand. Pure, no React.
 *
 * By hand rather than with `EventSource`, because `EventSource` cannot send a
 * header. The auth seam (`api/auth.ts`) puts a bearer token on every request,
 * and a stream that could not carry one would be the single endpoint that
 * stopped working the day `OIDC_ISSUER` was set. `fetch` can send the header;
 * this turns what it reads back into events.
 *
 * Incremental: bytes arrive in arbitrary chunks, so a frame can be split
 * anywhere — mid-line, mid-field, between the two newlines that end it. The
 * caller keeps `rest` and prepends it to the next chunk.
 */

export interface SseEvent {
  data: string;
  event: string;
}

/**
 * Every complete event in `buffer`, and whatever is left over.
 *
 * Follows the parts of the spec this server uses: `event:` and `data:` fields,
 * multi-line data joined with a newline, `:` comment lines ignored (the
 * heartbeat is one), and `message` as the default event name. `id:` and
 * `retry:` are not used here and are skipped rather than half-implemented.
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
