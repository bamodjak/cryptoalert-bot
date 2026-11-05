"""Add per-category alert notification preferences."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("notify_price_alerts", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("users", sa.Column("notify_gas_alerts", sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "notify_gas_alerts")
    op.drop_column("users", "notify_price_alerts")
