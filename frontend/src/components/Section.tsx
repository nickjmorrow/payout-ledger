import { type ReactNode, useId } from 'react';

interface Props {
  children: ReactNode;
  title: string;
}

/** One titled part of the console. The heading names the region for assistive technology too. */
export default function Section({ children, title }: Props) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={'flex flex-col gap-3'}>
      <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'} id={headingId}>
        {title}
      </h2>
      {children}
    </section>
  );
}
