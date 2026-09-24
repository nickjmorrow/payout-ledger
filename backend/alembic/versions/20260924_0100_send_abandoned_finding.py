"""send_abandoned finding

Reconciliation now finishes a pending transfer whose send task was
dead-lettered without being reversed. Autogenerate cannot see a CHECK
constraint's body, so this is written by hand.

Revision ID: 5d1c8e2f7a40
Revises: a637f2d88599
Create Date: 2026-09-24 01:00:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op


revision: str = "5d1c8e2f7a40"
down_revision: str | None = "a637f2d88599"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "'missing_at_provider', 'unknown_to_us', 'status_behind', 'status_contradicted', 'amount_mismatch'"


def upgrade() -> None:
    op.drop_constraint(
        "reconciliation_findings_kind_check", "reconciliation_findings", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_findings_kind_check",
        "reconciliation_findings",
        f"kind in ({KINDS}, 'send_abandoned')",
    )


def downgrade() -> None:
    op.execute("delete from reconciliation_findings where kind = 'send_abandoned'")
    op.drop_constraint(
        "reconciliation_findings_kind_check", "reconciliation_findings", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_findings_kind_check", "reconciliation_findings", f"kind in ({KINDS})"
    )
