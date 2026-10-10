"""Typed money shared by storage/API; legacy numeric prices remain Encar-only.

Amounts are already minor units. This module does not parse display strings,
convert currencies, or infer an auction round from a date.
"""
from sqlalchemy import select

ENCAR_MEANINGS = {"SALE": "SALE_TOTAL", "RENT": "RENT_MONTHLY", "LEASE": "LEASE_MONTHLY"}
MEANINGS = {*ENCAR_MEANINGS.values(), "CURRENT_BID", "BUY_NOW", "ESTIMATED_RETAIL", "UNKNOWN"}


def validate_money_values(values: list[dict]) -> list[dict]:
    if not isinstance(values, list):
        raise ValueError("money_values must be a list")
    result, keys = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Invalid money value")
        amount, currency, meaning, state, auction_round = (
            value.get(key) for key in ("amount_minor", "currency", "meaning", "state", "auction_round")
        )
        if currency not in {"KRW", "USD"} or meaning not in MEANINGS:
            raise ValueError("Unsupported money currency or meaning")
        if state == "KNOWN":
            if type(amount) is not int or not 0 <= amount <= 9223372036854775807:
                raise ValueError("Known money requires a nonnegative integer minor amount")
        elif state not in {"UNKNOWN", "ON_REQUEST"} or amount is not None:
            raise ValueError("Unknown/on-request money must not have an amount")
        if meaning == "UNKNOWN" and state != "UNKNOWN":
            raise ValueError("Unknown meaning must not be assigned a price")
        if auction_round is not None and (
            meaning != "CURRENT_BID" or not isinstance(auction_round, str)
            or not auction_round.strip() or len(auction_round) > 128
        ):
            raise ValueError("Only a bid may have a verified auction round")
        key = meaning, currency
        if key in keys:
            raise ValueError("Duplicate money meaning/currency")
        keys.add(key)
        result.append(dict(amount_minor=amount, currency=currency, meaning=meaning,
                           state=state, auction_round=auction_round))
    return sorted(result, key=lambda value: (value["meaning"], value["currency"]))


def encar_money_values(payload: dict, fallback_amount: int | None = None) -> list[dict]:
    """Missing offer type in legacy Encar data meant SALE; unknown codes do not."""
    meaning = ENCAR_MEANINGS.get(payload.get("offer_type") or "SALE", "UNKNOWN")
    amount = fallback_amount if fallback_amount is not None else payload.get("price_krw")
    trusted = payload.get("price_source") in {None, "DETAIL_PRIMARY", "DETAIL_RENTAL_MONTHLY", "DETAIL_LEASE_MONTHLY"}
    if fallback_amount is None and not trusted:
        amount = None
    if amount is None and trusted:
        amount = {"RENT_MONTHLY": payload.get("rental_monthly_payment_krw"),
                  "LEASE_MONTHLY": payload.get("lease_monthly_payment_krw")}.get(meaning)
    if meaning == "UNKNOWN":
        amount = None
    return validate_money_values([dict(amount_minor=amount, currency="KRW", meaning=meaning,
                                      state="KNOWN" if amount is not None else "UNKNOWN")])


def payload_money_values(payload: dict, source_key: str) -> list[dict]:
    if "money_values" in payload:
        return validate_money_values(payload["money_values"])
    return encar_money_values(payload) if source_key == "encar" else []


def money_values_out(car, db) -> list[dict]:
    from .models import CarMoneyValue
    rows = list(db.scalars(select(CarMoneyValue).where(CarMoneyValue.car_id == car.id)
                           .order_by(CarMoneyValue.meaning, CarMoneyValue.currency)))
    if rows:
        return [dict(amount_minor=row.amount_minor, currency=row.currency, meaning=row.meaning,
                     state=row.state, auction_round=row.auction_round) for row in rows]
    # Compatibility for imported/converted legacy rows that predate the table.
    return encar_money_values(car.details or {}, car.current_price) if car.source_key == "encar" else []


def comparable_money(previous: dict, current: dict) -> bool:
    if (previous.get("state") != "KNOWN" or current.get("state") != "KNOWN"
            or previous.get("currency") != current.get("currency")
            or previous.get("meaning") != current.get("meaning")
            or current.get("meaning") == "UNKNOWN"):
        return False
    return current["meaning"] != "CURRENT_BID" or (
        bool(current.get("auction_round")) and current.get("auction_round") == previous.get("auction_round")
    )


def store_money_values(db, car, values: list[dict], *, provenance: dict | None = None,
                       record_history: bool = True) -> list[dict]:
    """Replace a complete known quote set; unverified reads preserve known money.

    Validate the entire input before mutating anything. ON_REQUEST is an explicit
    change; UNKNOWN is not evidence that a formerly verified quote disappeared.
    History is still PriceHistory, with one typed quote per changed value.
    """
    from .models import CarMoneyValue, PriceHistory

    incoming = validate_money_values(values)
    old = money_values_out(car, db)
    by_key = {(value["meaning"], value["currency"]): value for value in old}
    if not incoming or all(value["state"] == "UNKNOWN" for value in incoming):
        effective = old or incoming
    else:
        effective = [by_key.get((value["meaning"], value["currency"]), value)
                     if value["state"] == "UNKNOWN" else value for value in incoming]
    rows = {(row.meaning, row.currency): row for row in db.scalars(
        select(CarMoneyValue).where(CarMoneyValue.car_id == car.id))}
    effective_keys = {(value["meaning"], value["currency"]) for value in effective}
    for key, row in rows.items():
        if key not in effective_keys:
            db.delete(row)
    for value in effective:
        key = value["meaning"], value["currency"]
        row = rows.get(key)
        if row is None:
            row = CarMoneyValue(car_id=car.id, meaning=key[0], currency=key[1])
            db.add(row)
        row.amount_minor, row.state, row.auction_round = value["amount_minor"], value["state"], value["auction_round"]
        # A legacy car's first typed write records its verified baseline too.
        if (record_history and value in incoming and value["state"] != "UNKNOWN"
                and (value != by_key.get(key) or not rows)):
            payload = {**(provenance or {}), "money_values": [value]}
            if (car.source_key == "encar" and value["currency"] == "KRW"
                    and value["meaning"] in ENCAR_MEANINGS.values()):
                payload.update(price_krw=value["amount_minor"],
                               offer_type=next(offer for offer, meaning in ENCAR_MEANINGS.items() if meaning == value["meaning"]))
            db.add(PriceHistory(car_id=car.id, kind="PRICE", payload=payload))
    if car.source_key == "encar":
        value = effective[0] if len(effective) == 1 else None
        car.current_price = (value["amount_minor"] if value and value["currency"] == "KRW"
                             and value["meaning"] in ENCAR_MEANINGS.values() else None)
    return effective
