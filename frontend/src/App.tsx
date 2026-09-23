import { useState } from 'react';
import Column from 'src/components/Column';
import DeadLetterList from 'src/components/DeadLetterList';
import FindingList from 'src/components/FindingList';
import LiveIndicator from 'src/components/LiveIndicator';
import NewDisbursement from 'src/components/NewDisbursement';
import Overview from 'src/components/Overview';
import QueuePanel from 'src/components/QueuePanel';
import RunList from 'src/components/RunList';
import Section from 'src/components/Section';
import ThemeToggle from 'src/components/ThemeToggle';
import TransferList from 'src/components/TransferList';
import useLiveUpdates from 'src/hooks/useLiveUpdates';

/**
 * The disbursement console. One page, so an operator is shown a problem rather
 * than having to know where to look for it.
 */
export default function App() {
  // Once, for the whole page: one stream, and every query kept current by it.
  useLiveUpdates();
  // The run the transfer list is narrowed to. Creating a run selects it.
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

        <Section title={'New disbursement'}>
          <NewDisbursement onRunCreated={setRunId} />
        </Section>

        <Section title={'Payment runs'}>
          <RunList onSelect={setRunId} selectedId={runId} />
        </Section>

        <Section title={'Disbursements'}>
          <TransferList
            onClearRun={() => {
              setRunId(null);
            }}
            runId={runId}
          />
        </Section>

        <Section title={'Reconciliation'}>
          <FindingList />
        </Section>

        <Section title={'Queue'}>
          <QueuePanel />
        </Section>

        <Section title={'Dead letters'}>
          <DeadLetterList />
        </Section>
      </Column>
    </div>
  );
}
