import type { TransferStatus } from 'src/api/ledger';

interface Props {
  status: TransferStatus;
}

/**
 * A transfer's state, coloured by what it means rather than by its name.
 *
 * `processing` reads as pending and not as success, deliberately: the money has
 * been promised and has not arrived, and showing that in the same colour as
 * `succeeded` would tell an operator the payment landed when it has not.
 */
const TONE: Record<TransferStatus, string> = {
  failed: 'bg-danger/10 text-danger',
  pending: 'bg-ink/10 text-ink-muted',
  processing: 'bg-pending/15 text-pending',
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
