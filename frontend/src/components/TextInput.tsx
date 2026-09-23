import type { InputHTMLAttributes } from 'react';

/** A text field in the console's one style. Every input attribute passes through. */
export default function TextInput({
  className = '',
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={[
        'mt-1 w-full rounded-lg border border-ink/15 bg-surface px-3 py-2 text-sm text-ink',
        className,
      ].join(' ')}
      {...props}
    />
  );
}
