import { useState } from 'react';
import StatusPill from 'src/components/StatusPill';
import TransferDetail from 'src/components/TransferDetail';
import { formatTime } from 'src/format';
import useTransfers from 'src/hooks/useTransfers';
import { formatMoney } from 'src/money';

interface Props {
  /** Show only this payment run's transfers, or everything when null. */
  runId: null | string;
  onClearRun: () => void;
}

/**
 * Disbursements, newest first — all of them, or one run's. Click one for its
 * history.
 *
 * The recipient's name is the control rather than the whole row, because a
 * row is not a button: it cannot take focus, a screen reader will not announce
 * it as something to press, and a click on the status pill to copy a reference
 * would open a drawer instead. One real button per row costs nothing.
 */
export default function TransferList({ onClearRun, runId }: Props) {
  const { data: transfers } = useTransfers(runId);
  const [selectedId, setSelectedId] = useState<null | string>(null);

  if (transfers?.length === 0 && runId === null) {
    return (
      <p
        className={
          'rounded-xl border border-dashed border-ink/15 px-4 py-8 text-center text-sm text-ink-muted'
        }
      >
        {'No disbursements yet. Authorise one above and watch it settle.'}
      </p>
    );
  }

  return (
    <div className={'overflow-hidden rounded-xl border border-ink/10'}>
      {runId !== null && (
        <div
          className={
            'flex items-center justify-between border-b border-ink/5 bg-surface-raised px-4 py-2 text-xs text-ink-muted'
          }
        >
          <span>{`Showing one payment run · ${String(transfers?.length ?? 0)} transfers`}</span>
          <button
            className={'font-medium text-accent hover:underline'}
            onClick={onClearRun}
            type={'button'}
          >
            {'Show all'}
          </button>
        </div>
      )}
      <table className={'w-full text-left text-sm'}>
        <thead className={'bg-surface-raised text-xs text-ink-muted'}>
          <tr>
            <th className={'px-4 py-2 font-medium'}>{'Recipient'}</th>
            <th className={'px-4 py-2 font-medium'}>{'Amount'}</th>
            <th className={'px-4 py-2 font-medium'}>{'Status'}</th>
            <th className={'hidden px-4 py-2 font-medium sm:table-cell'}>{'Provider ref'}</th>
            <th className={'px-4 py-2 font-medium'}>{'Started'}</th>
          </tr>
        </thead>
        <tbody>
          {(transfers ?? []).map((transfer) => (
            <tr
              className={[
                'border-t border-ink/5',
                transfer.id === selectedId ? 'bg-surface-raised' : '',
              ].join(' ')}
              key={transfer.id}
            >
              <td className={'px-4 py-2 text-ink'}>
                <button
                  className={'text-left underline-offset-2 hover:underline'}
                  onClick={() => {
                    setSelectedId(transfer.id);
                  }}
                  type={'button'}
                >
                  {transfer.recipientName}
                </button>
              </td>
              <td className={'px-4 py-2 text-ink tabular-nums'}>
                {formatMoney(transfer.amountMinor, transfer.currency)}
              </td>
              <td className={'px-4 py-2'}>
                <StatusPill status={transfer.status} />
                {transfer.failureReason !== null && (
                  <span className={'ml-2 text-xs text-ink-muted'}>{transfer.failureReason}</span>
                )}
              </td>
              <td className={'hidden px-4 py-2 font-mono text-xs text-ink-muted sm:table-cell'}>
                {transfer.providerReference ?? '—'}
              </td>
              <td className={'px-4 py-2 text-xs text-ink-muted'}>
                {formatTime(transfer.createdAt)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedId !== null && (
        <TransferDetail
          id={selectedId}
          onClose={() => {
            setSelectedId(null);
          }}
        />
      )}
    </div>
  );
}
