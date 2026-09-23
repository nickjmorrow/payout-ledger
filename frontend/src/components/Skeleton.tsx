interface Props {
  /** Size and shape: a height and width, matching what will replace it. */
  className?: string;
}

/**
 * A placeholder the shape of the content that will replace it.
 *
 * Shaped rather than a spinner, so the page does not jump when the data lands:
 * a row of skeletons becomes a row of transfers in the same place. Pulses only
 * for people who have not asked for reduced motion, and is hidden from screen
 * readers — `Loading` says what is loading, in words, once.
 */
export default function Skeleton({ className = '' }: Props) {
  return (
    <span
      aria-hidden={'true'}
      className={['block rounded bg-ink/10 motion-safe:animate-pulse', className].join(' ')}
    />
  );
}
