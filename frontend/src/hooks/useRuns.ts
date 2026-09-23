import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ledgerKeys, listRuns, type PaymentRun } from 'src/api/ledger';
import usePollInterval from 'src/hooks/usePollInterval';

/**
 * Recent payment runs with their progress, at the shared cadence.
 *
 * A run's progress moves whenever one of its transfers does, so this is
 * re-read on every `transfers` notice (see `events.ts`) — not on a topic of
 * its own, because nothing on a run itself ever changes.
 */
export default function useRuns(): UseQueryResult<PaymentRun[]> {
  return useQuery({
    queryFn: listRuns,
    queryKey: ledgerKeys.runs,
    refetchInterval: usePollInterval(),
  });
}
