import Column from 'src/components/Column';
import DisburseForm from 'src/components/DisburseForm';
import FindingList from 'src/components/FindingList';
import LiveIndicator from 'src/components/LiveIndicator';
import Overview from 'src/components/Overview';
import ThemeToggle from 'src/components/ThemeToggle';
import TransferList from 'src/components/TransferList';
import useLiveUpdates from 'src/hooks/useLiveUpdates';

/**
 * The disbursement console.
 *
 * One page on purpose. The whole job is: see what the programme has, send some
 * of it to somebody, watch it land, and be told when the books and the
 * provider stop agreeing. Splitting that across routes would mean an operator
 * had to know where to look for a problem, when the point is to be shown it.
 */
export default function App() {
  // Once, for the whole page: one stream, and every query kept current by it.
  useLiveUpdates();

  return (
    <div className={'flex h-full flex-col overflow-y-auto'}>
      <header className={'border-b border-ink/5'}>
        <Column className={'flex items-center justify-between py-4'}>
          <div>
            <h1 className={'text-sm font-medium tracking-tight text-ink'}>
              {'Unconditional cash transfers'}
            </h1>
            <p className={'text-xs text-ink-muted'}>{'Disbursement console'}</p>
          </div>
          <div className={'flex items-center gap-4'}>
            <LiveIndicator />
            <ThemeToggle />
          </div>
        </Column>
      </header>

      <Column className={'flex flex-col gap-8 py-8'}>
        <Overview />

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'New disbursement'}
          </h2>
          <DisburseForm />
        </section>

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'Disbursements'}
          </h2>
          <TransferList />
        </section>

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'Reconciliation'}
          </h2>
          <FindingList />
        </section>
      </Column>
    </div>
  );
}
