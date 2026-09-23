import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { getQueue, ledgerKeys, type QueueSnapshot } from 'src/api/ledger';
import usePollInterval from 'src/hooks/usePollInterval';

/**
 * The queue right now. Every claim, retry, finish and enqueue announces a
 * `tasks` notice, so this moves as the workers do.
 */
export default function useQueue(): UseQueryResult<QueueSnapshot> {
  return useQuery({
    queryFn: getQueue,
    queryKey: ledgerKeys.queue,
    refetchInterval: usePollInterval(),
  });
}
