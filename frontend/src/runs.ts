/**
 * A payment run's progress, in an operator's words. Pure, no React.
 *
 * The API counts a run's transfers by status; this turns those counts into
 * what a person asks of a run — how many were paid, how many are still moving,
 * did any fail, is it finished — without any of that being stored anywhere to
 * go out of date.
 */

import type { TransferStatus } from 'src/api/ledger';

export interface RunProgress {
  failed: number;
  /** Accepted by the provider, not yet confirmed. */
  inFlight: number;
  /** Nothing further will happen to any of it. */
  isDone: boolean;
  paid: number;
  /** Authorised, not yet handed to the provider. */
  queued: number;
  total: number;
}

export function progressOf(byStatus: Partial<Record<TransferStatus, number>>): RunProgress {
  const paid = byStatus.succeeded ?? 0;
  const failed = byStatus.failed ?? 0;
  const inFlight = byStatus.processing ?? 0;
  const queued = byStatus.pending ?? 0;
  return {
    failed,
    inFlight,
    isDone: inFlight + queued === 0,
    paid,
    queued,
    total: paid + failed + inFlight + queued,
  };
}

/**
 * `"18 of 24 paid · 4 in flight · 2 failed"`.
 *
 * Zero counts are left out rather than shown, so a clean run reads as one
 * clause and a failure is never lost in a list of zeroes.
 */
export function describeProgress(progress: RunProgress): string {
  const clauses: [number, string][] = [
    [progress.inFlight, 'in flight'],
    [progress.queued, 'queued'],
    [progress.failed, 'failed'],
  ];
  return [
    `${String(progress.paid)} of ${String(progress.total)} paid`,
    ...clauses.filter(([count]) => count > 0).map(([count, what]) => `${String(count)} ${what}`),
  ].join(' · ');
}

/** Each segment's share of the bar, as a percentage. Empty for an empty run. */
export function segmentWidths(progress: RunProgress): {
  failed: number;
  inFlight: number;
  paid: number;
  queued: number;
} {
  const share = (n: number) => (progress.total === 0 ? 0 : (n / progress.total) * 100);
  return {
    failed: share(progress.failed),
    inFlight: share(progress.inFlight),
    paid: share(progress.paid),
    queued: share(progress.queued),
  };
}
