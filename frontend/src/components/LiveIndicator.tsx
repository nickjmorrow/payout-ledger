import useLiveStatus from 'src/hooks/useLiveStatus';

/**
 * Whether the console is live or polling. Offline uses the pending color: nothing
 * is wrong with the money, only with how fast news of it arrives.
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
