"""Track Stars refunds whose remote result may need reconciliation."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("refund_pending", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_payments_refund_pending", "payments", ["refund_pending"])


def downgrade() -> None:
    op.drop_index("ix_payments_refund_pending", table_name="payments")
    op.drop_column("payments", "refund_pending")
