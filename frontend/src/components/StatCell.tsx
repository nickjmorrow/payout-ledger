interface Props {
  label: string;
  /** A semantic colour token when the value needs one, e.g. `text-danger`. */
  tone?: string;
  value: string;
}

/** One figure in the programme summary. */
export default function StatCell({ label, tone, value }: Props) {
  return (
    <div className={'bg-surface-raised px-4 py-3'}>
      <dt className={'text-xs text-ink-muted'}>{label}</dt>
      <dd className={['mt-1 text-sm font-medium tabular-nums', tone ?? 'text-ink'].join(' ')}>
        {value}
      </dd>
    </div>
  );
}
