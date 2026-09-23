import { useQuery } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { ledgerKeys, listRecipients } from 'src/api/ledger';
import useDisburse from 'src/hooks/useDisburse';
import { parseMajor } from 'src/money';

const CURRENCY = 'USD';

/**
 * Authorise a payment to one recipient.
 *
 * The amount is validated here *and* by the API *and* by a CHECK constraint.
 * That is not redundancy for its own sake: this one is immediate and in the
 * operator's own terms, and the other two are what make it true regardless of
 * who is calling.
 */
export default function DisburseForm() {
  const recipientField = useId();
  const amountField = useId();

  const {
    data: recipients,
    isError,
    isPending: isLoadingRecipients,
  } = useQuery({
    queryFn: listRecipients,
    queryKey: ledgerKeys.recipients,
  });

  const [recipientId, setRecipientId] = useState('');
  const [amount, setAmount] = useState('');
  const { error, isPending, submit } = useDisburse();

  const amountMinor = parseMajor(amount);
  const canSubmit = recipientId !== '' && amountMinor !== null && amountMinor > 0 && !isPending;

  return (
    <form
      className={'flex flex-col gap-3 rounded-xl border border-ink/10 p-4 sm:flex-row sm:items-end'}
      onSubmit={(event) => {
        event.preventDefault();
        // `canSubmit` already proves the amount parsed; the narrowing is what
        // lets TypeScript see it.
        if (amountMinor === null || !canSubmit) return;
        submit(recipientId, amountMinor, CURRENCY);
        setAmount('');
      }}
    >
      <div className={'flex-1'}>
        <label className={'block text-xs text-ink-muted'} htmlFor={recipientField}>
          {'Recipient'}
        </label>
        <select
          aria-busy={isLoadingRecipients}
          className={
            'mt-1 w-full rounded-lg border border-ink/15 bg-surface px-3 py-2 text-sm text-ink disabled:opacity-60'
          }
          // Disabled until there is someone to choose: an empty list that looks
          // ready invites a click that finds nothing.
          disabled={recipients === undefined}
          id={recipientField}
          onChange={(event) => {
            setRecipientId(event.target.value);
          }}
          value={recipientId}
        >
          <option value={''}>
            {isLoadingRecipients
              ? 'Loading recipients…'
              : isError
                ? 'Could not load recipients — retrying'
                : 'Choose someone…'}
          </option>
          {(recipients ?? []).map((recipient) => (
            <option key={recipient.id} value={recipient.id}>
              {`${recipient.fullName} · ${recipient.msisdn}`}
            </option>
          ))}
        </select>
      </div>

      <div className={'sm:w-44'}>
        <label className={'block text-xs text-ink-muted'} htmlFor={amountField}>
          {`Amount (${CURRENCY})`}
        </label>
        <input
          className={
            'mt-1 w-full rounded-lg border border-ink/15 bg-surface px-3 py-2 text-sm text-ink tabular-nums'
          }
          id={amountField}
          inputMode={'decimal'}
          onChange={(event) => {
            setAmount(event.target.value);
          }}
          placeholder={'500.00'}
          value={amount}
        />
      </div>

      <button
        className={
          'rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-accent transition hover:opacity-90 disabled:opacity-40'
        }
        disabled={!canSubmit}
        type={'submit'}
      >
        {isPending ? 'Sending…' : 'Disburse'}
      </button>

      {error && (
        <p className={'text-xs text-danger sm:absolute sm:mt-16'} role={'alert'}>
          {error.message}
        </p>
      )}
    </form>
  );
}
