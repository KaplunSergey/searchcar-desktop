from __future__ import annotations

from collections.abc import Callable


DEFAULT_REPORT_PRIORITY = {
    "NEW": 0,
    "PRICE_DROP": 1,
    "PRICE_INCREASE": 1,
    "MATERIAL_UPDATE": 2,
}


def default_report_priority(item: dict) -> int:
    return DEFAULT_REPORT_PRIORITY.get(item.get("change"), 9)


def _report_key(item: dict) -> tuple[str, int | str | None]:
    """Identify the physical vehicle before falling back to its Encar listing."""
    registration_number = "".join(
        str(item.get("registration_number") or "").upper().split()
    )
    if registration_number:
        return ("registration", registration_number)
    if item.get("car_id") is not None:
        return ("car", item["car_id"])
    if item.get("encar_id"):
        return ("encar", item["encar_id"])
    return ("report", None)


def _project_context(items: list[dict], preferred: dict) -> dict:
    contexts: list[tuple[int | None, str]] = []
    for item in items:
        project_id = item.get("project_id")
        project_name = item.get("project_name")
        if project_id is None and not project_name:
            continue
        context = (project_id, project_name or f"#{project_id}")
        if context not in contexts:
            contexts.append(context)

    preferred_context = (
        preferred.get("project_id"),
        preferred.get("project_name") or f"#{preferred.get('project_id')}",
    )
    contexts.sort(
        key=lambda context: (context != preferred_context, context[1], context[0] or -1)
    )
    if len(contexts) < 2:
        return {}
    return {
        "project_ids": [project_id for project_id, _ in contexts if project_id is not None],
        "project_names": [project_name for _, project_name in contexts],
    }


def _listing_context(items: list[dict], preferred: dict) -> dict:
    listing_ids = list(
        dict.fromkeys(
            str(item["encar_id"])
            for item in [preferred, *items]
            if item.get("encar_id")
        )
    )
    return {"encar_ids": listing_ids} if len(listing_ids) > 1 else {}


def dedupe_report(
    report: list[dict],
    *,
    priority: Callable[[dict], int] = default_report_priority,
) -> list[dict]:
    """Return every changed physical vehicle once per report."""
    grouped: dict[tuple[str, int | str | None], list[dict]] = {}
    for item in report:
        if item.get("change") == "RELISTED":
            continue
        grouped.setdefault(_report_key(item), []).append(item)

    deduped: list[dict] = []
    for items in grouped.values():
        preferred = items[0]
        for item in items[1:]:
            if item.get("change") == "SOLD" or (
                preferred.get("change") != "SOLD"
                and priority(item) < priority(preferred)
            ):
                preferred = item
        deduped.append(
            {
                **preferred,
                **_project_context(items, preferred),
                **_listing_context(items, preferred),
            }
        )

    return sorted(
        deduped,
        key=lambda item: (priority(item), item.get("updated_at") or ""),
    )
