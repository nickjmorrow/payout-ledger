import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { ledgerKeys, retryDeadLetter } from 'src/api/ledger';

export interface RetryDeadLetter {
  error: Error | null;
  isPending: boolean;
  retry: () => void;
}

/**
 * Retry one dead-lettered task.
 *
 * Unlike the forms that move money, this holds no idempotency key, because
 * the server does not need one: a task that is no longer dead is refused, so
 * pressing the button twice is refused the second time rather than doubled.
 * A refusal for a payment that already finished comes back as the error, in
 * words the operator can act on.
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
