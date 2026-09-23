import { useQuery } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { ledgerKeys, listRecipients } from 'src/api/ledger';
import PrimaryButton from 'src/components/PrimaryButton';
import Skeleton from 'src/components/Skeleton';
import TextInput from 'src/components/TextInput';
import useCreateRun from 'src/hooks/useCreateRun';
import { formatMoney, parseMajor } from 'src/money';

const CURRENCY = 'USD';

interface Props {
  /** Called with the new run's id, so the page can show its transfers. */
  onCreated: (runId: string) => void;
}

/**
 * Authorizes the same amount to each of several recipients, as one run. The total
 * is shown before submitting, since it is what the server checks against the fund.
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
          <TextInput
            className={'tabular-nums'}
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
          <TextInput
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
        <PrimaryButton disabled={!canSubmit}>
          {isPending ? 'Authorizing…' : `Authorize ${String(selected.size)} payments`}
        </PrimaryButton>
      </div>

      {error && (
        <p className={'text-sm text-danger'} role={'alert'}>
          {error.message}
        </p>
      )}
    </form>
  );
}
