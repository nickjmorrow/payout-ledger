import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { getTransfer, ledgerKeys, type TransferDetail } from 'src/api/ledger';
import { pollIntervalFor } from 'src/polling';

/**
 * One transfer with its journal and its attempts, polled at the shared cadence
 * while it is still moving.
 *
 * The list query already knows this transfer's status, but the detail is
 * re-read on its own schedule anyway: what changes underneath an open drawer
 * is not the status column but the rows *behind* it — a settlement journal
 * appearing, a retry being counted — and those are not in the list.
 */
export default function useTransfer(id: string): UseQueryResult<TransferDetail> {
  return useQuery({
    queryFn: () => getTransfer(id),
    queryKey: ledgerKeys.transfer(id),
    refetchInterval: (query) => {
      const detail = query.state.data;
      return pollIntervalFor(detail === undefined ? undefined : [detail]);
    },
  });
}
