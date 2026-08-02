"""initial SearchCar schema"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None

def upgrade():
    tables = {
        "projects": [sa.Column("name", sa.String(140), nullable=False), sa.Column("name_key", sa.String(140), unique=True, nullable=False), sa.Column("search_url", sa.Text, unique=True, nullable=False), sa.Column("scan_mode", sa.String(16), nullable=False), sa.Column("auto_update", sa.Boolean, server_default=sa.true())],
        "cars": [sa.Column("canonical_encar_id", sa.String(40), unique=True, nullable=False), sa.Column("url", sa.Text, nullable=False), sa.Column("title", sa.Text), sa.Column("details", sa.JSON), sa.Column("current_price", sa.BigInteger), sa.Column("status", sa.String(40), nullable=False), sa.Column("fingerprint", sa.String(64)), sa.Column("comment", sa.Text), sa.Column("rating", sa.Integer), sa.Column("excluded", sa.Boolean, server_default=sa.false())],
        "scan_runs": [sa.Column("kind", sa.String(20), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("progress", sa.Integer, server_default="0"), sa.Column("payload", sa.JSON), sa.Column("error", sa.Text)],
        "scheduler_settings": [sa.Column("enabled", sa.Boolean, server_default=sa.false()), sa.Column("interval_minutes", sa.Integer, server_default="180"), sa.Column("next_run_at", sa.DateTime(timezone=True))],
    }
    for name, cols in tables.items():
        op.create_table(name, sa.Column("id", sa.Integer, primary_key=True), *cols, sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("project_cars", sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True), sa.Column("car_id", sa.Integer, sa.ForeignKey("cars.id"), primary_key=True), sa.Column("first_seen_at", sa.DateTime(timezone=True)), sa.Column("last_seen_at", sa.DateTime(timezone=True)), sa.Column("last_found_at", sa.DateTime(timezone=True)), sa.Column("consecutive_missing_scans", sa.Integer, server_default="0"), sa.Column("favorite", sa.Boolean, server_default=sa.false()), sa.Column("viewed", sa.Boolean, server_default=sa.false()), sa.Column("viewed_at", sa.DateTime(timezone=True)))
    for name in ("car_aliases", "car_snapshots", "price_history", "car_events", "car_images"):
        extra = [sa.Column("alias_id", sa.String(40), unique=True)] if name == "car_aliases" else [sa.Column("kind", sa.String(40)), sa.Column("payload", sa.JSON)]
        op.create_table(name, sa.Column("id", sa.Integer, primary_key=True), sa.Column("car_id", sa.Integer, sa.ForeignKey("cars.id", ondelete="CASCADE"), nullable=False), *extra, sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("project_scan_runs", sa.Column("id", sa.Integer, primary_key=True), sa.Column("scan_run_id", sa.Integer, sa.ForeignKey("scan_runs.id", ondelete="CASCADE")), sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE")), sa.Column("status", sa.String(20)), sa.Column("error_code", sa.String(30)))
    op.create_table("scheduled_projects", sa.Column("scheduler_id", sa.Integer, sa.ForeignKey("scheduler_settings.id", ondelete="CASCADE"), primary_key=True), sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True))

def downgrade():
    for table in ("scheduled_projects","project_scan_runs","car_images","car_events","price_history","car_snapshots","car_aliases","project_cars","scheduler_settings","scan_runs","cars","projects"):
        op.drop_table(table)
