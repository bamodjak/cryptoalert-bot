"""Add admin tools, manual payment records, grants and giveaways."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("suspended", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("payments", sa.Column("premium_days", sa.Integer(), nullable=True))
    op.add_column("payments", sa.Column("entitlement_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("entitlement_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("entitlement_tracked", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "premium_grants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(256), server_default="manual grant", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_premium_grants_user_id", "premium_grants", ["user_id"])
    op.create_table(
        "manual_payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recorded_by", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(12), nullable=False),
        sa.Column("reference", sa.String(128), unique=True),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("premium_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("verified_by", sa.BigInteger()),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_manual_payments_user_id", "manual_payments", ["user_id"])
    op.create_table(
        "giveaways",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("premium_days", sa.Integer(), nullable=False),
        sa.Column("winner_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("drawn_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "giveaway_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("giveaway_id", sa.Integer(), sa.ForeignKey("giveaways.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("giveaway_id", "user_id", name="uq_giveaway_entry_user"),
    )
    op.create_index("ix_giveaway_entries_giveaway_id", "giveaway_entries", ["giveaway_id"])
    op.create_index("ix_giveaway_entries_user_id", "giveaway_entries", ["user_id"])
    op.create_table(
        "giveaway_winners",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("giveaway_id", sa.Integer(), sa.ForeignKey("giveaways.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("premium_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("giveaway_id", "user_id", name="uq_giveaway_winner_user"),
    )
    op.create_index("ix_giveaway_winners_giveaway_id", "giveaway_winners", ["giveaway_id"])
    op.create_index("ix_giveaway_winners_user_id", "giveaway_winners", ["user_id"])
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_user_id", sa.BigInteger()),
        sa.Column("details", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_audit_admin_id", "admin_audit", ["admin_id"])


def downgrade() -> None:
    op.drop_table("admin_audit")
    op.drop_table("giveaway_winners")
    op.drop_table("giveaway_entries")
    op.drop_table("giveaways")
    op.drop_table("manual_payments")
    op.drop_table("premium_grants")
    op.drop_column("payments", "entitlement_tracked")
    op.drop_column("payments", "entitlement_end")
    op.drop_column("payments", "entitlement_start")
    op.drop_column("payments", "premium_days")
    op.drop_column("users", "suspended")
