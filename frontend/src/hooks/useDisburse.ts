import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef } from 'react';
import { disburse, ledgerKeys, type Transfer } from 'src/api/ledger';

export interface Disburse {
  error: Error | null;
  isPending: boolean;
  reset: () => void;
  submit: (recipientId: string, amountMinor: number, currency: string) => void;
}

/**
 * Sends a disbursement with one idempotency key per attempt, kept across retries
 * and replaced only on success or reset. A fresh key per retry would let a timed-out
 * request be paid twice. See AGENTS.md > Idempotency.
 */
export default function useDisburse(onDisbursed?: (transfer: Transfer) => void): Disburse {
  const queryClient = useQueryClient();
  const key = useRef<string>(crypto.randomUUID());

  const mutation = useMutation({
    mutationFn: ({
      amountMinor,
      currency,
      recipientId,
    }: {
      amountMinor: number;
      currency: string;
      recipientId: string;
    }) => disburse({ amountMinor, currency, idempotencyKey: key.current, recipientId }),
    onSuccess: async (transfer) => {
      // Done: reusing the key would replay this one forever.
      key.current = crypto.randomUUID();
      onDisbursed?.(transfer);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ledgerKeys.transfers }),
        queryClient.invalidateQueries({ queryKey: ledgerKeys.overview }),
      ]);
    },
  });

  const submit = useCallback(
    (recipientId: string, amountMinor: number, currency: string) => {
      mutation.mutate({ amountMinor, currency, recipientId });
    },
    [mutation],
  );

  const reset = useCallback(() => {
    key.current = crypto.randomUUID();
    mutation.reset();
  }, [mutation]);

  return { error: mutation.error, isPending: mutation.isPending, reset, submit };
}
