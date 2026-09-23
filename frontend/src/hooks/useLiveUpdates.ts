import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { openEventStream } from 'src/api/events';
import { type ChangeEvent, keysToInvalidate } from 'src/events';

/**
 * How long to gather notices before acting on them.
 *
 * A single settlement is two or three notices — the transfer, the task that
 * settled it, the next one queued — and a payment run is dozens. Refreshing on
 * each would re-read the same list many times a second; gathering for a beat
 * reads it once, and every view refreshes in the same moment.
 */
const BATCH_MS = 150;

/**
 * Keep every query current by listening for changes rather than asking.
 *
 * Mounted once, at the top of the page. A notice invalidates the queries it
 * affects and TanStack re-reads whichever of them are on screen; the rest are
 * marked stale and re-read when they next appear. On `ready` — every connect,
 * reconnects included — everything is invalidated, because whatever changed
 * while the stream was down was never heard.
 */
export default function useLiveUpdates(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    let pending: ChangeEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | undefined;

    const flush = () => {
      timer = undefined;
      const keys = keysToInvalidate(pending);
      pending = [];
      for (const queryKey of keys) void queryClient.invalidateQueries({ queryKey });
    };

    const close = openEventStream({
      onChange: (event) => {
        pending.push(event);
        timer ??= setTimeout(flush, BATCH_MS);
      },
      onReady: () => {
        void queryClient.invalidateQueries();
      },
    });

    return () => {
      close();
      clearTimeout(timer);
    };
  }, [queryClient]);
}
