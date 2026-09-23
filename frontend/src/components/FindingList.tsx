import { useQuery } from '@tanstack/react-query';
import { ledgerKeys, listFindings } from 'src/api/ledger';
import LoadFailed from 'src/components/LoadFailed';
import Loading from 'src/components/Loading';
import Skeleton from 'src/components/Skeleton';
import { formatTime } from 'src/format';
import usePollInterval from 'src/hooks/usePollInterval';

/**
 * What reconciliation found, and whether it healed it. Healed findings stay: each
 * is a fact about a moment. See AGENTS.md > Reconciliation.
 */
export default function FindingList() {
  const {
    data: findings,
    error,
    isPending,
  } = useQuery({
    queryFn: listFindings,
    queryKey: ledgerKeys.findings,
    refetchInterval: usePollInterval(),
  });

  if (isPending) {
    return (
      <Loading label={'Loading reconciliation findings'}>
        <div className={'flex flex-col gap-2'}>
          <Skeleton className={'h-16 w-full rounded-lg'} />
          <Skeleton className={'h-16 w-full rounded-lg'} />
        </div>
      </Loading>
    );
  }

  // No data after loading is a failed read, not "nothing to report".
  if (findings === undefined) {
    return <LoadFailed error={error} what={'reconciliation findings'} />;
  }

  if (findings.length === 0) {
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
