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
 * The two ways to authorise money: to one person, or to many as one decision.
 *
 * Two forms rather than one that grows a list, because they are not the same
 * request. A one-off is a transfer; a run is all-or-nothing across every
 * recipient, and the operator should know which of those they are about to do
 * before they press the button.
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
