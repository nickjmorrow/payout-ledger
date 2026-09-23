/**
 * The live connection: one stream of change notices, reconnecting for as long
 * as the page is open. No React.
 *
 * Read with `fetch` rather than `EventSource` so the request carries the auth
 * header like every other one — see `sse.ts` for why that decides it.
 *
 * The connection state is a tiny external store rather than React state,
 * because the one stream is shared by the whole page: the hook that opens it,
 * the indicator in the header, and every query deciding how often to poll all
 * read the same answer. `useSyncExternalStore` is the React side of it.
 */

import { authHeaders } from 'src/api/auth';
import { type ChangeEvent, isChangeEvent } from 'src/events';
import { parseSse } from 'src/sse';

/**
 * `connecting` before the first answer, `live` once the server has said
 * `ready`, and `offline` from the first failure until a reconnect succeeds.
 * Not `live` on the socket opening — see `ready` in `backend/app/api/routes/
 * events.py` for why only the server can say when listening has started.
 */
export type LiveStatus = 'connecting' | 'live' | 'offline';

export interface StreamHandlers {
  onChange: (event: ChangeEvent) => void;
  /** The subscription is live. Re-read everything: changes before now were not heard. */
  onReady: () => void;
}

// Doubling, capped. A backend restarting takes a second or two; one that is
// down for longer should not be asked forty times a minute by every tab.
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 30_000;

/**
 * Silence this long means the connection is dead, whatever the socket says.
 *
 * A stream can die without closing. The one that found this was Vite's dev
 * proxy, which keeps the browser's side open after the backend behind it has
 * gone — so the console said Live while hearing nothing, and would have
 * forever. A laptop waking from sleep and a NAT dropping an idle mapping look
 * the same. The server sends a heartbeat every `HEARTBEAT_SECONDS` (10, in
 * `backend/app/api/routes/events.py`), so two and a half of those missed is
 * not a quiet moment, it is a corpse.
 */
const STALL_MS = 25_000;

let status: LiveStatus = 'connecting';
const listeners = new Set<() => void>();

export function getLiveStatus(): LiveStatus {
  return status;
}

export function subscribeLiveStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function setStatus(next: LiveStatus): void {
  if (next === status) return;
  status = next;
  for (const listener of listeners) listener();
}

/** Open the stream and keep it open. Returns the function that closes it for good. */
export function openEventStream(handlers: StreamHandlers): () => void {
  const controller = new AbortController();
  void run(handlers, controller.signal);
  return () => {
    controller.abort();
  };
}

async function run(handlers: StreamHandlers, signal: AbortSignal): Promise<void> {
  let delay = RETRY_MIN_MS;
  // One exit, checked after each attempt. Closing aborts the fetch in flight or
  // the sleep in progress; either way the attempt ends and this sees it.
  for (;;) {
    try {
      await read(handlers, signal, () => {
        delay = RETRY_MIN_MS;
        setStatus('live');
      });
    } catch {
      // A refused connection, a proxy timeout, a server restart. All of them
      // mean the same thing here: wait, then try again.
    }
    if (signal.aborted) return;
    setStatus('offline');
    await sleep(delay, signal);
    delay = Math.min(delay * 2, RETRY_MAX_MS);
  }
}

/**
 * One connection, read until it ends or goes silent. Throws on anything but a
 * clean close.
 *
 * Aborted by its own controller rather than the caller's: closing the page
 * aborts both, but a stall aborts only this attempt, and the loop in `run`
 * then reconnects.
 */
async function read(
  handlers: StreamHandlers,
  signal: AbortSignal,
  onLive: () => void,
): Promise<void> {
  const connection = new AbortController();
  const abort = () => {
    connection.abort();
  };
  signal.addEventListener('abort', abort, { once: true });
  let watchdog = setTimeout(abort, STALL_MS);

  try {
    const response = await fetch('/api/events', {
      headers: { Accept: 'text/event-stream', ...(await authHeaders()) },
      signal: connection.signal,
    });
    if (!response.ok || response.body === null) {
      throw new Error(`event stream refused: ${String(response.status)}`);
    }

    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;

      // Any bytes at all — a heartbeat included — prove the line is alive.
      clearTimeout(watchdog);
      watchdog = setTimeout(abort, STALL_MS);

      buffer = dispatch(buffer + value, handlers, onLive);
    }
  } finally {
    clearTimeout(watchdog);
    signal.removeEventListener('abort', abort);
  }
}

/** Hand every complete event in `buffer` to its handler; return the remainder. */
function dispatch(buffer: string, handlers: StreamHandlers, onLive: () => void): string {
  const { events, rest } = parseSse(buffer);
  for (const event of events) {
    if (event.event === 'ready') {
      onLive();
      handlers.onReady();
    } else if (event.event === 'change') {
      const data: unknown = JSON.parse(event.data);
      // A notice this console does not understand is skipped, not fatal: the
      // next `ready` re-reads everything anyway.
      if (isChangeEvent(data)) handlers.onChange(data);
    }
  }
  return rest;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}
