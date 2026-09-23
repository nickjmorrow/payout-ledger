import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { ledgerKeys, retryDeadLetter } from 'src/api/ledger';

export interface RetryDeadLetter {
  error: Error | null;
  isPending: boolean;
  retry: () => void;
}

/**
 * Retries one dead-lettered task. No idempotency key: the server refuses a task
 * that is no longer dead, so a double press is refused rather than doubled.
 */
export default function useRetryDeadLetter(taskId: string): RetryDeadLetter {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => retryDeadLetter(taskId),
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ledgerKeys.tasks }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.overview }),
      ]);
    },
  });

  const retry = useCallback(() => {
    mutation.mutate();
  }, [mutation]);

  return { error: mutation.error, isPending: mutation.isPending, retry };
}
