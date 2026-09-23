import RunProgress from 'src/components/RunProgress';
import { formatTime } from 'src/format';
import useRuns from 'src/hooks/useRuns';
import { formatMoney } from 'src/money';

interface Props {
  onSelect: (runId: null | string) => void;
  selectedId: null | string;
}

/**
 * Recent payment runs, each with its progress. Choose one to see its transfers.
 *
 * Choosing the selected run again clears the choice, and the transfer list
 * says which run it is showing, so the filter is never on without the operator
 * being able to see it — a list that silently shows a subset reads as missing
 * payments.
 */
export default function RunList({ onSelect, selectedId }: Props) {
  const { data: runs } = useRuns();

  if (runs === undefined || runs.length === 0) {
    return (
      <p className={'text-sm text-ink-muted'}>
        {'No payment runs yet. Choose "Payment run" above to pay several people at once.'}
      </p>
    );
  }

  return (
    <ul className={'flex flex-col gap-2'}>
      {runs.map((run) => {
        const isSelected = run.id === selectedId;
        return (
          <li key={run.id}>
            <button
              aria-pressed={isSelected}
              className={[
                'flex w-full flex-col gap-2 rounded-lg border px-4 py-3 text-left transition',
                isSelected
                  ? 'border-accent bg-surface-raised'
                  : 'border-ink/10 hover:border-ink/20',
              ].join(' ')}
              onClick={() => {
                onSelect(isSelected ? null : run.id);
              }}
              type={'button'}
            >
              <div className={'flex items-baseline gap-2'}>
                <span className={'text-sm font-medium text-ink'}>
                  {run.memo ?? `Run of ${String(run.count)}`}
                </span>
                <span className={'text-sm text-ink tabular-nums'}>
                  {formatMoney(run.totalMinor, run.currency)}
                </span>
                <span className={'ml-auto text-xs text-ink-muted'}>
                  {formatTime(run.createdAt)}
                </span>
              </div>
              <RunProgress run={run} />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
