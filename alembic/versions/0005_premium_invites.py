"""Track personalized Premium invitations and recipient starts."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "premium_invites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(length=64), nullable=False, unique=True),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicked_by", sa.BigInteger(), nullable=True),
        sa.Column("recipient_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("premium_invites")
