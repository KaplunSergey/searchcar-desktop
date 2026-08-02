"""store the user's preferred interface locale

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "preferred_locale",
            sa.String(length=2),
            nullable=False,
            server_default="ru",
        ),
    )
    op.create_check_constraint(
        "ck_users_preferred_locale",
        "users",
        "preferred_locale IN ('ru', 'uk')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_users_preferred_locale",
        "users",
        type_="check",
    )
    op.drop_column("users", "preferred_locale")
