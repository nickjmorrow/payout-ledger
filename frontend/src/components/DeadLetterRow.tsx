import type { Task } from 'src/api/ledger';
import { formatClock } from 'src/format';
import useRetryDeadLetter from 'src/hooks/useRetryDeadLetter';
import { taskLabel } from 'src/labels';

interface Props {
  task: Task;
}

/**
 * One dead-lettered task and a Retry. Each row holds its own retry state, so a
 * refusal shows under the task it refers to.
 */
export default function DeadLetterRow({ task }: Props) {
  const { error, isPending, retry } = useRetryDeadLetter(task.id);

  return (
    <li className={'flex flex-col gap-1 rounded-lg border border-ink/10 px-4 py-3'}>
      <div className={'flex items-center gap-2'}>
        <span className={'text-sm text-ink'}>{taskLabel(task.kind)}</span>
        <span className={'text-xs text-ink-muted tabular-nums'}>
          {`${String(task.attempts)}/${String(task.maxAttempts)} attempts · ${formatClock(task.updatedAt)}`}
        </span>
        <button
          className={
            'ml-auto rounded-md border border-ink/15 px-2.5 py-1 text-xs font-medium text-ink transition hover:bg-surface-raised disabled:opacity-40'
          }
          disabled={isPending}
          onClick={retry}
          type={'button'}
        >
          {isPending ? 'Retrying…' : 'Retry'}
        </button>
      </div>
      {task.error !== null && (
        <p className={'line-clamp-2 font-mono text-xs break-all text-danger'} title={task.error}>
          {task.error}
        </p>
      )}
      {error && (
        <p className={'text-xs text-ink-muted'} role={'alert'}>
          {error.message}
        </p>
      )}
    </li>
  );
}
