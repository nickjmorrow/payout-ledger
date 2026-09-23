import type { ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Spacing for this row. The width is not the caller's to set. */
  className?: string;
}

/**
 * The reading column: one width, shared by everything stacked in it.
 *
 * The header and the page body are separate subtrees, and they have to be —
 * the scroll container has to span the whole pane so that its scrollbar lands
 * at the edge of the window rather than hard against the text. That leaves two
 * places which must agree on where the text starts and stops, and a component
 * is the cheapest way to make disagreeing impossible.
 *
 * Anything full-bleed — the rule under the header — goes OUTSIDE it. Anything that bleeds slightly past the text, like the hover
 * background of an icon button sitting in the margin, goes inside and lives in
 * the padding, which is also what stops the scroll container clipping it.
 */
export default function Column({ children, className = '' }: Props) {
  return <div className={['mx-auto w-full max-w-5xl px-4', className].join(' ')}>{children}</div>;
}
