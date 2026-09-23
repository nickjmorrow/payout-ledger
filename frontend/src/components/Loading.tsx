import type { ReactNode } from 'react';

interface Props {
  /** Skeletons shaped like what is on its way. */
  children: ReactNode;
  /** What is loading, for someone who cannot see the shapes: "Loading transfers". */
  label: string;
}

/**
 * A loading region: `aria-busy` plus a visually hidden label, so screen readers
 * skip the skeletons inside.
 */
export default function Loading({ children, label }: Props) {
  return (
    <div aria-busy={'true'}>
      <span className={'sr-only'}>{label}</span>
      {children}
    </div>
  );
}
