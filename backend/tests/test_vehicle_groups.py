from app.vehicle_groups import group_vehicle_listings


def listing(id, plate=None, vin=None, status="UPDATED"):
    return {"id": id, "status": status, "details": {"registration_number": plate, "vin": vin}}


def test_grouping_prefers_active_listing_and_preserves_individual_offers():
    sold = {**listing(1, "237허8037", status="SOLD"), "price": 100, "favorite": True}
    active = {**listing(2, " 237 허-8037 "), "price": 200, "favorite": False}
    groups = group_vehicle_listings([sold, active, listing(3, "237허8038")])
    assert len(groups) == 2
    assert groups[0]["id"] == 2
    assert groups[0]["price"] == 200
    assert groups[0]["other_listings"] == [sold]
    assert active.get("other_listings") is None


def test_grouping_uses_vin_and_keeps_unknown_or_conflicting_identities_separate():
    vin = "KMHAA123456789012"
    other_vin = "KMHAA123456789013"
    assert len(group_vehicle_listings([
        listing(1, "237허8037", vin), listing(2, "237허8037"), listing(3, vin=vin),
    ])) == 1
    assert len(group_vehicle_listings([
        listing(1, "237허8037", vin), listing(2, "237허8037", other_vin), listing(3, "237허8037"),
    ])) == 3
    for plate in (None, "", "—", "비공개", "237허****", "unknown"):
        assert len(group_vehicle_listings([listing(1, plate), listing(2, plate)])) == 2
