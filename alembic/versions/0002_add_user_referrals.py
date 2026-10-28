"""Add referral fields to users."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("referred_by", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("affiliation_shares", sa.Integer(), server_default="0", nullable=False))
    op.add_column("users", sa.Column("referrals_count", sa.Integer(), server_default="0", nullable=False))
    op.create_index("ix_users_referred_by", "users", ["referred_by"])

def downgrade():
    op.drop_index("ix_users_referred_by", table_name="users")
    op.drop_column("users", "referrals_count")
    op.drop_column("users", "affiliation_shares")
    op.drop_column("users", "referred_by")
