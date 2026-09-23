/**
 * Schema identifiers as words: `program_funding` -> "Program fund". Pure, no React.
 *
 * Unknown identifiers fall through unchanged, so a gap is visible rather than hidden.
 */

const ACCOUNT_KINDS: Record<string, string> = {
  program_funding: 'Program fund',
  provider_settlement: 'Provider float',
  recipient_payable: 'Owed to recipient',
};

const JOURNAL_KINDS: Record<string, string> = {
  funding_deposit: 'Funded',
  transfer_authorized: 'Authorized',
  transfer_reversed: 'Reversed',
  transfer_settled: 'Settled',
};

const TASK_KINDS: Record<string, string> = {
  disburse_transfer: 'Send payment',
  reconcile: 'Reconcile',
  settle_transfer: 'Check settlement',
};

/** `program_funding` -> `"Program fund"`. */
export function accountLabel(kind: string): string {
  return ACCOUNT_KINDS[kind] ?? kind;
}

/** `transfer_authorized` -> `"Authorized"`. */
export function journalLabel(kind: string): string {
  return JOURNAL_KINDS[kind] ?? kind;
}

/** `settle_transfer` -> `"Check settlement"`. */
export function taskLabel(kind: string): string {
  return TASK_KINDS[kind] ?? kind;
}
