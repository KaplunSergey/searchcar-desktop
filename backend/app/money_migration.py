"""Frozen v1 schema/backfill for SQLite 10 and Alembic 0019.

Do not derive a historical migration from mutable ORM/adapter contracts.
Original amounts/payload fields and internal IDs are retained. A historical
monthly quote without its own type cannot inherit today's offer type.
"""
import sqlalchemy as sa


def money_tables_v1():
    metadata = sa.MetaData()
    cars = sa.Table("cars", metadata, sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("source_key", sa.String), sa.Column("current_price", sa.BigInteger),
                    sa.Column("details", sa.JSON))
    money = sa.Table(
        "car_money_values", metadata,
        sa.Column("car_id", sa.Integer, sa.ForeignKey("cars.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("meaning", sa.String(32), primary_key=True),
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("amount_minor", sa.BigInteger), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("auction_round", sa.String(128)),
        sa.CheckConstraint("currency IN ('KRW', 'USD')", name="ck_money_currency"),
        sa.CheckConstraint("meaning IN ('SALE_TOTAL', 'RENT_MONTHLY', 'LEASE_MONTHLY', 'CURRENT_BID', 'BUY_NOW', 'ESTIMATED_RETAIL', 'UNKNOWN')", name="ck_money_meaning"),
        sa.CheckConstraint("(state = 'KNOWN' AND amount_minor IS NOT NULL AND amount_minor >= 0) OR (state IN ('UNKNOWN', 'ON_REQUEST') AND amount_minor IS NULL)", name="ck_money_amount_state"),
        sa.CheckConstraint("meaning != 'UNKNOWN' OR state = 'UNKNOWN'", name="ck_money_unknown_meaning"),
        sa.CheckConstraint("auction_round IS NULL OR (meaning = 'CURRENT_BID' AND length(auction_round) > 0)", name="ck_money_auction_round"),
    )
    history = [sa.Table(name, metadata, sa.Column("id", sa.Integer, primary_key=True),
                        sa.Column("car_id", sa.Integer), sa.Column("payload", sa.JSON))
               for name in ("price_history", "car_snapshots")]
    return cars, money, history


def _legacy_quote_v1(payload, fallback=None, *, ambiguous_history=False):
    offer = payload.get("offer_type")
    if not offer:
        offer = {"DETAIL_RENTAL_MONTHLY": "RENT", "DETAIL_LEASE_MONTHLY": "LEASE"}.get(payload.get("price_source"))
    if not offer:
        offer = "UNKNOWN" if ambiguous_history else "SALE"
    meaning = {"SALE": "SALE_TOTAL", "RENT": "RENT_MONTHLY", "LEASE": "LEASE_MONTHLY"}.get(offer, "UNKNOWN")
    amount = fallback if fallback is not None else payload.get("price_krw")
    trusted = payload.get("price_source") in {None, "DETAIL_PRIMARY", "DETAIL_RENTAL_MONTHLY", "DETAIL_LEASE_MONTHLY"}
    if fallback is None and not trusted:
        amount = None
    if amount is None and trusted:
        amount = {"RENT_MONTHLY": payload.get("rental_monthly_payment_krw"),
                  "LEASE_MONTHLY": payload.get("lease_monthly_payment_krw")}.get(meaning)
    if meaning == "UNKNOWN" or type(amount) is not int or not 0 <= amount <= 9223372036854775807:
        amount = None
    return dict(amount_minor=amount, currency="KRW", meaning=meaning,
                state="KNOWN" if amount is not None else "UNKNOWN", auction_round=None)


def backfill_money_v1(connection):
    cars, money, history = money_tables_v1()
    for car in connection.execute(sa.select(cars).where(cars.c.source_key == "encar")):
        details = car.details or {}
        value = _legacy_quote_v1(details, car.current_price)
        existing = connection.execute(sa.select(money.c.car_id).where(money.c.car_id == car.id)).first()
        if not existing:
            connection.execute(money.insert().values(car_id=car.id, **value))
        if "money_values" not in details:
            connection.execute(cars.update().where(cars.c.id == car.id).values(details={**details, "money_values": [value]}))
        for table in history:
            for point in connection.execute(sa.select(table).where(table.c.car_id == car.id)):
                payload = point.payload or {}
                if "money_values" in payload:
                    continue
                quote = _legacy_quote_v1(payload, ambiguous_history=(
                    table.name == "price_history" and details.get("offer_type") not in (None, "SALE")
                ))
                connection.execute(table.update().where(table.c.id == point.id).values(
                    payload={**payload, "money_values": [quote]}))


def migrate_money_v1(connection):
    _cars, money, _history = money_tables_v1()
    money.create(connection, checkfirst=True)
    backfill_money_v1(connection)
