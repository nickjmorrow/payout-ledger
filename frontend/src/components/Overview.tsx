import { useQuery } from '@tanstack/react-query';
import { getOverview, ledgerKeys } from 'src/api/ledger';
import StatCell from 'src/components/StatCell';
import useTransfers from 'src/hooks/useTransfers';
import { formatMoney } from 'src/money';
import { pollIntervalFor } from 'src/polling';

/**
 * The programme at a glance: what is left to give away, what is still held at
 * the provider, and whether anything needs a person.
 *
 * One query rather than three, because these numbers are read together — a
 * fund balance from one moment beside a float balance from another reads, for
 * a ledger, as the books not adding up.
 */
export default function Overview() {
  // Subscribed to the transfers query purely to share its cadence: these
  // balances move when the worker settles something in the background, and
  // polling on an independent schedule would show a settled transfer beside a
  // float balance from before it settled. TanStack dedupes by key, so this
  // costs no extra request.
  const { data: transfers } = useTransfers();

  const { data } = useQuery({
    queryFn: getOverview,
    queryKey: ledgerKeys.overview,
    refetchInterval: pollIntervalFor(transfers),
  });

  const fund = data?.accounts.find((account) => account.kind === 'program_funding');
  const float = data?.accounts.find((account) => account.kind === 'provider_settlement');

  // Always zero. Shown rather than hidden because a non-zero value means a
  // database trigger has gone missing, and nothing else in the product would
  // ever say so.
  const areBooksBalanced = data?.trialBalanceMinor === 0;
  const needsAttention = (data?.unresolvedFindings ?? 0) + (data?.deadLettered ?? 0);

  return (
    <dl className={'grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-ink/10 sm:grid-cols-4'}>
      <StatCell
        label={'Available to disburse'}
        value={fund ? formatMoney(fund.balanceMinor, fund.currency) : '—'}
      />
      <StatCell
        label={'Held at provider'}
        value={float ? formatMoney(float.balanceMinor, float.currency) : '—'}
      />
      <StatCell
        label={'Books'}
        tone={data === undefined || areBooksBalanced ? undefined : 'text-danger'}
        value={
          data === undefined
            ? '—'
            : areBooksBalanced
              ? 'Balanced'
              : `Out by ${String(data.trialBalanceMinor)}`
        }
      />
      <StatCell
        label={'Needs attention'}
        tone={needsAttention > 0 ? 'text-pending' : undefined}
        value={data === undefined ? '—' : String(needsAttention)}
      />
    </dl>
  );
}
