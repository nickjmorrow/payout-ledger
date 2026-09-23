import useLiveStatus from 'src/hooks/useLiveStatus';

/**
 * Whether what is on screen is current, said once, in the corner.
 *
 * Worth showing because the two states behave differently in a way an operator
 * would otherwise have to guess at: live, a settlement appears the moment the
 * worker commits it; offline, the console falls back to polling and it can
 * take a few seconds. `offline` is the pending colour rather than danger —
 * nothing is wrong with the money, only with how quickly news of it arrives.
 */
const APPEARANCE = {
  connecting: { dot: 'bg-ink/30', label: 'Connecting…', text: 'text-ink-muted' },
  live: { dot: 'bg-success', label: 'Live', text: 'text-ink-muted' },
  offline: { dot: 'bg-pending', label: 'Reconnecting…', text: 'text-pending' },
} as const;

export default function LiveIndicator() {
  const status = useLiveStatus();
  const { dot, label, text } = APPEARANCE[status];

  return (
    <span
      className={['flex items-center gap-1.5 text-xs', text].join(' ')}
      role={'status'}
      title={
        status === 'live'
          ? 'Changes appear as they happen.'
          : 'Not connected to live updates; refreshing on a timer until it reconnects.'
      }
    >
      <span aria-hidden={'true'} className={['h-1.5 w-1.5 rounded-full', dot].join(' ')} />
      {label}
    </span>
  );
}
