import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { getTransfer, ledgerKeys, type TransferDetail } from 'src/api/ledger';
import useLiveStatus from 'src/hooks/useLiveStatus';
import { pollIntervalFor } from 'src/polling';

/**
 * One transfer with its journal and its attempts.
 *
 * What changes underneath an open drawer is mostly not the status column but
 * the rows *behind* it — a settlement journal appearing, a retry being
 * counted — so the detail is re-read on its own notices (`tasks` events carry
 * the transfer they belong to) and on its own polling schedule when the stream
 * is down.
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
