"""ORM models: the source of truth for the schema.

Alembic autogenerates migrations from these classes, so every constraint is
declared here; one left out is one the next `--autogenerate` drops. Money is
`BigInteger` minor units, always positive, with `direction` carrying the sign.
States are `Text` plus a CHECK rather than a Postgres ENUM, which is awkward to
change. `default=` covers ORM inserts and `server_default=` covers everything
else, such as the raw SQL in `task_service`.
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


# Direction is only correct relative to an account's kind; see NATURAL_DIRECTION
# in ledger_service.
ACCOUNT_KINDS = (
    # Equity: the money this program has to give away.
    "program_funding",
    # Liability: owed to one recipient between authorization and settlement.
    "recipient_payable",
    # Asset: our balance held at the payment provider.
    "provider_settlement",
)


class Task(Base):
    """One unit of work for a worker. Mutable: this is work in progress, not history.

    `kind` has no CHECK: kinds are handler registrations in `worker/handlers.py`,
    and an unknown one fails its own task rather than the insert.
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
    # The request that enqueued this, re-bound by the worker so one grep finds
    # both halves of a job. Null for work the worker enqueued itself.
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
        # Partial: the claim query only ever reads pending rows.
        Index("tasks_claim_idx", "run_at", postgresql_where=text("status = 'pending'")),
        Index("tasks_kind_idx", "kind", desc("created_at")),
        # An expression index rather than a column, because most task kinds are
        # about no transfer at all.
        Index("tasks_transfer_idx", text("(payload ->> 'transfer_id')")),
    )


class Recipient(Base):
    """A person the program sends money to."""

    __tablename__ = "recipients"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    # E.164. Unique, because one number enrolled twice is one person paid twice.
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
    """One place money can sit.

    No `balance` column: balances are derived from the entries. See AGENTS.md >
    Balances are derived, never stored.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # ISO 4217. One currency per account; moving between currencies is an FX leg.
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    # Set for `recipient_payable` and null otherwise, per the CHECK below.
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
        CheckConstraint(
            "(kind = 'recipient_payable') = (recipient_id is not null)",
            name="accounts_recipient_kind_check",
        ),
        # One payable per recipient per currency. Partial, because a plain
        # UNIQUE would also collide every system account's null recipient.
        Index(
            "accounts_recipient_currency_idx",
            "recipient_id",
            "currency",
            unique=True,
            postgresql_where=text("recipient_id is not null"),
        ),
        # One fund and one float per currency, so `system_account` is well-defined.
        Index(
            "accounts_system_kind_currency_idx",
            "kind",
            "currency",
            unique=True,
            postgresql_where=text("recipient_id is null"),
        ),
    )


class JournalEntry(Base):
    """One balanced set of postings: the unit the books balance at.

    The balance rule is a deferred constraint trigger on `ledger_entries`,
    created in the ledger schema migration. See AGENTS.md > The books.
    """

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("transfers.id", ondelete="RESTRICT"), nullable=True
    )
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
    """One line of a journal: this account, this direction, this amount.

    Append-only, enforced by a trigger. A mistake is corrected with a reversing
    journal, never an UPDATE.
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
        # credit; both would otherwise still balance.
        CheckConstraint("amount_minor > 0", name="ledger_entries_amount_check"),
        CheckConstraint("char_length(currency) = 3", name="ledger_entries_currency_check"),
        Index("ledger_entries_journal_idx", "journal_entry_id"),
        # The balance query reads every line for one account.
        Index("ledger_entries_account_idx", "account_id", desc("created_at")),
    )


class PaymentRun(Base):
    """Many disbursements authorized as one decision.

    No status and no total: both are counted from the run's transfers, as
    balances are counted from entries. See AGENTS.md > Payment runs.
    """

    __tablename__ = "payment_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
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
    """One disbursement to one recipient: the intent and its progress.

    The journal entries are what happened; this row is what was meant to. See
    AGENTS.md > The transfer lifecycle.
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
    # The provider's id for this payment. Unique: two transfers pointing at one
    # payment means paid once and recorded twice.
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
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
        Index("transfers_run_idx", "run_id"),
    )


class IdempotencyKey(Base):
    """A client's promise that two identical requests are one request.

    See AGENTS.md > Idempotency.
    """

    __tablename__ = "idempotency_keys"

    # The key is the primary key: the uniqueness constraint is the lock.
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    # Null while the first request is still in flight.
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
    """The payment provider's record of a payment. Not our books.

    Written only by `provider/mock.py`, which stands in for an external API. A
    separate table is what makes reconciliation a real comparison. See AGENTS.md
    > The provider seam.
    """

    __tablename__ = "provider_payments"

    reference: Mapped[str] = mapped_column(Text, primary_key=True)
    # Theirs: they dedupe on a key we supply, which makes our retries safe.
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
        Index("provider_payments_created_idx", desc("created_at")),
    )


class ReconciliationFinding(Base):
    """A disagreement between our books and the provider's records.

    A fact about a moment, never updated. See AGENTS.md > Reconciliation.
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
