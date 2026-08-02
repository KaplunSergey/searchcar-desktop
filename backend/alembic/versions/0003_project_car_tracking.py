"""allow cars to be removed from one project without deleting shared data"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"


def upgrade():
    op.add_column(
        "project_cars",
        sa.Column(
            "tracking_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade():
    op.drop_column("project_cars", "tracking_enabled")
