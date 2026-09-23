/**
 * The disbursement API, as the browser sees it.
 *
 * Amounts are `…Minor` integers everywhere; the name says so.
 */

import { apiFetch } from 'src/api/client';

export type TransferStatus = 'failed' | 'pending' | 'processing' | 'succeeded';

export interface Transfer {
  amountMinor: number;
  createdAt: string;
  currency: string;
  failureReason: null | string;
  id: string;
  providerReference: null | string;
  recipientId: string;
  recipientName: string;
  /** The payment run this was authorized in, or null for a one-off. */
  runId: null | string;
  status: TransferStatus;
  updatedAt: string;
}

/** A payment run with its progress, counted from its transfers on every read. */
export interface PaymentRun {
  byStatus: Partial<Record<TransferStatus, number>>;
  count: number;
  createdAt: string;
  currency: string;
  id: string;
  memo: null | string;
  totalMinor: number;
}

export interface RunRequest {
  currency: string;
  /** One per attempt, reused across retries. See `DisburseRequest`. */
  idempotencyKey: string;
  items: { amountMinor: number; recipientId: string }[];
  memo: null | string;
}

export type TaskStatus = 'failed' | 'pending' | 'running' | 'succeeded';

export interface Task {
  attempts: number;
  claimedBy: null | string;
  createdAt: string;
  error: null | string;
  id: string;
  kind: string;
  maxAttempts: number;
  runAt: string;
  status: TaskStatus;
  transferId: null | string;
  updatedAt: string;
}

export interface Line {
  accountId: string;
  accountKind: string;
  accountName: string;
  amountMinor: number;
  currency: string;
  direction: 'credit' | 'debit';
}

export interface Journal {
  createdAt: string;
  id: string;
  kind: string;
  lines: Line[];
  memo: null | string;
}

/** The queue right now. `due` and `scheduled` are both pending, split on whether they may run yet. */
export interface QueueSnapshot {
  active: Task[];
  dead: number;
  due: number;
  running: number;
  scheduled: number;
}

/** A transfer with its two histories: the books' account and the worker's. */
export interface TransferDetail extends Transfer {
  journals: Journal[];
  tasks: Task[];
}

export interface Recipient {
  country: string;
  enrolledAt: string;
  fullName: string;
  id: string;
  msisdn: string;
}

export interface Account {
  balanceMinor: number;
  currency: string;
  id: string;
  kind: string;
  name: string;
}

export interface Finding {
  createdAt: string;
  detail: string;
  healed: boolean;
  id: string;
  kind: string;
  providerReference: null | string;
  transferId: null | string;
}

export interface Overview {
  accounts: Account[];
  deadLettered: number;
  trialBalanceMinor: number;
  unresolvedFindings: number;
}

export interface DisburseRequest {
  amountMinor: number;
  currency: string;
  /** One per attempt, reused across retries of it. See `useDisburse`. */
  idempotencyKey: string;
  recipientId: string;
}

/** Query keys, hierarchical: `['transfers']` covers the list and every `['transfers', id]`. */
export const ledgerKeys = {
  /** Under `tasks`, so every task notice reaches it. */
  deadLetters: ['tasks', 'dead'] as const,
  findings: ['findings'] as const,
  overview: ['overview'] as const,
  /** Under `tasks`, so every task notice reaches it. */
  queue: ['tasks', 'queue'] as const,
  recipients: ['recipients'] as const,
  runs: ['runs'] as const,
  tasks: ['tasks'] as const,
  transfer: (id: string) => ['transfers', id] as const,
  transfers: ['transfers'] as const,
  /** Under `transfers`, so a change to any transfer reaches the filtered list too. */
  transfersInRun: (runId: string) => ['transfers', 'run', runId] as const,
};

export function getOverview(): Promise<Overview> {
  return apiFetch<Overview>('/overview');
}

export function listTransfers(runId?: null | string): Promise<Transfer[]> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : '';
  return apiFetch<Transfer[]>(`/transfers${query}`);
}

export function listRuns(): Promise<PaymentRun[]> {
  return apiFetch<PaymentRun[]>('/runs');
}

export function createRun({
  currency,
  idempotencyKey,
  items,
  memo,
}: RunRequest): Promise<PaymentRun> {
  return apiFetch<PaymentRun>('/runs', {
    body: JSON.stringify({ currency, items, memo }),
    headers: { 'Idempotency-Key': idempotencyKey },
    method: 'POST',
  });
}

export function getTransfer(id: string): Promise<TransferDetail> {
  return apiFetch<TransferDetail>(`/transfers/${id}`);
}

export function listRecipients(): Promise<Recipient[]> {
  return apiFetch<Recipient[]>('/recipients');
}

export function listFindings(): Promise<Finding[]> {
  return apiFetch<Finding[]>('/findings');
}

export function disburse({
  amountMinor,
  currency,
  idempotencyKey,
  recipientId,
}: DisburseRequest): Promise<Transfer> {
  return apiFetch<Transfer>('/transfers', {
    body: JSON.stringify({ amountMinor, currency, recipientId }),
    headers: { 'Idempotency-Key': idempotencyKey },
    method: 'POST',
  });
}

export function getQueue(): Promise<QueueSnapshot> {
  return apiFetch<QueueSnapshot>('/queue');
}

export function listDeadLetters(): Promise<Task[]> {
  return apiFetch<Task[]>('/dead-letters');
}

/** Put a dead-lettered task back on the queue. A repeat is refused by the server, not doubled. */
export function retryDeadLetter(taskId: string): Promise<Task> {
  return apiFetch<Task>(`/dead-letters/${taskId}/retry`, { method: 'POST' });
}
