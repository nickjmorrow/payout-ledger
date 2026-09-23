import { useState } from 'react';
import DisburseForm from 'src/components/DisburseForm';
import RunForm from 'src/components/RunForm';

type Mode = 'one' | 'run';

const MODES: { label: string; value: Mode }[] = [
  { label: 'One recipient', value: 'one' },
  { label: 'Payment run', value: 'run' },
];

interface Props {
  onRunCreated: (runId: string) => void;
}

/**
 * Pay one recipient, or authorize a payment run to many as one all-or-nothing
 * decision. Two forms, because they are different requests.
 */
export default function NewDisbursement({ onRunCreated }: Props) {
  const [mode, setMode] = useState<Mode>('one');

  return (
    <div className={'flex flex-col gap-3'}>
      <div
        aria-label={'Disbursement type'}
        className={'flex w-fit items-center gap-0.5 rounded-lg border border-ink/10 p-0.5'}
        role={'group'}
      >
        {MODES.map((option) => {
          const isActive = option.value === mode;
          return (
            <button
              aria-pressed={isActive}
              className={[
                'rounded-md px-3 py-1 text-xs font-medium transition',
                isActive ? 'bg-surface-raised text-ink' : 'text-ink-muted hover:text-ink',
              ].join(' ')}
              key={option.value}
              onClick={() => {
                setMode(option.value);
              }}
              type={'button'}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      {mode === 'one' ? <DisburseForm /> : <RunForm onCreated={onRunCreated} />}
    </div>
  );
}
