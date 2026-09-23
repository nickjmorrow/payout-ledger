import DeadLetterRow from 'src/components/DeadLetterRow';
import LoadFailed from 'src/components/LoadFailed';
import Loading from 'src/components/Loading';
import Skeleton from 'src/components/Skeleton';
import useDeadLetters from 'src/hooks/useDeadLetters';

/**
 * The dead-letter queue: work that exhausted its retries. A disbursement reverses
 * its transfer before it is parked, so no money is stuck here.
 */
export default function DeadLetterList() {
  const { data: tasks, error, isPending } = useDeadLetters();

  if (isPending) {
    return (
      <Loading label={'Loading dead letters'}>
        <Skeleton className={'h-16 w-full rounded-lg'} />
      </Loading>
    );
  }

  // No data after loading is a failed read, not an empty queue.
  if (tasks === undefined) {
    return <LoadFailed error={error} what={'dead letters'} />;
  }

  if (tasks.length === 0) {
    return <p className={'text-sm text-ink-muted'}>{'Nothing has run out of retries.'}</p>;
  }

  return (
    <ul className={'flex flex-col gap-2'}>
      {tasks.map((task) => (
        <DeadLetterRow key={task.id} task={task} />
      ))}
    </ul>
  );
}
