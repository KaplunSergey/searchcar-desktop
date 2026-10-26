"""Persist normalized public list observations in the common worker transaction."""
from datetime import datetime, timezone
from sqlalchemy import select
from .models import Car, CarSnapshot, ProjectCar
from .money import money_values_out, store_money_values
from .auction_rounds import record_auction_observation
from .sources.registry import get_source


def store_auction_row(db, project, row, *, scan_id, complete):
    source = get_source(project.source_key)
    detail = source.parse_list_row(row)
    external_id = detail['source_listing_id']
    car = db.scalar(select(Car).where(Car.source_key==project.source_key, Car.source_listing_id==external_id))
    relation = db.get(ProjectCar,dict(project_id=project.id,car_id=car.id)) if car else None
    if car and (car.excluded or (relation and not relation.tracking_enabled)):
        return None
    if not car and not detail['schedule'].get('local_date'):
        return None
    timestamp = datetime.now(timezone.utc)
    new_relation = relation is None
    if car is None:
        car = Car(source_key=project.source_key,source_listing_id=external_id,canonical_encar_id=None,
                  url=detail['url'],title=detail['vehicle_title'],status='AVAILABLE')
        db.add(car)
        db.flush()
    if relation is None:
        relation = ProjectCar(project_id=project.id,car_id=car.id,first_seen_at=timestamp,is_new=True,tracking_enabled=True)
        db.add(relation)
    previous = car.details or {}
    date_absent = not detail['schedule'].get('local_date') and row.get('date_text') in ('Upcoming lot','Future','Auction Not Assigned')
    schedule = {**detail['schedule'], 'date_absent_confirmed':date_absent and complete}
    observation = record_auction_observation(db,car,observation_key=f'scan:{scan_id}:{car.id}',observed_at=timestamp,schedule=schedule)
    incoming = [{**v, 'auction_round':observation.round_id if v['meaning']=='CURRENT_BID' else None} for v in detail['money_values']]
    present = {(v['currency'],v['meaning']) for v in incoming}
    incoming.extend({**v, 'amount_minor':None,'state':'UNKNOWN'} for v in money_values_out(car,db)
                    if (v['currency'],v['meaning']) not in present)
    detail['money_values'] = store_money_values(db,car,incoming,provenance={'scan_run_id':scan_id,'checked_at':timestamp.isoformat(),'source':'PUBLIC_LIST'})
    merged = {**previous, **{key:value for key,value in detail.items() if value is not None}}
    merged['mileage']={**(previous.get('mileage') or {}),**{key:value for key,value in detail['mileage'].items() if value is not None}}
    if date_absent and not complete:
        merged['schedule'] = previous.get('schedule', detail['schedule'])
    fingerprint = source.fingerprint_listing(merged)
    changed = bool(previous and fingerprint!=car.fingerprint)
    car.url, car.title, car.current_price = detail['url'],detail['vehicle_title'],None
    car.details, car.fingerprint, car.updated_at = merged,fingerprint,timestamp
    relation.search_status='FOUND'
    relation.last_found_at=relation.last_seen_at=timestamp
    if new_relation or changed or relation.last_observed_snapshot_id is None:
        snapshot = CarSnapshot(car_id=car.id,kind='LIST_ONLY',payload={**merged,'scan_run_id':scan_id})
        db.add(snapshot)
        db.flush()
        relation.last_observed_snapshot_id=snapshot.id
    relation.last_observed_at=timestamp
    relation.last_observed_fingerprint=fingerprint
    relation.last_observed_status=car.status
    if new_relation or changed:
        return dict(car_id=car.id,project_id=project.id,project_name=project.name,source_key=project.source_key,
                    source_listing_id=external_id,title=car.title,change='NEW' if new_relation else 'UPDATED',
                    changes=source.material_changes(previous,merged),after_snapshot_id=relation.last_observed_snapshot_id,
                    updated_at=timestamp.isoformat())
    return None


def finish_auction_search(db, project, seen, *, all_pages):
    if not all_pages:
        return
    for relation, car in db.execute(select(ProjectCar,Car).join(Car,Car.id==ProjectCar.car_id).where(
            ProjectCar.project_id==project.id,ProjectCar.tracking_enabled.is_(True),Car.source_key==project.source_key)):
        if car.source_listing_id not in seen:
            relation.search_status='NOT_FOUND_IN_SEARCH'  # Not SOLD, not a global deletion.
