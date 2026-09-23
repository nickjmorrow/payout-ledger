import { useRef } from 'react';
import JournalCard from 'src/components/JournalCard';
import LoadFailed from 'src/components/LoadFailed';
import Loading from 'src/components/Loading';
import Skeleton from 'src/components/Skeleton';
import StatusPill from 'src/components/StatusPill';
import { formatClock } from 'src/format';
import useDismiss from 'src/hooks/useDismiss';
import useTransfer from 'src/hooks/useTransfer';
import { taskLabel } from 'src/labels';
import { formatMoney } from 'src/money';

interface Props {
  id: string;
  onClose: () => void;
}

/**
 * Everything that happened to one transfer, in a drawer beside the list.
 *
 * Two histories, side by side. The worker's — each send and settlement check,
 * with its attempt count and whatever error it hit — is how a payment took
 * four minutes. The books' — each journal entry, lines and totals — is what
 * that did to the money. A failed transfer shows both the authorisation and
 * the reversal, because the reversal did not delete anything: that is what
 * append-only looks like from the outside.
 *
 * A drawer rather than a page. The list stays visible, so the operator can
 * open the next row without losing their place — and Escape or a click outside
 * closes it, which `useDismiss` owes every overlay.
 */
export default function TransferDetail({ id, onClose }: Props) {
  const ref = useRef<HTMLElement>(null);
  useDismiss(ref, true, onClose);
  const { data: transfer, error, isPending } = useTransfer(id);

  return (
    <aside
      aria-label={'Transfer detail'}
      className={
        'fixed inset-y-0 right-0 z-10 flex w-full max-w-md flex-col border-l border-ink/10 bg-surface shadow-xl'
      }
      ref={ref}
      role={'dialog'}
    >
      <header className={'flex items-center justify-between border-b border-ink/5 px-4 py-3'}>
        <h2 className={'text-sm font-medium text-ink'}>{'Transfer'}</h2>
        <button
          aria-label={'Close'}
          className={'rounded-md p-1 text-ink-muted transition hover:text-ink'}
          onClick={onClose}
          type={'button'}
        >
          <svg
            aria-hidden={'true'}
            className={'h-4 w-4'}
            fill={'none'}
            stroke={'currentColor'}
            strokeLinecap={'round'}
            strokeWidth={2}
            viewBox={'0 0 24 24'}
          >
            <path d={'M6 6l12 12M18 6L6 18'} />
          </svg>
        </button>
      </header>

      <div className={'flex flex-1 flex-col gap-6 overflow-y-auto px-4 py-4'}>
        {isPending && (
          <Loading label={'Loading this transfer'}>
            <div className={'flex flex-col gap-6'}>
              <div className={'grid grid-cols-2 gap-x-4 gap-y-3'}>
                {Array.from({ length: 10 }, (_, cell) => (
                  <Skeleton className={'h-4 w-3/4'} key={cell} />
                ))}
              </div>
              <Skeleton className={'h-20 w-full rounded-lg'} />
              <Skeleton className={'h-40 w-full rounded-lg'} />
            </div>
          </Loading>
        )}

        {error && !transfer && <LoadFailed error={error} what={'this transfer'} />}

        {transfer && (
          <>
            <dl className={'grid grid-cols-2 gap-x-4 gap-y-2 text-sm'}>
              <dt className={'text-xs text-ink-muted'}>{'Recipient'}</dt>
              <dd className={'text-ink'}>{transfer.recipientName}</dd>
              <dt className={'text-xs text-ink-muted'}>{'Amount'}</dt>
              <dd className={'text-ink tabular-nums'}>
                {formatMoney(transfer.amountMinor, transfer.currency)}
              </dd>
              <dt className={'text-xs text-ink-muted'}>{'Status'}</dt>
              <dd>
                <StatusPill status={transfer.status} />
                {transfer.failureReason !== null && (
                  <span className={'ml-2 text-xs text-ink-muted'}>{transfer.failureReason}</span>
                )}
              </dd>
              <dt className={'text-xs text-ink-muted'}>{'Provider ref'}</dt>
              <dd className={'font-mono text-xs text-ink'}>{transfer.providerReference ?? '—'}</dd>
              <dt className={'text-xs text-ink-muted'}>{'Authorized'}</dt>
              <dd className={'text-ink'}>{formatClock(transfer.createdAt)}</dd>
            </dl>

            <section className={'flex flex-col gap-2'}>
              <h3 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
                {'What the worker did'}
              </h3>
              {transfer.tasks.length === 0 ? (
                <p className={'text-sm text-ink-muted'}>{'Nothing yet.'}</p>
              ) : (
                <ol className={'flex flex-col gap-1.5'}>
                  {transfer.tasks.map((task) => (
                    <li
                      className={'flex flex-col gap-0.5 rounded-lg border border-ink/10 px-3 py-2'}
                      key={task.id}
                    >
                      <div className={'flex items-center gap-2'}>
                        <span className={'text-sm text-ink'}>{taskLabel(task.kind)}</span>
                        <StatusPill status={task.status} />
                        <span className={'ml-auto text-xs text-ink-muted tabular-nums'}>
                          {`${String(task.attempts)}/${String(task.maxAttempts)} · ${formatClock(task.updatedAt)}`}
                        </span>
                      </div>
                      {task.error !== null && <p className={'text-xs text-danger'}>{task.error}</p>}
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section className={'flex flex-col gap-2'}>
              <h3 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
                {'What the books say'}
              </h3>
              {transfer.journals.map((journal) => (
                <JournalCard journal={journal} key={journal.id} />
              ))}
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
