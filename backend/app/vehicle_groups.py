"""Group listings for display, keeping each listing's tracking data intact."""

import re
import unicodedata


def group_vehicle_listings(listings: list[dict]) -> list[dict]:
    identities = []
    plate_vins: dict[str, set[str]] = {}
    for listing in listings:
        details = listing.get("details") or {}
        plate = re.sub(r"[\s-]", "", unicodedata.normalize("NFKC", str(details.get("registration_number") or "")))
        if not re.fullmatch(r"[가-힣]{0,4}\d{2,3}[가-힣]\d{4}", plate):
            plate = ""
        vin = str(details.get("vin") or "").strip().upper()
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            vin = ""
        identities.append((plate, vin))
        if plate and vin:
            plate_vins.setdefault(plate, set()).add(vin)

    groups: dict[tuple, list[dict]] = {}
    for listing, (plate, vin) in zip(listings, identities):
        known_vins = plate_vins.get(plate, set())
        if not vin and len(known_vins) == 1:
            vin = next(iter(known_vins))
        if vin:
            key = ("vin", vin)
        elif plate and len(known_vins) < 2:
            key = ("plate", plate)
        else:
            # Unknown or conflicting identities must stay separate.
            key = ("listing", listing["id"])
        groups.setdefault(key, []).append(listing)

    result = []
    unavailable = {"UNAVAILABLE", "NOT_FOUND_IN_SEARCH"}
    for members in groups.values():
        # Input is newest first. Prefer an active listing over stale/sold copies.
        members.sort(key=lambda item: 2 if item.get("status") == "SOLD" else 1 if item.get("status") in unavailable else 0)
        result.append({**members[0], "other_listings": members[1:]})
    return result
