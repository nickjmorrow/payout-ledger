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
 * Authorizes a payment run. As in `useDisburse`, one idempotency key per attempt,
 * kept across retries: a run that timed out may have authorized every payment in
 * it, and only the same key gets that answer back instead of authorizing them again.
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
