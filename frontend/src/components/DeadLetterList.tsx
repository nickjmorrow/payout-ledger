import DeadLetterRow from 'src/components/DeadLetterRow';
import useDeadLetters from 'src/hooks/useDeadLetters';

/**
 * The dead-letter queue: work that exhausted its retries, parked for a person.
 *
 * Not money that is stuck. A disbursement that runs out of attempts reverses
 * its transfer before it is parked, so what is here is the evidence of why —
 * and a Retry for the cases where running it again can still help.
 */
export default function DeadLetterList() {
  const { data: tasks } = useDeadLetters();

  if (tasks === undefined || tasks.length === 0) {
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
