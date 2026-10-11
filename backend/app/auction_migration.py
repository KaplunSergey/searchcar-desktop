"""Frozen additive schema for SQLite 11 / Alembic 0020.

Also used by the initial ORM mapping. Keep v1 immutable; future schema changes
need a new migration, not edits to these historical definitions.
"""
import sqlalchemy as sa

from .db_types import UTCDateTime


def auction_tables_v1(metadata=None):
    metadata = metadata if metadata is not None else sa.MetaData()
    if "cars" not in metadata.tables:
        sa.Table("cars", metadata, sa.Column("id", sa.Integer, primary_key=True))
    rounds = sa.Table(
        "auction_rounds", metadata,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("car_id", sa.Integer, sa.ForeignKey("cars.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_kind", sa.String(24), nullable=False),
        sa.Column("evidence_key", sa.String(128)),
        sa.Column("source_sale_id", sa.String(128)),
        sa.Column("is_current", sa.Boolean, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_observed_at", UTCDateTime(), nullable=False),
        sa.Column("source_updated_at", UTCDateTime()),
        sa.UniqueConstraint("car_id", "evidence_kind", "evidence_key", name="uq_auction_round_evidence"),
        sa.UniqueConstraint("car_id", "source_sale_id", name="uq_auction_round_sale_id"),
        sa.UniqueConstraint("id", "car_id", name="uq_auction_round_car"),
        sa.CheckConstraint("(evidence_kind = 'PROVISIONAL' AND evidence_key IS NULL) OR "
                           "(evidence_kind IN ('SOURCE_SALE_ID', 'SOURCE_TRANSITION') AND length(evidence_key) > 0 AND evidence_key IS NOT NULL)",
                           name="ck_auction_round_evidence"),
        sa.CheckConstraint("source_sale_id IS NULL OR length(source_sale_id) > 0", name="ck_auction_round_sale_id"),
    )
    sa.Index("uq_auction_round_current", rounds.c.car_id, unique=True,
             sqlite_where=rounds.c.is_current.is_(True), postgresql_where=rounds.c.is_current.is_(True))
    versions = sa.Table(
        "auction_schedule_versions", metadata,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("round_id", sa.String(32), sa.ForeignKey("auction_rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("change_kind", sa.String(16), nullable=False),
        sa.Column("event_kind", sa.String(16), nullable=False),
        sa.Column("precision", sa.String(16), nullable=False),
        sa.Column("utc_at", UTCDateTime()),
        sa.Column("local_date", sa.String(10)),
        sa.Column("source_timezone", sa.String(64)),
        sa.Column("evidence", sa.JSON, nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("round_id", "version", name="uq_auction_schedule_version"),
        sa.UniqueConstraint("id", "round_id", name="uq_auction_schedule_round"),
        sa.CheckConstraint("version > 0", name="ck_auction_schedule_version"),
        sa.CheckConstraint("change_kind IN ('INITIAL', 'CONFIRMED', 'RESCHEDULED')", name="ck_auction_schedule_change"),
        sa.CheckConstraint("event_kind IN ('START', 'END', 'UNKNOWN')", name="ck_auction_schedule_event"),
        sa.CheckConstraint("(precision = 'EXACT' AND utc_at IS NOT NULL AND local_date IS NOT NULL AND source_timezone IS NOT NULL) OR "
                           "(precision = 'DATE_ONLY' AND utc_at IS NULL AND local_date IS NOT NULL AND source_timezone IS NOT NULL) OR "
                           "(precision = 'UNKNOWN' AND utc_at IS NULL)", name="ck_auction_schedule_precision"),
    )
    observations = sa.Table(
        "auction_observations", metadata,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("car_id", sa.Integer, nullable=False),
        sa.Column("round_id", sa.String(32), nullable=False),
        sa.Column("schedule_version_id", sa.String(32)),
        sa.Column("observation_key", sa.String(128), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("source_updated_at", UTCDateTime()),
        sa.Column("applied", sa.Boolean, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.ForeignKeyConstraint(["round_id", "car_id"], ["auction_rounds.id", "auction_rounds.car_id"], ondelete="CASCADE", name="fk_auction_observation_car"),
        sa.ForeignKeyConstraint(["schedule_version_id", "round_id"], ["auction_schedule_versions.id", "auction_schedule_versions.round_id"], ondelete="CASCADE", name="fk_auction_observation_schedule"),
        sa.UniqueConstraint("car_id", "observation_key", name="uq_auction_observation"),
        sa.CheckConstraint("length(observation_key) > 0", name="ck_auction_observation_key"),
    )
    return rounds, versions, observations


def migrate_auctions_v1(connection):
    for table in auction_tables_v1():
        table.create(connection, checkfirst=True)
