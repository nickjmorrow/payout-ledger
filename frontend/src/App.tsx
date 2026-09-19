import { useQuery } from '@tanstack/react-query';
import { getHealth } from 'src/api/health';
import Column from 'src/components/Column';
import ThemeToggle from 'src/components/ThemeToggle';

/**
 * The shell, until there is something to put in it.
 *
 * What was here — a conversation list, a router, a transcript — belonged to the
 * chat app this was forked from and left with it. The health read below is not
 * a placeholder for its own sake: it exercises the whole path end to end (the
 * Vite proxy, nginx in production, the response envelope, a database round
 * trip), so a broken seam shows up here rather than inside the first real
 * feature built on it.
 */
export default function App() {
  const { data, error } = useQuery({
    queryFn: () => getHealth(),
    queryKey: ['health'],
  });

  return (
    <div className={'flex h-full flex-col'}>
      <header className={'border-b border-ink/5'}>
        <Column className={'flex items-center justify-between py-4'}>
          <h1 className={'text-sm font-medium tracking-tight text-ink'}>Ledger</h1>
          <ThemeToggle />
        </Column>
      </header>

      <Column className={'flex flex-1 flex-col justify-center gap-2'}>
        <p className={'text-sm text-ink'}>
          {error ? 'Could not reach the server.' : `API ${data?.status ?? '…'}`}
        </p>
        <p className={'text-xs text-ink-muted'}>
          {error ? error.message : `Database ${data?.database ?? '…'}`}
        </p>
      </Column>
    </div>
  );
}
