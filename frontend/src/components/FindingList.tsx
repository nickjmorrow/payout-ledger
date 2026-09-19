import { useQuery } from '@tanstack/react-query';
import { listFindings } from 'src/api/ledger';
import { formatTime } from 'src/format';

/**
 * What reconciliation found, and whether it fixed it.
 *
 * Healed findings stay on the list rather than disappearing. A finding is a
 * fact about a moment, not a ticket — what it records is that the two systems
 * disagreed, and that remains true after the disagreement is resolved. An
 * operator asking "has this been drifting all week" needs the healed ones to
 * still be there.
 */
export default function FindingList() {
  const { data: findings } = useQuery({
    queryFn: listFindings,
    queryKey: ['findings'],
    refetchInterval: 15_000,
  });

  if (findings === undefined || findings.length === 0) {
    return (
      <p className={'text-sm text-ink-muted'}>
        {'Nothing to report — the books and the provider agree.'}
      </p>
    );
  }

  return (
    <ul className={'flex flex-col gap-2'}>
      {findings.map((finding) => (
        <li className={'rounded-lg border border-ink/10 px-4 py-3'} key={finding.id}>
          <div className={'flex items-center gap-2'}>
            <span className={'font-mono text-xs text-ink'}>{finding.kind}</span>
            <span
              className={[
                'rounded-full px-2 py-0.5 text-xs font-medium',
                finding.healed ? 'bg-success/15 text-success' : 'bg-pending/15 text-pending',
              ].join(' ')}
            >
              {finding.healed ? 'healed' : 'needs a person'}
            </span>
            <span className={'ml-auto text-xs text-ink-muted'}>
              {formatTime(finding.createdAt)}
            </span>
          </div>
          <p className={'mt-1 text-sm text-ink-muted'}>{finding.detail}</p>
        </li>
      ))}
    </ul>
  );
}
