import { useQuery } from '@tanstack/react-query';
import { getOverview, ledgerKeys } from 'src/api/ledger';
import StatCell from 'src/components/StatCell';
import usePollInterval from 'src/hooks/usePollInterval';
import { formatMoney } from 'src/money';

/**
 * The program at a glance: the fund, the float at the provider, and whether
 * anything needs a person. One query, so the numbers are from one moment.
 */
export default function Overview() {
  // The shared cadence, so these balances refresh with the transfer list.
  const { data, isPending } = useQuery({
    queryFn: getOverview,
    queryKey: ledgerKeys.overview,
    refetchInterval: usePollInterval(),
  });

  const fund = data?.accounts.find((account) => account.kind === 'program_funding');
  const float = data?.accounts.find((account) => account.kind === 'provider_settlement');

  // Always zero. Non-zero means a ledger trigger has gone missing.
  const areBooksBalanced = data?.trialBalanceMinor === 0;
  const needsAttention = (data?.unresolvedFindings ?? 0) + (data?.deadLettered ?? 0);

  return (
    <dl className={'grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-ink/10 sm:grid-cols-4'}>
      <StatCell
        isLoading={isPending}
        label={'Available to disburse'}
        value={fund ? formatMoney(fund.balanceMinor, fund.currency) : '—'}
      />
      <StatCell
        isLoading={isPending}
        label={'Held at provider'}
        value={float ? formatMoney(float.balanceMinor, float.currency) : '—'}
      />
      <StatCell
        isLoading={isPending}
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
        isLoading={isPending}
        label={'Needs attention'}
        tone={needsAttention > 0 ? 'text-pending' : undefined}
        value={data === undefined ? '—' : String(needsAttention)}
      />
    </dl>
  );
}
