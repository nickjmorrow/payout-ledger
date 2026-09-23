import type { TaskStatus, TransferStatus } from 'src/api/ledger';

interface Props {
  status: TaskStatus | TransferStatus;
}

/**
 * A status, colored by meaning. `processing` and `running` use the pending color:
 * the money has not arrived yet.
 */
const TONE: Record<TaskStatus | TransferStatus, string> = {
  failed: 'bg-danger/10 text-danger',
  pending: 'bg-ink/10 text-ink-muted',
  processing: 'bg-pending/15 text-pending',
  running: 'bg-pending/15 text-pending',
  succeeded: 'bg-success/15 text-success',
};

export default function StatusPill({ status }: Props) {
  return (
    <span
      className={[
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize',
        TONE[status],
      ].join(' ')}
    >
      {status}
    </span>
  );
}
