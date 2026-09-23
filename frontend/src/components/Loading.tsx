import type { ReactNode } from 'react';

interface Props {
  /** Skeletons shaped like what is on its way. */
  children: ReactNode;
  /** What is loading, for someone who cannot see the shapes: "Loading transfers". */
  label: string;
}

/**
 * A region that is still loading, said once for assistive technology.
 *
 * `aria-busy` tells a screen reader not to announce the region's contents
 * while they are placeholders, and the visually hidden label says what is
 * coming. The skeletons inside carry no meaning of their own.
 */
export default function Loading({ children, label }: Props) {
  return (
    <div aria-busy={'true'}>
      <span className={'sr-only'}>{label}</span>
      {children}
    </div>
  );
}
