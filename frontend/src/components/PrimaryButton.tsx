import type { ButtonHTMLAttributes } from 'react';

/**
 * A form's submit button. Disabled is a different fill, not a fainter one, so it
 * never looks pressable.
 */
export default function PrimaryButton({
  className = '',
  type = 'submit',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={[
        'rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-accent transition enabled:hover:opacity-90',
        'disabled:cursor-not-allowed disabled:bg-ink/10 disabled:text-ink-muted',
        className,
      ].join(' ')}
      type={type}
      {...props}
    />
  );
}
