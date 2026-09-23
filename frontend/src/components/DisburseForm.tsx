import { useQuery } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { ledgerKeys, listRecipients } from 'src/api/ledger';
import PrimaryButton from 'src/components/PrimaryButton';
import SelectInput from 'src/components/SelectInput';
import TextInput from 'src/components/TextInput';
import { formatPhone } from 'src/format';
import useDisburse from 'src/hooks/useDisburse';
import { formatMoney, parseMajor } from 'src/money';

const CURRENCY = 'USD';

/**
 * Authorizes a payment to one recipient. The amount is kept until the API accepts
 * it, so a refusal can be corrected rather than retyped.
 */
export default function DisburseForm() {
  const recipientField = useId();
  const amountField = useId();

  const {
    data: recipients,
    isError,
    isPending: isLoadingRecipients,
  } = useQuery({ queryFn: listRecipients, queryKey: ledgerKeys.recipients });

  const [recipientId, setRecipientId] = useState('');
  const [amount, setAmount] = useState('');
  const [confirmation, setConfirmation] = useState<null | string>(null);
  const { error, isPending, submit } = useDisburse((transfer) => {
    setAmount('');
    setConfirmation(
      `Authorized ${formatMoney(transfer.amountMinor, transfer.currency)} for ${transfer.recipientName}.`,
    );
  });

  const amountMinor = parseMajor(amount);
  const canSubmit = recipientId !== '' && amountMinor !== null && amountMinor > 0 && !isPending;

  return (
    <form
      className={'flex flex-col gap-3 rounded-xl border border-ink/10 p-4'}
      onSubmit={(event) => {
        event.preventDefault();
        if (amountMinor === null || !canSubmit) return;
        setConfirmation(null);
        submit(recipientId, amountMinor, CURRENCY);
      }}
    >
      <div className={'flex flex-col gap-3 sm:flex-row sm:items-end'}>
        <div className={'flex-1'}>
          <label className={'block text-xs text-ink-muted'} htmlFor={recipientField}>
            {'Recipient'}
          </label>
          <SelectInput
            aria-busy={isLoadingRecipients}
            disabled={recipients === undefined}
            id={recipientField}
            onChange={(event) => {
              setRecipientId(event.target.value);
              setConfirmation(null);
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
                {`${recipient.fullName} · ${formatPhone(recipient.msisdn)}`}
              </option>
            ))}
          </SelectInput>
        </div>

        <div className={'sm:w-44'}>
          <label className={'block text-xs text-ink-muted'} htmlFor={amountField}>
            {`Amount (${CURRENCY})`}
          </label>
          <TextInput
            className={'tabular-nums'}
            id={amountField}
            inputMode={'decimal'}
            onChange={(event) => {
              setAmount(event.target.value);
              setConfirmation(null);
            }}
            placeholder={'500.00'}
            value={amount}
          />
        </div>

        <PrimaryButton disabled={!canSubmit}>{isPending ? 'Sending…' : 'Disburse'}</PrimaryButton>
      </div>

      {error && (
        <p className={'text-sm text-danger'} role={'alert'}>
          {error.message}
        </p>
      )}
      {confirmation !== null && !error && (
        <p className={'text-sm text-success'} role={'status'}>
          {confirmation}
        </p>
      )}
    </form>
  );
}
