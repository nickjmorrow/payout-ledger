import type { Journal } from 'src/api/ledger';
import { formatClock } from 'src/format';
import { accountLabel, journalLabel } from 'src/labels';
import { formatMinor } from 'src/money';

interface Props {
  journal: Journal;
}

/**
 * One posting, laid out the way a bookkeeper would: a debit column and a
 * credit column, one line per account, totals underneath.
 *
 * Both totals are shown even though they are always equal. That is not
 * redundancy — it is the point. A journal is the unit the books balance *at*,
 * and a reader who can see the two columns agree has seen the invariant
 * rather than been told about it.
 */
export default function JournalCard({ journal }: Props) {
  const debits = journal.lines.filter((line) => line.direction === 'debit');
  const credits = journal.lines.filter((line) => line.direction === 'credit');
  const total = (lines: typeof journal.lines) =>
    lines.reduce((sum, line) => sum + line.amountMinor, 0);
  const currency = journal.lines[0]?.currency ?? '';

  return (
    <article className={'overflow-hidden rounded-lg border border-ink/10'}>
      <header className={'flex items-baseline justify-between bg-surface-raised px-3 py-2'}>
        <span className={'text-sm font-medium text-ink'}>{journalLabel(journal.kind)}</span>
        <span className={'text-xs text-ink-muted'}>{formatClock(journal.createdAt)}</span>
      </header>
      {journal.memo !== null && (
        <p className={'border-t border-ink/5 px-3 py-2 text-xs text-ink-muted'}>{journal.memo}</p>
      )}
      <table className={'w-full text-sm'}>
        <thead className={'border-t border-ink/5 text-xs text-ink-muted'}>
          <tr>
            <th className={'px-3 py-1.5 text-left font-medium'}>{'Account'}</th>
            <th className={'px-3 py-1.5 text-right font-medium'}>{`Debit ${currency}`}</th>
            <th className={'px-3 py-1.5 text-right font-medium'}>{`Credit ${currency}`}</th>
          </tr>
        </thead>
        <tbody>
          {journal.lines.map((line) => (
            <tr className={'border-t border-ink/5'} key={line.accountId + line.direction}>
              <td className={'px-3 py-1.5 text-ink'}>{accountLabel(line.accountKind)}</td>
              <td className={'px-3 py-1.5 text-right text-ink tabular-nums'}>
                {line.direction === 'debit' ? formatMinor(line.amountMinor) : ''}
              </td>
              <td className={'px-3 py-1.5 text-right text-ink tabular-nums'}>
                {line.direction === 'credit' ? formatMinor(line.amountMinor) : ''}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot className={'border-t border-ink/10 text-xs font-medium text-ink-muted'}>
          <tr>
            <td className={'px-3 py-1.5'}>{'Balances'}</td>
            <td className={'px-3 py-1.5 text-right tabular-nums'}>{formatMinor(total(debits))}</td>
            <td className={'px-3 py-1.5 text-right tabular-nums'}>{formatMinor(total(credits))}</td>
          </tr>
        </tfoot>
      </table>
    </article>
  );
}
