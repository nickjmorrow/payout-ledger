import useLiveStatus from 'src/hooks/useLiveStatus';
import useTransfers from 'src/hooks/useTransfers';
import { pollIntervalFor } from 'src/polling';

/**
 * The shared refresh cadence for any view the worker can change, so every view
 * refreshes together. See AGENTS.md > Frontend.
 */
export default function usePollInterval(): number {
  const { data: transfers } = useTransfers();
  return pollIntervalFor(transfers, useLiveStatus() === 'live');
}
