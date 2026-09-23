import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ledgerKeys, listTransfers, type Transfer } from 'src/api/ledger';
import useLiveStatus from 'src/hooks/useLiveStatus';
import { pollIntervalFor } from 'src/polling';

/**
 * Disbursements, newest first: all of them, or one payment run's. Computes its own
 * interval, because `usePollInterval` is derived from this query.
 */
export default function useTransfers(runId: null | string = null): UseQueryResult<Transfer[]> {
  const isLive = useLiveStatus() === 'live';
  return useQuery({
    queryFn: () => listTransfers(runId),
    queryKey: runId === null ? ledgerKeys.transfers : ledgerKeys.transfersInRun(runId),
    refetchInterval: (query) => pollIntervalFor(query.state.data, isLive),
  });
}
