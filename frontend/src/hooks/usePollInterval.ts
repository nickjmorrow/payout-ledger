import useLiveStatus from 'src/hooks/useLiveStatus';
import useTransfers from 'src/hooks/useTransfers';
import { pollIntervalFor } from 'src/polling';

/**
 * The shared refresh cadence, for any view whose numbers the worker can move.
 *
 * Derived from the transfer list and the stream's state, so every view that
 * uses it speeds up, slows down and falls back together. Subscribing to the
 * transfers query costs nothing extra: TanStack dedupes by key.
 */
export default function usePollInterval(): number {
  const { data: transfers } = useTransfers();
  return pollIntervalFor(transfers, useLiveStatus() === 'live');
}
