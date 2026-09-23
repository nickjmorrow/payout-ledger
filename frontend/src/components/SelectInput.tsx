import type { SelectHTMLAttributes } from 'react';

/** A select in the console's one style, dimmed while it has nothing to offer. */
export default function SelectInput({
  className = '',
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={[
        'mt-1 w-full rounded-lg border border-ink/15 bg-surface px-3 py-2 text-sm text-ink disabled:opacity-60',
        className,
      ].join(' ')}
      {...props}
    />
  );
}
