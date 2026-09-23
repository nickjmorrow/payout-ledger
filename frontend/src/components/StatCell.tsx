import Skeleton from 'src/components/Skeleton';

interface Props {
  /** Show a placeholder the size of the figure rather than a dash. */
  isLoading?: boolean;
  label: string;
  /** A semantic colour token when the value needs one, e.g. `text-danger`. */
  tone?: string;
  value: string;
}

/**
 * One figure in a summary row.
 *
 * Loading is a placeholder, not a dash: a dash already means "we could not
 * say", and a balance that reads `—` for the first half-second looks like a
 * fund with nothing in it.
 */
export default function StatCell({ isLoading = false, label, tone, value }: Props) {
  return (
    <div className={'bg-surface-raised px-4 py-3'}>
      <dt className={'text-xs text-ink-muted'}>{label}</dt>
      <dd className={['mt-1 text-sm font-medium tabular-nums', tone ?? 'text-ink'].join(' ')}>
        {isLoading ? <Skeleton className={'my-0.5 h-4 w-24'} /> : value}
      </dd>
    </div>
  );
}
