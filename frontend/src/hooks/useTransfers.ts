import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ledgerKeys, listTransfers, type Transfer } from 'src/api/ledger';
import { pollIntervalFor } from 'src/polling';

/**
 * Every disbursement, polled at the shared cadence.
 *
 * A hook rather than a `useQuery` in each component so that both the table and
 * the balances above it derive their interval from the same data. TanStack
 * dedupes by key, so two callers are still one request.
 */
export default function useTransfers(): UseQueryResult<Transfer[]> {
  return useQuery({
    queryFn: listTransfers,
    queryKey: ledgerKeys.transfers,
    refetchInterval: (query) => pollIntervalFor(query.state.data),
  });
}
