import { useQuery } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { ledgerKeys, listRecipients } from 'src/api/ledger';
import Skeleton from 'src/components/Skeleton';
import useCreateRun from 'src/hooks/useCreateRun';
import { formatMoney, parseMajor } from 'src/money';

const CURRENCY = 'USD';

interface Props {
  /** Called with the new run's id, so the page can show its transfers. */
  onCreated: (runId: string) => void;
}

/**
 * Authorise one payment to each of several recipients, as one decision.
 *
 * The same amount to everyone, because that is what an unconditional cash
 * transfer programme does: a fixed sum per household per cycle. The total is
 * shown before submitting, in the operator's terms, since it is the number the
 * server will check against the fund — and refuse the whole run over, not the
 * recipient who tipped it.
 */
export default function RunForm({ onCreated }: Props) {
  const amountField = useId();
  const memoField = useId();

  const {
    data: recipients,
    isError,
    isPending: isLoadingRecipients,
  } = useQuery({
    queryFn: listRecipients,
    queryKey: ledgerKeys.recipients,
  });

  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [amount, setAmount] = useState('500.00');
  const [memo, setMemo] = useState('');
  const { error, isPending, submit } = useCreateRun((run) => {
    setSelected(new Set());
    setMemo('');
    onCreated(run.id);
  });

  const everyone = recipients ?? [];
  const isAllSelected = everyone.length > 0 && selected.size === everyone.length;
  const amountMinor = parseMajor(amount);
  const totalMinor = amountMinor === null ? null : amountMinor * selected.size;
  const canSubmit = selected.size > 0 && amountMinor !== null && amountMinor > 0 && !isPending;

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <form
      className={'flex flex-col gap-4 rounded-xl border border-ink/10 p-4'}
      onSubmit={(event) => {
        event.preventDefault();
        if (amountMinor === null || !canSubmit) return;
        submit({
          currency: CURRENCY,
          items: [...selected].map((recipientId) => ({ amountMinor, recipientId })),
          memo: memo.trim() === '' ? null : memo.trim(),
        });
      }}
    >
      <fieldset className={'flex flex-col gap-2'}>
        <legend className={'flex w-full items-center justify-between text-xs text-ink-muted'}>
          <span>
            {isLoadingRecipients
              ? 'Recipients · loading…'
              : isError && recipients === undefined
                ? 'Recipients · could not load — retrying'
                : `Recipients · ${String(selected.size)} of ${String(everyone.length)}`}
          </span>
        </legend>
        <label className={'flex items-center gap-2 text-sm text-ink'}>
          <input
            checked={isAllSelected}
            className={'accent-accent'}
            disabled={everyone.length === 0}
            onChange={() => {
              setSelected(isAllSelected ? new Set() : new Set(everyone.map((r) => r.id)));
            }}
            type={'checkbox'}
          />
          {'Everyone enrolled'}
        </label>
        <div
          aria-busy={isLoadingRecipients}
          className={
            'grid max-h-48 grid-cols-1 gap-x-4 gap-y-1 overflow-y-auto rounded-lg border border-ink/10 p-2 sm:grid-cols-2'
          }
        >
          {isLoadingRecipients &&
            Array.from({ length: 8 }, (_, row) => (
              <Skeleton className={'my-1 h-4 w-3/4'} key={row} />
            ))}
          {everyone.map((recipient) => (
            <label className={'flex items-center gap-2 text-sm text-ink'} key={recipient.id}>
              <input
                checked={selected.has(recipient.id)}
                className={'accent-accent'}
                onChange={() => {
                  toggle(recipient.id);
                }}
                type={'checkbox'}
              />
              {recipient.fullName}
            </label>
          ))}
        </div>
      </fieldset>

      <div className={'flex flex-col gap-3 sm:flex-row sm:items-end'}>
        <div className={'sm:w-44'}>
          <label className={'block text-xs text-ink-muted'} htmlFor={amountField}>
            {`Amount each (${CURRENCY})`}
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
            value={amount}
          />
        </div>
        <div className={'flex-1'}>
          <label className={'block text-xs text-ink-muted'} htmlFor={memoField}>
            {'Memo (optional)'}
          </label>
          <input
            className={
              'mt-1 w-full rounded-lg border border-ink/15 bg-surface px-3 py-2 text-sm text-ink'
            }
            id={memoField}
            maxLength={200}
            onChange={(event) => {
              setMemo(event.target.value);
            }}
            placeholder={'September cycle'}
            value={memo}
          />
        </div>
      </div>

      <div className={'flex items-center justify-between gap-3'}>
        <span className={'text-sm text-ink-muted tabular-nums'}>
          {totalMinor === null || selected.size === 0
            ? 'Choose recipients and an amount'
            : `Total ${formatMoney(totalMinor, CURRENCY)}`}
        </span>
        <button
          className={
            'rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-accent transition hover:opacity-90 disabled:opacity-40'
          }
          disabled={!canSubmit}
          type={'submit'}
        >
          {isPending ? 'Authorizing…' : `Authorize ${String(selected.size)} payments`}
        </button>
      </div>

      {error && (
        <p className={'text-xs text-danger'} role={'alert'}>
          {error.message}
        </p>
      )}
    </form>
  );
}
