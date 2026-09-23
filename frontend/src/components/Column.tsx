import type { ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Spacing for this row. The width is not the caller's to set. */
  className?: string;
}

/**
 * The reading column, shared by the header and the body so their edges agree.
 * Full-bleed elements go outside it.
 */
export default function Column({ children, className = '' }: Props) {
  return <div className={['mx-auto w-full max-w-5xl px-4', className].join(' ')}>{children}</div>;
}
