import { useState } from 'react';
import Column from 'src/components/Column';
import DeadLetterList from 'src/components/DeadLetterList';
import FindingList from 'src/components/FindingList';
import LiveIndicator from 'src/components/LiveIndicator';
import NewDisbursement from 'src/components/NewDisbursement';
import Overview from 'src/components/Overview';
import QueuePanel from 'src/components/QueuePanel';
import RunList from 'src/components/RunList';
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
  // Which payment run the transfer list is narrowed to. Here because two
  // sections share it: choosing a run filters the list below, and creating
  // one selects it so the operator watches the run they just authorised.
  const [runId, setRunId] = useState<null | string>(null);

  return (
    <div className={'flex h-full flex-col overflow-y-auto'}>
      <header className={'border-b border-ink/5'}>
        <Column className={'flex items-center justify-between py-4'}>
          <div>
            <h1 className={'text-sm font-medium tracking-tight text-ink'}>{'Payout Ledger'}</h1>
            <p className={'text-xs text-ink-muted'}>
              {'Unconditional cash transfers · disbursement console'}
            </p>
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
          <NewDisbursement onRunCreated={setRunId} />
        </section>

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'Payment runs'}
          </h2>
          <RunList onSelect={setRunId} selectedId={runId} />
        </section>

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'Disbursements'}
          </h2>
          <TransferList
            onClearRun={() => {
              setRunId(null);
            }}
            runId={runId}
          />
        </section>

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'Reconciliation'}
          </h2>
          <FindingList />
        </section>

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'Queue'}
          </h2>
          <QueuePanel />
        </section>

        <section className={'flex flex-col gap-3'}>
          <h2 className={'text-xs font-medium tracking-wide text-ink-muted uppercase'}>
            {'Dead letters'}
          </h2>
          <DeadLetterList />
        </section>
      </Column>
    </div>
  );
}
