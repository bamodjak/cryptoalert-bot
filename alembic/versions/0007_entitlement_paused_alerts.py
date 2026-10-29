"""Pause Premium-only and excess alerts after entitlement expiry."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("entitlement_paused", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(
        "ix_alerts_active_entitlement",
        "alerts",
        ["active", "entitlement_paused"],
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_active_entitlement", table_name="alerts")
    op.drop_column("alerts", "entitlement_paused")
