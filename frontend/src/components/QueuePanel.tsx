import LoadFailed from 'src/components/LoadFailed';
import Loading from 'src/components/Loading';
import Skeleton from 'src/components/Skeleton';
import StatCell from 'src/components/StatCell';
import StatusPill from 'src/components/StatusPill';
import useNow from 'src/hooks/useNow';
import useQueue from 'src/hooks/useQueue';
import { taskLabel } from 'src/labels';
import { timingOf } from 'src/queue';

/**
 * What the workers have to do, as they do it.
 *
 * The counts split pending work on whether it may run yet, because a dozen
 * settlement checks scheduled a few seconds out is a healthy queue and would
 * look like a backlog if lumped in with work that is late. Below them, every
 * unfinished task with the worker holding it or the time it will run — so a
 * payment run shows as sends being claimed by two workers at once, and the
 * reconciliation pass shows as the one task that is always a few seconds away.
 */
export default function QueuePanel() {
  const { data: queue, error, isPending } = useQueue();
  const now = useNow();

  return (
    <div className={'flex flex-col gap-3'}>
      <dl className={'grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-ink/10 sm:grid-cols-4'}>
        <StatCell isLoading={isPending} label={'Due'} value={queue ? String(queue.due) : '—'} />
        <StatCell
          isLoading={isPending}
          label={'Scheduled'}
          value={queue ? String(queue.scheduled) : '—'}
        />
        <StatCell
          isLoading={isPending}
          label={'Running'}
          tone={queue && queue.running > 0 ? 'text-pending' : undefined}
          value={queue ? String(queue.running) : '—'}
        />
        <StatCell
          isLoading={isPending}
          label={'Dead letters'}
          tone={queue && queue.dead > 0 ? 'text-danger' : undefined}
          value={queue ? String(queue.dead) : '—'}
        />
      </dl>

      {isPending && (
        <Loading label={'Loading the queue'}>
          <div className={'flex flex-col gap-2 rounded-xl border border-ink/10 px-4 py-3'}>
            <Skeleton className={'h-4 w-2/3'} />
            <Skeleton className={'h-4 w-1/2'} />
          </div>
        </Loading>
      )}
      {error && !queue && <LoadFailed error={error} what={'the queue'} />}

      {queue && queue.active.length > 0 && (
        <ol className={'flex flex-col overflow-hidden rounded-xl border border-ink/10'}>
          {queue.active.map((task) => (
            <li
              className={'flex items-center gap-3 border-t border-ink/5 px-4 py-2 first:border-t-0'}
              key={task.id}
            >
              <span className={'text-sm text-ink'}>{taskLabel(task.kind)}</span>
              <StatusPill status={task.status} />
              {task.attempts > 0 && (
                <span className={'text-xs text-ink-muted tabular-nums'}>
                  {`attempt ${String(task.attempts)}/${String(task.maxAttempts)}`}
                </span>
              )}
              <span className={'ml-auto font-mono text-xs text-ink-muted'}>
                {timingOf(task, now)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
