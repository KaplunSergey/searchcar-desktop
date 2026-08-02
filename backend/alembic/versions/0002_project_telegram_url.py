"""add optional Telegram channel URL to projects"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.add_column("projects", sa.Column("telegram_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("projects", "telegram_url")
