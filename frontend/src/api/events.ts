/**
 * The live connection: one stream of change notices, reconnecting while the page is open. No React.
 *
 * Read with `fetch`, not `EventSource`, so it carries the auth header. The
 * connection state is a small external store, because the whole page shares
 * one stream; `useLiveStatus` is its React side.
 */

import { authHeaders } from 'src/api/auth';
import { type ChangeEvent, isChangeEvent } from 'src/events';
import { parseSse } from 'src/sse';

/**
 * `connecting` before the first answer, `live` once the server says `ready`,
 * `offline` from a failure until a reconnect succeeds.
 */
export type LiveStatus = 'connecting' | 'live' | 'offline';

export interface StreamHandlers {
  onChange: (event: ChangeEvent) => void;
  /** The subscription is live. Re-read everything: changes before now were not heard. */
  onReady: () => void;
}

// Reconnect backoff: doubling, capped.
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 30_000;

/**
 * Silence this long means the connection is dead, whatever the socket says.
 *
 * A proxy can hold a stream open after its upstream dies. The server heartbeats
 * every 10s (`HEARTBEAT_SECONDS` in backend/app/api/routes/events.py); change
 * the two together.
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
  // Closing aborts the fetch or the sleep in progress; either way this sees it.
  for (;;) {
    try {
      await read(handlers, signal, () => {
        delay = RETRY_MIN_MS;
        setStatus('live');
      });
    } catch {
      // Any failure means the same thing: wait, then reconnect.
    }
    if (signal.aborted) return;
    setStatus('offline');
    await sleep(delay, signal);
    delay = Math.min(delay * 2, RETRY_MAX_MS);
  }
}

/**
 * One connection, read until it ends or goes silent. Throws on anything but a clean close.
 *
 * Its own controller, so a stall aborts only this attempt and `run` reconnects.
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
      // Skip a notice this console does not understand; the next `ready` re-reads everything.
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
