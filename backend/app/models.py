"""ORM models.

**This file is the source of truth for the schema.** Alembic autogenerates
migrations by diffing these classes against the database, so anything not
declared here does not exist: a CHECK constraint left out is a CHECK constraint
dropped, silently, on the next `alembic revision --autogenerate`.

That is why the constraints below are spelled out rather than left to the
service layer to enforce. The application validates too — better errors, closer
to the user — but the database is the thing that cannot be bypassed by a
migration script, a psql session, or the next process someone writes.

Note `default=` AND `server_default=` on several columns. They are not
redundant: `default=` is applied by SQLAlchemy when the ORM inserts a row, and
`server_default=` is what the column actually has in Postgres. Declare only the
first and every insert that does not go through the ORM — a psql session, a
fixture, the raw SQL in task_service — hits a NOT NULL column with no default.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

# N811: `UUID` is a class, not a constant — pep8-naming cannot tell.
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
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
            "status in ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="tasks_status_check",
        ),
        # Partial, because the claim query only ever looks at pending rows.
        # Without postgresql_where this becomes a full index over every task
        # that has ever run, which is the opposite of the point.
        Index("tasks_claim_idx", "run_at", postgresql_where=text("status = 'pending'")),
        Index("tasks_kind_idx", "kind", desc("created_at")),
    )
