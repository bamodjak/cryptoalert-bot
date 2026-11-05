"""Add snooze scheduling to alerts."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_alerts_snoozed_until", "alerts", ["snoozed_until"])


def downgrade() -> None:
    op.drop_index("ix_alerts_snoozed_until", table_name="alerts")
    op.drop_column("alerts", "snoozed_until")
