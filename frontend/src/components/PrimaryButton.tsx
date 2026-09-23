import type { ButtonHTMLAttributes } from 'react';

/**
 * The one action a form exists for.
 *
 * Disabled is a different fill, not a fainter one: 40% of the accent read as
 * a muted but pressable button in the dark theme, and a button that looks
 * ready and does nothing is worse than one that plainly is not.
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
