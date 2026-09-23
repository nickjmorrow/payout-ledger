import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { getTransfer, ledgerKeys, type TransferDetail } from 'src/api/ledger';
import useLiveStatus from 'src/hooks/useLiveStatus';
import { pollIntervalFor } from 'src/polling';

/**
 * One transfer with its journal and attempts, re-read on its own `transfers` and
 * `tasks` notices.
 */
export default function useTransfer(id: string): UseQueryResult<TransferDetail> {
  const isLive = useLiveStatus() === 'live';
  return useQuery({
    queryFn: () => getTransfer(id),
    queryKey: ledgerKeys.transfer(id),
    refetchInterval: (query) => {
      const detail = query.state.data;
      return pollIntervalFor(detail === undefined ? undefined : [detail], isLive);
    },
  });
}
