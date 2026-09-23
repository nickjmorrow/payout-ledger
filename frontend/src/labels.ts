/**
 * What the wire's identifiers mean to a person. Pure, no React.
 *
 * The API speaks in the schema's own words — `program_funding`,
 * `transfer_reversed`, `settle_transfer` — because those are the names the
 * database checks and the logs are grepped for. They are the wrong words for
 * a screen. An operator reading a journal wants "Program fund" beside a
 * debit, not a column name, and the difference between the two vocabularies is
 * exactly one lookup, kept here so no component carries its own copy.
 *
 * Each map falls back to the raw value rather than to nothing: an identifier
 * this file has not heard of is still better shown than hidden, and showing
 * it is how the gap gets noticed.
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
