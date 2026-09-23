import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ledgerKeys, listDeadLetters, type Task } from 'src/api/ledger';
import usePollInterval from 'src/hooks/usePollInterval';

/** Work that exhausted its retries and is waiting for a person. */
export default function useDeadLetters(): UseQueryResult<Task[]> {
  return useQuery({
    queryFn: listDeadLetters,
    queryKey: ledgerKeys.deadLetters,
    refetchInterval: usePollInterval(),
  });
}
