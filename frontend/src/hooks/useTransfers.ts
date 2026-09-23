import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ledgerKeys, listTransfers, type Transfer } from 'src/api/ledger';
import useLiveStatus from 'src/hooks/useLiveStatus';
import { pollIntervalFor } from 'src/polling';

/**
 * Every disbursement, kept current by the event stream, polled when it is down.
 *
 * The interval is computed from this query's own data rather than through
 * `usePollInterval`, which reads this hook — the one place the cadence starts.
 */
export default function useTransfers(): UseQueryResult<Transfer[]> {
  const isLive = useLiveStatus() === 'live';
  return useQuery({
    queryFn: listTransfers,
    queryKey: ledgerKeys.transfers,
    refetchInterval: (query) => pollIntervalFor(query.state.data, isLive),
  });
}
