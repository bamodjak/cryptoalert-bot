"""Add user timezone and advanced-interface preferences."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False))
    op.add_column("users", sa.Column("advanced_mode", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "advanced_mode")
    op.drop_column("users", "timezone")
