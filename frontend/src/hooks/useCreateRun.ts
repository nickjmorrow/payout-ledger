import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef } from 'react';
import { createRun, ledgerKeys, type PaymentRun, type RunRequest } from 'src/api/ledger';

export interface CreateRun {
  error: Error | null;
  isPending: boolean;
  reset: () => void;
  submit: (request: Omit<RunRequest, 'idempotencyKey'>) => void;
}

/**
 * Authorising a payment run, with the idempotency key handled correctly.
 *
 * The same shape as `useDisburse`, deliberately, and for the same reason with
 * more at stake: **one key per attempt, kept across retries.** A run that
 * timed out may or may not have authorised forty payments, and the browser
 * cannot know which. Retrying with the same key gets the first answer back;
 * retrying with a fresh one would authorise forty more.
 *
 * Regenerated only on success or an explicit reset — when the operator is
 * starting a genuinely new run.
 */
export default function useCreateRun(onCreated?: (run: PaymentRun) => void): CreateRun {
  const queryClient = useQueryClient();
  const key = useRef<string>(crypto.randomUUID());

  const mutation = useMutation({
    mutationFn: (request: Omit<RunRequest, 'idempotencyKey'>) =>
      createRun({ ...request, idempotencyKey: key.current }),
    onSuccess: async (run) => {
      key.current = crypto.randomUUID();
      onCreated?.(run);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ledgerKeys.runs }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.transfers }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.overview }),
      ]);
    },
  });

  const submit = useCallback(
    (request: Omit<RunRequest, 'idempotencyKey'>) => {
      mutation.mutate(request);
    },
    [mutation],
  );

  const reset = useCallback(() => {
    key.current = crypto.randomUUID();
    mutation.reset();
  }, [mutation]);

  return { error: mutation.error, isPending: mutation.isPending, reset, submit };
}
