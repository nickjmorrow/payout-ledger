import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { openEventStream } from 'src/api/events';
import { type ChangeEvent, keysToInvalidate } from 'src/events';

/**
 * Notices are batched for this long, so a burst (a settlement is several, a run
 * dozens) re-reads each query once.
 */
const BATCH_MS = 150;

/**
 * Keeps every query current from the event stream. Mounted once. A notice
 * invalidates what it affects; `ready` invalidates everything, since changes made
 * while the stream was down were never heard.
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
