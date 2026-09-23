import { useState } from 'react';
import LoadFailed from 'src/components/LoadFailed';
import Loading from 'src/components/Loading';
import Skeleton from 'src/components/Skeleton';
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
 * Disbursements, newest first: all of them, or one run's. The recipient's name is
 * a button that opens the transfer's history.
 */
export default function TransferList({ onClearRun, runId }: Props) {
  const { data: transfers, error, isPending } = useTransfers(runId);
  const [selectedId, setSelectedId] = useState<null | string>(null);

  if (isPending) {
    return (
      <Loading label={'Loading disbursements'}>
        <div className={'flex flex-col gap-3 rounded-xl border border-ink/10 px-4 py-3'}>
          <Skeleton className={'h-3 w-1/3'} />
          {Array.from({ length: 5 }, (_, row) => (
            <Skeleton className={'h-5 w-full'} key={row} />
          ))}
        </div>
      </Loading>
    );
  }

  if (transfers === undefined) {
    return <LoadFailed error={error} what={'disbursements'} />;
  }

  if (transfers.length === 0 && runId === null) {
    return (
      <p
        className={
          'rounded-xl border border-dashed border-ink/15 px-4 py-8 text-center text-sm text-ink-muted'
        }
      >
        {'No disbursements yet. Authorize one above and watch it settle.'}
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
          <span>{`Showing one payment run · ${String(transfers.length)} transfers`}</span>
          <button
            className={'font-medium text-accent hover:underline'}
            onClick={onClearRun}
            type={'button'}
          >
            {'Show all'}
          </button>
        </div>
      )}
      {/* Scrolls sideways on a narrow screen rather than clipping the last column. */}
      <div className={'overflow-x-auto'}>
        <table className={'w-full text-left text-sm whitespace-nowrap'}>
          <thead className={'bg-surface-raised text-xs text-ink-muted'}>
            <tr>
              <th className={'px-4 py-2 font-medium'}>{'Recipient'}</th>
              <th className={'px-4 py-2 text-right font-medium'}>{'Amount'}</th>
              <th className={'px-4 py-2 font-medium'}>{'Status'}</th>
              <th className={'hidden px-4 py-2 font-medium sm:table-cell'}>{'Provider ref'}</th>
              <th className={'px-4 py-2 font-medium'}>{'Started'}</th>
            </tr>
          </thead>
          <tbody>
            {transfers.map((transfer) => (
              <tr
                className={[
                  'border-t border-ink/5',
                  transfer.id === selectedId ? 'bg-surface-raised' : '',
                ].join(' ')}
                key={transfer.id}
              >
                <td className={'px-4 py-2 text-ink'}>
                  <button
                    // Underlined at rest, so it reads as a link.
                    className={
                      'text-left font-medium underline decoration-ink/30 underline-offset-4 transition hover:decoration-ink'
                    }
                    onClick={() => {
                      setSelectedId(transfer.id);
                    }}
                    type={'button'}
                  >
                    {transfer.recipientName}
                  </button>
                </td>
                <td className={'px-4 py-2 text-right text-ink tabular-nums'}>
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
      </div>

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
