import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import latest_price_change, price_history_out, project_cars
from app.models import Car, CarSnapshot, PriceHistory, Project, User
from app.services import upsert_detail, upsert_price_status


def quote(amount, meaning="SALE_TOTAL", currency="KRW", state="KNOWN", auction_round=None):
    return dict(amount_minor=amount, currency=currency, meaning=meaning,
                state=state, auction_round=auction_round)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(User(id=1, username="Owner", username_key="owner", password_hash="!"))
        session.flush()
        session.add(Project(id=1, owner_id=1, name="Test", name_key="test",
                            search_url="https://www.encar.com/dc/dc_carsearchlist.do"))
        session.commit()
        yield session


def detail(amount, offer="SALE"):
    return dict(canonical_car_id="42688672", source_car_id="42688672",
                url="https://fem.encar.com/cars/detail/42688672", title="Audi",
                price_krw=amount, offer_type=offer, sold=False,
                price_source={"SALE": "DETAIL_PRIMARY", "RENT": "DETAIL_RENTAL_MONTHLY",
                              "LEASE": "DETAIL_LEASE_MONTHLY"}[offer])


@pytest.mark.parametrize("offer,meaning", [
    ("SALE", "SALE_TOTAL"), ("RENT", "RENT_MONTHLY"), ("LEASE", "LEASE_MONTHLY")])
def test_encar_money_is_typed_in_storage_snapshot_history_and_api(db, offer, meaning):
    car = upsert_detail(db, db.get(Project, 1), detail(360000, offer))
    db.commit()
    output = project_cars(project_id=1, current=db.get(User, 1), db=db)
    assert output[0]["price"] == 360000
    assert output[0]["money_values"] == [quote(360000, meaning)]
    assert db.scalar(select(CarSnapshot)).payload["money_values"] == [quote(360000, meaning)]
    assert db.scalar(select(PriceHistory)).payload["money_values"] == [quote(360000, meaning)]
    assert price_history_out(car.id, db)[0]["money_values"] == [quote(360000, meaning)]


def test_monthly_payment_is_not_a_discount_from_sale_price(db):
    project = db.get(Project, 1)
    car = upsert_detail(db, project, detail(26000000))
    db.commit()
    upsert_price_status(db, project, detail(360000, "RENT"))
    db.commit()
    assert latest_price_change(car, db) is None
    upsert_detail(db, project, detail(350000, "RENT"))
    db.commit()
    assert latest_price_change(car, db)["previous"] == 360000
    assert latest_price_change(car, db)["current"] == 350000


def test_unknown_and_sold_observations_do_not_erase_verified_money(db):
    from app.money import money_values_out
    project = db.get(Project, 1)
    car = upsert_detail(db, project, detail(26000000))
    db.commit()
    upsert_price_status(db, project, detail(None))
    db.commit()
    assert money_values_out(car, db) == [quote(26000000)]
    upsert_detail(db, project, {**detail(None), "sold": True})
    db.commit()
    assert money_values_out(car, db) == [quote(26000000)]
    assert car.current_price == 26000000


def test_auction_has_separate_bid_buy_now_and_estimate_without_krw_mirror(db):
    from app.money import money_values_out, store_money_values
    car = Car(source_key="copart", source_listing_id="42688672", url="https://copart.example/lot/42688672")
    db.add(car)
    db.flush()
    values = [quote(0, "CURRENT_BID", "USD", auction_round="round-1"),
              quote(1299950, "BUY_NOW", "USD"), quote(2500000, "ESTIMATED_RETAIL", "USD")]
    store_money_values(db, car, values)
    db.commit()
    assert money_values_out(car, db) == sorted(values, key=lambda value: (value["meaning"], value["currency"]))
    assert car.current_price is None
    assert latest_price_change(car, db) is None


@pytest.mark.parametrize("bad", [
    quote(10.5), quote(True), quote(-1), quote(None), quote(100, currency="???"),
    quote(100, meaning="PRICE"), quote(100, state="UNKNOWN"),
    quote(100, "SALE_TOTAL", auction_round="invented"),
])
def test_invalid_money_rejected_before_any_write(db, bad):
    from app.money import store_money_values
    car = upsert_detail(db, db.get(Project, 1), detail(1000))
    db.commit()
    with pytest.raises(ValueError):
        store_money_values(db, car, [quote(1200), bad])
    assert price_history_out(car.id, db)[-1]["price"] == 1000


def test_on_request_is_not_zero_and_duplicate_quote_is_rejected(db):
    from app.money import money_values_out, store_money_values
    car = upsert_detail(db, db.get(Project, 1), detail(1000))
    db.commit()
    store_money_values(db, car, [quote(None, state="ON_REQUEST")])
    db.commit()
    assert money_values_out(car, db) == [quote(None, state="ON_REQUEST")]
    assert car.current_price is None
    with pytest.raises(ValueError):
        store_money_values(db, car, [quote(1000), quote(2000)])


def test_currency_and_bid_round_changes_are_not_percentage_changes():
    from app.money import comparable_money
    assert comparable_money(quote(1000), quote(1000, currency="USD")) is False
    assert comparable_money(quote(1000), quote(1000, "RENT_MONTHLY")) is False
    assert comparable_money(quote(1000, "CURRENT_BID", "USD"), quote(500, "CURRENT_BID", "USD")) is False
    assert comparable_money(quote(1000, "CURRENT_BID", "USD", auction_round="a"),
                            quote(500, "CURRENT_BID", "USD", auction_round="b")) is False
    assert comparable_money(quote(1000, "CURRENT_BID", "USD", auction_round="a"),
                            quote(1100, "CURRENT_BID", "USD", auction_round="a")) is True


def test_repeating_quotes_is_idempotent_and_bid_history_preserves_round(db):
    from app.money import store_money_values
    car = Car(source_key="iaai", source_listing_id="46271057", url="https://iaai.example/lot/46271057")
    db.add(car)
    db.flush()
    bid = quote(50000, "CURRENT_BID", "USD", auction_round="a")
    store_money_values(db, car, [bid])
    db.commit()
    store_money_values(db, car, [bid])
    db.commit()
    assert len(price_history_out(car.id, db)) == 1
    next_bid = quote(0, "CURRENT_BID", "USD", auction_round="b")
    store_money_values(db, car, [next_bid])
    db.commit()
    assert [point["money_values"] for point in price_history_out(car.id, db)] == [[bid], [next_bid]]


def test_import_preserves_different_meanings_with_same_numeric_amount(db, tmp_path, monkeypatch):
    import json
    from sqlalchemy.orm import sessionmaker
    from app import importer
    from app.money import money_values_out

    root = tmp_path / "import"
    (root / "data").mkdir(parents=True)
    (root / "data/seen.json").write_text(json.dumps({"42688672": {
        **detail(360000, "RENT"), "price_history": [
            {"price_krw": 360000, "offer_type": "SALE", "checked_at": "2026-10-01T10:00:00Z"},
            {"price_krw": 360000, "offer_type": "RENT", "checked_at": "2026-10-02T10:00:00Z"},
        ],
    }}), encoding="utf-8")
    monkeypatch.setattr(importer, "SessionLocal", sessionmaker(bind=db.get_bind()))
    importer.run(root, commit=True, owner_id=1)
    db.expire_all()
    car = db.scalar(select(Car))
    assert money_values_out(car, db) == [quote(360000, "RENT_MONTHLY")]
    assert [point["money_values"] for point in price_history_out(car.id, db)] == [
        [quote(360000)], [quote(360000, "RENT_MONTHLY")]]
    importer.run(root, commit=True, owner_id=1)
    assert len(price_history_out(car.id, db)) == 2


def test_change_report_preserves_exact_money_basis(db):
    from app.worker import PreviousState, _change_report
    project = db.get(Project, 1)
    car = upsert_detail(db, project, detail(360000, "RENT"))
    db.commit()
    previous = PreviousState(car_id=car.id, canonical_id=car.canonical_encar_id,
                             price=360000, fingerprint=car.fingerprint, status=car.status,
                             details=dict(car.details), search_status="FOUND", snapshot_id=None)
    upsert_detail(db, project, detail(350000, "RENT"))
    db.commit()
    report = _change_report(car, project, previous)
    assert report["change"] == "PRICE_DROP"
    assert report["money_values"] == [quote(350000, "RENT_MONTHLY")]
    assert report["previous_money_values"] == [quote(360000, "RENT_MONTHLY")]


def test_report_does_not_compare_different_currencies():
    from types import SimpleNamespace
    from app.worker import PreviousState, _change_report
    previous = PreviousState(car_id=1, canonical_id="42688672", price=1000,
                             fingerprint="old", status="UPDATED", search_status="FOUND",
                             details={"money_values": [quote(1000)]}, snapshot_id=None)
    car = SimpleNamespace(id=1, canonical_encar_id="42688672", current_price=2000,
                          fingerprint="new", status="UPDATED", title="Car",
                          details={"money_values": [quote(2000, currency="USD")]})
    report = _change_report(car, SimpleNamespace(id=1, name="P"), previous)
    assert report["change"] == "MATERIAL_UPDATE"
    assert report["changes"][0]["field"] == "money_values"


def test_legacy_krw_mirror_is_cleared_for_non_krw_money(db):
    from app.money import store_money_values
    car = upsert_detail(db, db.get(Project, 1), detail(1000))
    db.commit()
    store_money_values(db, car, [quote(25000, currency="USD")])
    db.commit()
    assert car.current_price is None
    assert latest_price_change(car, db) is None
    assert price_history_out(car.id, db)[-1]["price"] is None
    assert price_history_out(car.id, db)[-1]["money_values"] == [quote(25000, currency="USD")]
