import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ledgerKeys, listRuns, type PaymentRun } from 'src/api/ledger';
import usePollInterval from 'src/hooks/usePollInterval';

/**
 * Recent payment runs with their progress. Re-read on `transfers` notices, since a
 * run's progress is counted from its transfers.
 */
export default function useRuns(): UseQueryResult<PaymentRun[]> {
  return useQuery({
    queryFn: listRuns,
    queryKey: ledgerKeys.runs,
    refetchInterval: usePollInterval(),
  });
}
