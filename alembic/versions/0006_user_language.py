"""Add per-user language preference."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("language", sa.String(length=2), server_default="en", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "language")
