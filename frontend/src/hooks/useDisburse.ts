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
 * Sending a disbursement, with the idempotency key handled correctly.
 *
 * **The key is generated once per attempt and kept across retries.** That is
 * the entire point of it: if the first request times out, the browser has no
 * way to know whether the payment was authorised, and retrying with a fresh
 * key would tell the server these are two different disbursements. Same key,
 * and the server replays the first answer instead of paying twice.
 *
 * It is regenerated only on success or on an explicit reset — that is, when
 * the operator is starting a genuinely new disbursement. A `useRef` rather
 * than state because nothing renders differently for it, and re-rendering on
 * every keystroke to hold a uuid would be the wrong trade.
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
      // A new disbursement from here on: this one is done, and reusing the key
      // would replay it forever.
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
