"""ORM models.

**This file is the source of truth for the schema.** Alembic autogenerates
migrations by diffing these classes against the database, so anything not
declared here does not exist: a CHECK constraint left out is a CHECK constraint
dropped, silently, on the next `alembic revision --autogenerate`.

That is why the constraints below are spelled out rather than left to the
service layer to enforce. The application validates too — better errors, closer
to the user — but the database is the thing that cannot be bypassed by a
migration script, a psql session, or the next process someone writes.

`onupdate=func.now()` on the `updated_at` columns is a third thing again, and
it is the one that was missing: without it the column records when a row was
*created* and never moves, so a transfer that went pending → processing →
succeeded still showed its original timestamp. SQLAlchemy emits it on any
flushed UPDATE; a raw SQL update still has to set the column itself.

Note `default=` AND `server_default=` on several columns. They are not
redundant: `default=` is applied by SQLAlchemy when the ORM inserts a row, and
`server_default=` is what the column actually has in Postgres. Declare only the
first and every insert that does not go through the ORM — a psql session, a
fixture, the raw SQL in task_service — hits a NOT NULL column with no default.

**Money is `BigInteger` minor units — cents, not dollars.** Never a float, and
never `Numeric` here: a float cannot represent 0.10 and a ledger that cannot
add up its own rows exactly is not a ledger. `amount_minor` is always positive;
which way it moves is `direction`, not a sign, so a query can sum debits and
credits separately without a CASE.

**States are `Text` plus a CHECK, not a Postgres ENUM.** Adding a value to an
ENUM is a migration that cannot run inside a transaction with other DDL on some
versions, and removing one is a table rewrite. A CHECK is one `ALTER` either
way, and the set of values is readable in this file rather than in
`pg_catalog`.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

# N811: `UUID` is a class, not a constant — pep8-naming cannot tell.
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# The accounts a disbursement moves money between, and what kind of balance
# each one naturally carries. Spelled out because the direction of a posting is
# only correct relative to these: an asset goes up on a debit and a fund
# balance goes up on a credit, so "debit the funding account" means *reducing*
# the money available to give away.
ACCOUNT_KINDS = (
    # The pool of donated money this programme has to give away. Equity: it
    # goes down when a transfer is authorised.
    "program_funding",
    # What we owe one recipient, from the moment a transfer is authorised until
    # the provider confirms they were paid. A liability, and the account that
    # makes the in-flight state visible in the books rather than only in a
    # status column.
    "recipient_payable",
    # Our cash balance held at the mobile-money provider. An asset: it goes
    # down when they actually pay somebody.
    "provider_settlement",
)


class Task(Base):
    """One unit of work a worker will pick up.

    Mutable, unlike a ledger entry — this is the state of work in progress, not
    a record of what happened. See `services/task_service.py` for why Postgres
    is the queue.

    `kind` has no CHECK constraint listing its values, deliberately. The chat
    template had two kinds and could afford to enumerate them; a task kind here
    is a handler registration in `worker/handlers.py`, and a constraint that has
    to be migrated every time one is added is a constraint people route around.
    The registry is the enforcement, and an unknown kind fails its own task
    rather than the insert.
    """

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The request that enqueued this, so the worker's logs can be joined to the
    # API's. Null for anything the worker enqueued itself.
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed')",
            name="tasks_status_check",
        ),
        # Partial, because the claim query only ever looks at pending rows.
        # Without postgresql_where this becomes a full index over every task
        # that has ever run, which is the opposite of the point.
        Index("tasks_claim_idx", "run_at", postgresql_where=text("status = 'pending'")),
        Index("tasks_kind_idx", "kind", desc("created_at")),
        # A transfer's history is every task that named it in the payload. An
        # expression index rather than a `transfer_id` column, because a task
        # is generic work and most kinds — reconcile, above all — are about no
        # transfer at all; a column that is null for them would be a column
        # that lies about what a task is.
        Index("tasks_transfer_idx", text("(payload ->> 'transfer_id')")),
    )


class Recipient(Base):
    """A person the programme sends money to."""

    __tablename__ = "recipients"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    # The mobile-money number the provider pays out to, in E.164. Unique
    # because two recipient records for one number is how the same person gets
    # enrolled twice and paid twice — a deduplication problem that is far
    # cheaper to refuse here than to reconcile afterwards.
    msisdn: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # ISO 3166-1 alpha-2.
    country: Mapped[str] = mapped_column(Text, nullable=False)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("char_length(country) = 2", name="recipients_country_check"),
        CheckConstraint("msisdn ~ '^\\+[1-9][0-9]{6,14}$'", name="recipients_msisdn_check"),
    )


class Account(Base):
    """One place money can sit. Balances are derived, never stored.

    There is deliberately no `balance` column. A stored balance is a second
    source of truth that can disagree with the entries, and the entire reason
    for double-entry bookkeeping is to have exactly one. The balance of an
    account is `sum(credits) - sum(debits)` over `ledger_entries`, computed
    when asked — see `services/ledger_service.balance`.

    That is a real performance trade and the honest answer is that it is fine
    until it is not: the fix is a rollup table maintained in the same
    transaction as the entries, which is a cache rather than a second truth.
    Do not add a mutable `balance` column here.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # ISO 4217. An account holds exactly one currency; a transfer between
    # currencies is two postings and an explicit FX leg, never one entry that
    # quietly changes denomination.
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    # Set for `recipient_payable` and null for every other kind — enforced by
    # the CHECK below rather than by whoever inserts the row.
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("recipients.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    recipient: Mapped["Recipient | None"] = relationship()

    __table_args__ = (
        CheckConstraint(
            f"kind in ({', '.join(repr(k) for k in ACCOUNT_KINDS)})",
            name="accounts_kind_check",
        ),
        CheckConstraint("char_length(currency) = 3", name="accounts_currency_check"),
        # A recipient account must name its recipient, and no other kind may.
        # Without this a payable row with a null recipient is orphaned money
        # that reconciliation cannot attribute to anybody.
        CheckConstraint(
            "(kind = 'recipient_payable') = (recipient_id is not null)",
            name="accounts_recipient_kind_check",
        ),
        # One payable account per recipient per currency. The partial index is
        # what makes this expressible at all: a plain UNIQUE would also forbid
        # a second `program_funding` account, since every one of those has a
        # null recipient_id.
        Index(
            "accounts_recipient_currency_idx",
            "recipient_id",
            "currency",
            unique=True,
            postgresql_where=text("recipient_id is not null"),
        ),
        # Exactly one funding account and one settlement account per currency.
        # Without this `ledger_service.system_account` is ill-defined — it would
        # have to pick one of several and would pick a different one depending
        # on the plan, which is the kind of bug that only shows up once the
        # books are already wrong.
        Index(
            "accounts_system_kind_currency_idx",
            "kind",
            "currency",
            unique=True,
            postgresql_where=text("recipient_id is null"),
        ),
    )


class JournalEntry(Base):
    """One balanced set of postings. The unit the books balance *at*.

    Every movement of money is a journal entry with two or more lines that sum
    to zero, rather than an update to a balance somewhere. That is what makes
    the books self-checking: if debits and credits ever stop agreeing you have
    a bug and you find out immediately, instead of discovering a silent
    miscalculation months later with no way to tell when it started.

    The balance rule is enforced by a **deferred constraint trigger** on
    `ledger_entries`, not here and not in Python. Deferred because it has to
    be: the lines are inserted one at a time and a journal is unbalanced
    between the first INSERT and the last, so an immediate check would reject
    every correct posting. Deferring to COMMIT is the only point at which the
    question "does this journal balance" is even well-formed. See the
    migration for the trigger body.
    """

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    # What this posting represents. Read the values as a narrative of a
    # transfer's life: authorised, then settled, or reversed if it failed.
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("transfers.id", ondelete="RESTRICT"), nullable=True
    )
    # Free-text, for a human reading the books.
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lines: Mapped[list["LedgerEntry"]] = relationship(back_populates="journal_entry")

    __table_args__ = (
        CheckConstraint(
            "kind in ('funding_deposit', 'transfer_authorized', 'transfer_settled',"
            " 'transfer_reversed')",
            name="journal_entries_kind_check",
        ),
        Index("journal_entries_transfer_idx", "transfer_id"),
    )


class LedgerEntry(Base):
    """One line of a journal entry: this account, this direction, this amount.

    **Append-only.** Nothing updates or deletes a row here, and a trigger in
    the migration refuses both rather than trusting everyone to remember. A
    mistake is corrected by posting a reversing journal entry, which leaves
    both the error and the correction in the history — which is the whole point
    of keeping books this way. An UPDATE would leave neither.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    # Always positive; `direction` carries the sign. Minor units.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    journal_entry: Mapped["JournalEntry"] = relationship(back_populates="lines")
    account: Mapped["Account"] = relationship()

    __table_args__ = (
        CheckConstraint("direction in ('debit', 'credit')", name="ledger_entries_direction_check"),
        # Zero is not a posting, and a negative amount is a debit written as a
        # credit — both are mistakes that would otherwise still balance.
        CheckConstraint("amount_minor > 0", name="ledger_entries_amount_check"),
        CheckConstraint("char_length(currency) = 3", name="ledger_entries_currency_check"),
        Index("ledger_entries_journal_idx", "journal_entry_id"),
        # The balance query is "every line for this account, newest first", so
        # this is the index that keeps it off a sequential scan.
        Index("ledger_entries_account_idx", "account_id", desc("created_at")),
    )


class PaymentRun(Base):
    """Many disbursements, authorised together as one decision.

    **There is no status column, and no total.** A run's progress is the
    statuses of its transfers and its total is the sum of their amounts, both
    counted when asked — the same reasoning that keeps a balance off
    `accounts`. A stored status would be a second record of what the transfers
    already say, and the first time a settlement updated one and not the
    other, the console would show a run as complete with payments still in
    flight.

    What the row does hold is the one thing the transfers cannot: that these
    payments were decided together.
    """

    __tablename__ = "payment_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    # Free text for a person: "September cycle, Siaya county".
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("char_length(currency) = 3", name="payment_runs_currency_check"),
        Index("payment_runs_created_idx", desc("created_at")),
    )


class Transfer(Base):
    """One disbursement to one recipient: the domain object above the books.

    A transfer is not the money moving — the journal entries are. This row is
    the *intent* and its progress, which is why it has a mutable status and the
    entries do not. Keeping the two separate is what lets a transfer fail and
    be retried without the books ever showing a payment that did not happen.
    """

    __tablename__ = "transfers"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("recipients.id", ondelete="RESTRICT"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    # What the provider calls this payment. Null until we have successfully
    # handed it over, and unique because two of our transfers pointing at one
    # provider payment means we paid once and recorded twice.
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The run this was authorised as part of, if any. Null for a one-off.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("payment_runs.id", ondelete="RESTRICT", name="transfers_run_id_fkey"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    recipient: Mapped["Recipient"] = relationship()

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="transfers_amount_check"),
        CheckConstraint("char_length(currency) = 3", name="transfers_currency_check"),
        CheckConstraint(
            "status in ('pending', 'processing', 'succeeded', 'failed')",
            name="transfers_status_check",
        ),
        Index("transfers_status_idx", "status", desc("created_at")),
        Index("transfers_recipient_idx", "recipient_id", desc("created_at")),
        # A run's progress is a GROUP BY over this.
        Index("transfers_run_idx", "run_id"),
    )


class IdempotencyKey(Base):
    """A client's promise that two identical requests are one request.

    The problem this solves is that retries are mandatory — networks drop
    responses, users double-click, providers re-send webhooks — and a retried
    disbursement without this is a second payment. The client sends a key; the
    first request to arrive with it does the work and stores its response here;
    every later request with the same key gets that stored response back
    instead of doing the work again.

    `request_fingerprint` is what stops the key from being a way to *hide* a
    second payment: reusing a key with a different body is a client bug, and
    returning the first response would silently discard the second request. It
    is refused instead. See `services/idempotency_service`.
    """

    __tablename__ = "idempotency_keys"

    # The key itself is the primary key. There is no surrogate id, because the
    # uniqueness IS the feature: two concurrent requests race to INSERT and
    # exactly one wins, which is the lock, for free, with no extra round trip.
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    # Scoped per endpoint so a key reused against a different operation cannot
    # replay an unrelated response.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    # Null while the first request is still in flight. A second request that
    # arrives in that window is told to retry rather than served a half-answer.
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("transfers.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("key", "endpoint", name="idempotency_keys_key_endpoint_key"),
        Index("idempotency_keys_created_idx", desc("created_at")),
    )


class ProviderPayment(Base):
    """The mobile-money provider's record of a payment. **Not our books.**

    This table stands in for a system we do not own. In production it is an
    HTTP API at somebody else's company; here it is a table so the whole thing
    runs with `docker compose up`. Everything else in the schema is ours and is
    written by our services — this one is written *only* by
    `provider/mock.py`, pretending to be them.

    Keeping it a separate table rather than columns on `transfers` is what
    makes reconciliation a real comparison. Two independent records of the same
    payment can disagree, and finding out that they have is the entire job of
    the reconciliation pass. Fold this into `transfers` and reconciliation
    becomes a query that compares a row to itself and always passes.

    `idempotency_key` is theirs, not ours: a real provider dedupes on a key we
    supply, which is what makes it safe for the worker to retry a payment it is
    not sure landed. That is the whole reason at-least-once delivery is
    tolerable.
    """

    __tablename__ = "provider_payments"

    # Their identifier, which we store on the transfer as `provider_reference`.
    reference: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    msisdn: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'succeeded', 'failed')", name="provider_payments_status_check"
        ),
        CheckConstraint("amount_minor > 0", name="provider_payments_amount_check"),
        # Reconciliation sweeps by time, so this is the index it runs on.
        Index("provider_payments_created_idx", desc("created_at")),
    )


class ReconciliationFinding(Base):
    """A disagreement between our books and the provider's records.

    Written by the reconciliation pass and never by anything else. A finding is
    a *fact about a moment* — it is not updated when the underlying problem is
    fixed, because the point of keeping it is to be able to answer "what did we
    know, and when" long after the fix. A later pass that finds the same
    problem writes another row; a later pass that does not simply writes
    nothing, and the absence is the record of the resolution.

    `kind` says which way the disagreement runs, and they are not
    interchangeable — see `services/reconciliation_service` for what each one
    means and why only two of them are safe to heal automatically.
    """

    __tablename__ = "reconciliation_findings"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("transfers.id", ondelete="SET NULL"), nullable=True
    )
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What a person needs in order to decide what to do. Written for whoever is
    # reading it at 3am with no other context.
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    # True when the pass repaired it rather than only reporting it.
    healed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind in ('missing_at_provider', 'unknown_to_us', 'status_behind',"
            " 'status_contradicted', 'amount_mismatch')",
            name="reconciliation_findings_kind_check",
        ),
        Index("reconciliation_findings_created_idx", desc("created_at")),
    )
