"""Read-only saved auction collections, no live sites or production data."""
from datetime import timedelta

import pytest
from sqlalchemy import select
from app.models import Car, Project, ProjectCar, AuctionScheduleVersion
from app.auction_rounds import record_auction_observation
from app.auction_storage import finish_auction_search
from test_auction_api import auction_api, NOW


@pytest.fixture
def collections(auction_api):
    client, factory, clock = auction_api
    with factory() as db:
        for source, project, base in [("copart", 1, 10), ("iaa", 2, 20)]:
            db.get(ProjectCar,(project,project)).tracking_enabled=False
            for offset, schedule in enumerate([
                dict(local_datetime="2026-10-11T02:00:00", source_timezone="UTC", event_kind="START"),
                dict(local_datetime="2026-10-12T02:00:00", source_timezone="UTC", event_kind="START"),
                dict(local_date="2026-10-13", source_timezone="UTC", event_kind="START"),
                None,
                dict(local_datetime="2026-10-09T02:00:00", source_timezone="UTC", event_kind="START"),
            ]):
                car = Car(id=base+offset, source_key=source, source_listing_id=f"saved-{offset}", url=f"https://{source}.example.test/{offset}")
                db.add(car); db.flush()
                db.add(ProjectCar(project_id=project, car_id=car.id))
                record_auction_observation(db, car, observation_key="fixture", observed_at=NOW, schedule=schedule)
        db.commit()
    return client, factory, clock


def read(client, project=1, collection="active", zone="UTC", **params):
    return client.get(f"/api/projects/{project}/auction-lots", params=dict(computer_timezone=zone, collection=collection, **params))


@pytest.mark.parametrize("source,project,base", [("copart",1,10),("iaa",2,20)])
def test_saved_collections_separate_today_future_undated_without_archiving_elapsed(collections, source, project, base):
    client, factory, _ = collections
    with factory() as db:
        before = [(r.project_id,r.car_id,r.search_status,r.tracking_enabled) for r in db.scalars(select(ProjectCar))]
        versions = len(list(db.scalars(select(AuctionScheduleVersion))))
    assert [lot["id"] for lot in read(client,project,"today").json()["lots"]] == [base]
    assert {lot["id"] for lot in read(client,project,"future").json()["lots"]} == {base+1,base+2}
    assert [lot["id"] for lot in read(client,project,"undated").json()["lots"]] == [base+3]
    assert read(client,project,"archive").json()["lots"] == []
    assert base+4 in {lot["id"] for lot in read(client,project).json()["lots"]}
    assert all(lot["source_key"]==source for lot in read(client,project).json()["lots"])
    with factory() as db:
        assert [(r.project_id,r.car_id,r.search_status,r.tracking_enabled) for r in db.scalars(select(ProjectCar))] == before
        assert len(list(db.scalars(select(AuctionScheduleVersion)))) == versions


def test_archive_is_project_scoped_full_search_evidence_not_partial_elapsed_or_manual_removal(collections):
    client, factory, _ = collections
    with factory() as db:
        db.add(ProjectCar(project_id=4,car_id=10))
        finish_auction_search(db, db.get(Project,1), {"same-id"}, all_pages=False)
        db.commit()
    assert read(client,collection="archive").json()["lots"] == []
    with factory() as db:
        finish_auction_search(db,db.get(Project,1),{"same-id"},all_pages=True)
        db.get(ProjectCar,(1,11)).tracking_enabled=False
        db.get(ProjectCar,(1,11)).search_status="REMOVED_FROM_PROJECT"
        db.commit()
    result=read(client,collection="archive").json()
    assert {lot["id"] for lot in result["lots"]} == {10,12,13,14}
    assert all(lot["project_search_status"]=="NOT_FOUND_IN_SEARCH" for lot in result["lots"])
    assert 10 not in {lot["id"] for lot in read(client).json()["lots"]}
    assert read(client,project=4,collection="archive").status_code==404
    with factory() as db:
        assert db.get(ProjectCar,(4,10)).search_status=="FOUND"
        assert db.get(Car,10).status!="SOLD"
        db.get(ProjectCar,(1,10)).search_status="FOUND";db.commit()
    assert 10 in {lot["id"] for lot in read(client).json()["lots"]}


def test_collections_follow_runtime_zone_midnight_and_validate_inputs(collections):
    client, _, clock = collections
    clock[0]=NOW+timedelta(hours=22)
    assert 11 in {lot["id"] for lot in read(client,collection="future",zone="UTC").json()["lots"]}
    assert 11 not in {lot["id"] for lot in read(client,collection="future",zone="America/Los_Angeles").json()["lots"]}
    clock[0]=NOW+timedelta(days=1)
    assert 11 in {lot["id"] for lot in read(client,collection="today").json()["lots"]}
    assert read(client,collection="sold").status_code==422
    assert read(client,collection="archive",today_only=True).status_code==400
    assert client.get('/api/projects/1/auction-lots?computer_timezone=UTC&today_only=true').json()['lots']==read(client,collection="today").json()['lots']


def test_date_only_cross_day_stays_uncertain_and_confirmed_loss_then_return_reclassifies(collections):
    client, factory, _ = collections
    with factory() as db:
        car=db.get(Car,13)
        record_auction_observation(db,car,observation_key="date-only",observed_at=NOW+timedelta(seconds=1),
            schedule=dict(local_date="2026-10-11",source_timezone="America/Chicago",event_kind="START"))
        db.commit()
    assert 13 not in {lot['id'] for lot in read(client,collection='today').json()['lots']}
    assert 13 not in {lot['id'] for lot in read(client,collection='future').json()['lots']}
    assert next(lot for lot in read(client).json()['lots'] if lot['id']==13)['time']['is_today'] is None
    with factory() as db:
        record_auction_observation(db,db.get(Car,13),observation_key="date-lost",observed_at=NOW+timedelta(seconds=2),
            schedule={'date_absent_confirmed':True})
        db.commit()
    assert 13 in {lot['id'] for lot in read(client,collection='undated').json()['lots']}
    with factory() as db:
        record_auction_observation(db,db.get(Car,13),observation_key="date-returned",observed_at=NOW+timedelta(seconds=3),
            schedule=dict(local_date='2026-10-14',source_timezone='UTC',event_kind='START'))
        db.commit()
    assert 13 not in {lot['id'] for lot in read(client,collection='undated').json()['lots']}
    assert 13 in {lot['id'] for lot in read(client,collection='future').json()['lots']}


def test_archive_honors_user_exclusion_and_other_owned_project_remains_active(collections):
    client, factory, _ = collections
    with factory() as db:
        db.add(Project(id=5,owner_id=1,source_key='copart',name='Other',name_key='other',search_url='https://copart.example.test/other'))
        db.flush();db.add(ProjectCar(project_id=5,car_id=10))
        db.get(ProjectCar,(1,10)).search_status='NOT_FOUND_IN_SEARCH';db.commit()
    assert [lot['id'] for lot in read(client,project=5).json()['lots']]==[10]
    assert [lot['id'] for lot in read(client,collection='archive').json()['lots']]==[10]
    with factory() as db:
        from app.models import UserCarState
        db.add(UserCarState(user_id=1,car_id=10,excluded=True));db.commit()
    assert read(client,collection='archive').json()['lots']==[]
