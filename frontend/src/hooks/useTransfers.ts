import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ledgerKeys, listTransfers, type Transfer } from 'src/api/ledger';
import useLiveStatus from 'src/hooks/useLiveStatus';
import { pollIntervalFor } from 'src/polling';

/**
 * Disbursements, newest first — all of them, or the ones in one payment run.
 * Kept current by the event stream, polled when it is down.
 *
 * The interval is computed from this query's own data rather than through
 * `usePollInterval`, which reads this hook — the one place the cadence starts.
 */
export default function useTransfers(runId: null | string = null): UseQueryResult<Transfer[]> {
  const isLive = useLiveStatus() === 'live';
  return useQuery({
    queryFn: () => listTransfers(runId),
    queryKey: runId === null ? ledgerKeys.transfers : ledgerKeys.transfersInRun(runId),
    refetchInterval: (query) => pollIntervalFor(query.state.data, isLive),
  });
}
