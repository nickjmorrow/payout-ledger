import type { PaymentRun } from 'src/api/ledger';
import { describeProgress, progressOf, segmentWidths } from 'src/runs';

interface Props {
  run: PaymentRun;
}

/**
 * A run's transfers as one bar: paid, in flight, queued, failed.
 *
 * The same colours as the status pills, so a segment means what a pill of that
 * colour means — in flight is the pending colour, not success, for the reason
 * `StatusPill` gives. Widths animate, so a run being worked through is visibly
 * *moving* rather than jumping between snapshots.
 */
export default function RunProgress({ run }: Props) {
  const progress = progressOf(run.byStatus);
  const widths = segmentWidths(progress);

  return (
    <div className={'flex flex-col gap-1'}>
      <div
        aria-hidden={'true'}
        className={'flex h-1.5 w-full overflow-hidden rounded-full bg-ink/10'}
      >
        <span
          className={'bg-success transition-all duration-500'}
          style={{ width: `${String(widths.paid)}%` }}
        />
        <span
          className={'bg-pending transition-all duration-500'}
          style={{ width: `${String(widths.inFlight)}%` }}
        />
        <span
          className={'bg-ink/25 transition-all duration-500'}
          style={{ width: `${String(widths.queued)}%` }}
        />
        <span
          className={'bg-danger transition-all duration-500'}
          style={{ width: `${String(widths.failed)}%` }}
        />
      </div>
      <span className={'text-xs text-ink-muted tabular-nums'}>{describeProgress(progress)}</span>
    </div>
  );
}
