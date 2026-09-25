import hashlib
import json
import re
from typing import Any

SUPPORTED_HOSTS = {"encar.com", "www.encar.com", "fem.encar.com", "m.encar.com"}
ACCIDENT_TERMS = [
    "무사고", "사고", "보험이력", "성능점검기록부", "성능점검", "교환", "판금",
    "도색", "주요골격", "골격", "침수", "렌트", "영업용", "소유자변경",
    "내차 피해", "타차 가해", "부식", "프레임 진단", "외부패널 진단",
]


def clean_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in (text or "").splitlines() if line.strip()]


def extract_car_id(value: str):
    for pattern in (
        r"/cars/detail/(\d{6,})",
        r"[?&]car[Ii]d=(\d{6,})",
        r"[?&]carNo=(\d{6,})",
        r"\bcar(?:Id|id|No)['\"]?\s*[:=]\s*['\"]?(\d{6,})",
    ):
        if match := re.search(pattern, value or ""):
            return match.group(1)


def extract_real_encar_id(text: str):
    match = re.search(r"등록번호\s*(\d{6,})", text or "")
    return match.group(1) if match else None


def parse_price_krw(text: str):
    values = [
        int(value.replace(",", "")) * 10_000
        for value in re.findall(
            r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]{3,5})\s*만원", text or ""
        )
        if 500 <= int(value.replace(",", "")) <= 20000
    ]
    values += [
        int(value.replace(",", ""))
        for value in re.findall(
            r"([0-9]{1,3}(?:,[0-9]{3}){2,})\s*원", text or ""
        )
        if 5_000_000 <= int(value.replace(",", "")) <= 300_000_000
    ]
    return values[0] if values else None


def parse_detail_price_krw(text: str):
    """Return only a price anchored to the listing's primary detail section.

    Detail pages also contain prices for options and similar cars. Falling back
    to the first amount on the page can therefore create false price changes.
    """
    amount = r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]{3,5})\s*만원"
    status = r"(?:\s*\([^)\n]{1,40}\))?"
    anchors = (
        rf"{amount}{status}\s*총비용계산기",
        rf"{amount}{status}\s*(?:계약중\s*)?엔카\s*기본정보",
    )
    for pattern in anchors:
        match = re.search(pattern, text or "")
        if match:
            value = int(match.group(1).replace(",", ""))
            if 500 <= value <= 20_000:
                return value * 10_000
    return None


def _labelled_manwon(text: str, label: str):
    match = re.search(
        rf"{re.escape(label)}\s*([0-9][0-9,]*)\s*만원",
        text or "",
    )
    return int(match.group(1).replace(",", "")) * 10_000 if match else None


def parse_rental_terms(text: str):
    """Parse Encar rental pricing without treating it as a sale price."""

    value = text or ""
    payments = list(re.finditer(
        r"월\s*([0-9][0-9,]*)\s*만원(?:\s*/\s*(\d{1,3})\s*개월)?",
        value,
    ))
    term_matches = list(
        re.finditer(r"월\s*렌트료\s*\(\s*(\d{1,3})\s*개월\s*\)", value)
    )
    payment = next(
        (
            candidate
            for candidate in payments
            if (
                candidate.group(2)
                and "렌트" in value[max(0, candidate.start() - 180) : candidate.end() + 180]
            )
            or any(abs(term.start() - candidate.end()) <= 300 for term in term_matches)
        ),
        None,
    )
    if payment is None:
        return None
    term = payment.group(2)
    if not term:
        term_match = next(
            (match for match in term_matches if abs(match.start() - payment.end()) <= 300),
            None,
        )
        term = term_match.group(1) if term_match else None
    return {
        "offer_type": "RENT",
        "rental_monthly_payment_krw": int(payment.group(1).replace(",", "")) * 10_000,
        "rental_term_months": int(term) if term else None,
        "rental_acquisition_price_krw": _labelled_manwon(value, "인수금"),
        "vehicle_price_krw": _labelled_manwon(value, "차량가격"),
    }


def parse_lease_terms(text: str):
    """Parse Encar lease payments from its visible and embedded page state."""

    value = text or ""
    payments = list(re.finditer(
        r"월\s*([0-9][0-9,]*)\s*만원(?:\s*/\s*(\d{1,3})\s*개월)?",
        value,
    ))
    term_matches = list(
        re.finditer(r"월\s*리스료\s*\(\s*(\d{1,3})\s*개월\s*\)", value)
    )
    payment = next(
        (
            candidate
            for candidate in payments
            if (
                candidate.group(2)
                and "리스" in value[max(0, candidate.start() - 180) : candidate.end() + 180]
            )
            or any(abs(term.start() - candidate.end()) <= 300 for term in term_matches)
        ),
        None,
    )
    if payment:
        term_match = next(
            (match for match in term_matches if abs(match.start() - payment.end()) <= 300),
            None,
        )
        return {
            "offer_type": "LEASE",
            "lease_monthly_payment_krw": int(payment.group(1).replace(",", "")) * 10_000,
            "lease_term_months": int(payment.group(2) or term_match.group(1)) if (payment.group(2) or term_match) else None,
        }

    if not re.search(r'"leaseRentType"\s*:\s*"LEASE"', value):
        return None
    info = re.search(r'"leaseRentInfo"\s*:\s*\{(?P<value>[^}]{0,500})\}', value)
    if not info:
        return None
    monthly = re.search(r'"monthlyFee"\s*:\s*(\d+)', info.group("value"))
    term = re.search(r'"residualMonth"\s*:\s*(\d+)', info.group("value"))
    if not monthly:
        return None
    return {
        "offer_type": "LEASE",
        "lease_monthly_payment_krw": int(monthly.group(1)) * 10_000,
        "lease_term_months": int(term.group(1)) if term else None,
    }


def parse_new_car_price_percent(text: str):
    match = re.search(r"신차\s*대비\s*(\d{1,3})\s*%", text or "")
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 100 else None


def parse_contract_status(text: str) -> bool:
    value = text or ""
    amount = r"(?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]{3,5})\s*만원"
    contract = r"(?:\(\s*계약\s*중\s*\)|계약\s*중)"
    if re.search(rf"{amount}\s*{contract}\s*총비용계산기", value):
        return True
    if re.search(rf"{amount}\s*{contract}\s*엔카\s*기본정보", value):
        return True
    # Search-card text is short and contains only the current listing.
    return (
        len(value) <= 800
        and "동급매물" not in value
        and bool(re.search(contract, value))
    )


def parse_mileage_km(text: str):
    values = [
        int(value.replace(",", ""))
        for value in re.findall(
            r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,6})\s*km", text or "", re.I
        )
    ]
    return next((value for value in values if 0 <= value <= 300_000), None)


def parse_year_month(text: str):
    match = re.search(r"(?<!\d)(\d{2})/(0?[1-9]|1[0-2])\s*식", text or "")
    if match:
        return f"{2000 + int(match.group(1))}/{int(match.group(2)):02d}"
    match = re.search(
        r"연식\s*\n?\s*(20[0-9]{2})\s*[./년]\s*(0?[1-9]|1[0-2])", text or ""
    )
    return f"{match.group(1)}/{int(match.group(2)):02d}" if match else None


def detect_fuel(text: str):
    value = text or ""
    labelled = _value_after_label(value, ("연료",), 2)
    candidates = [labelled, "\n".join(clean_lines(value)[:45])]
    for candidate in candidates:
        if not candidate:
            continue
        if any(token in candidate for token in ("하이브리드", "가솔린+전기", "Hybrid")):
            return "HYBRID"
        if "디젤" in candidate:
            return "DIESEL"
        if "가솔린" in candidate:
            return "GASOLINE"
        if "전기" in candidate:
            return "ELECTRIC"
        if "LPG" in candidate.upper():
            return "LPG"
    return "UNVERIFIED"


def detect_drivetrain(text: str):
    upper = text.upper()
    if any(value in upper for value in ("AWD", "4WD")) or "사륜" in text:
        return "AWD"
    if "전륜" in text or "2WD" in upper:
        return "FWD"
    if "후륜" in text:
        return "RWD"
    return "UNVERIFIED"


def _value_after_label(text: str, labels: tuple[str, ...], lookahead: int = 3):
    lines = clean_lines(text)
    for index, line in enumerate(lines):
        for label in labels:
            if line == label:
                for candidate in lines[index + 1 : index + 1 + lookahead]:
                    if candidate not in labels:
                        return candidate
            if line.startswith(label):
                remainder = line[len(label) :].strip(" :·")
                if remainder:
                    return remainder
    return None


def _date_value(text: str, labels: tuple[str, ...]):
    value = _value_after_label(text, labels, 4)
    if not value:
        return None
    match = re.search(r"(20\d{2})[./년-]\s*(\d{1,2})[./월-]?\s*(\d{1,2})?", value)
    if not match:
        return None
    return "-".join(
        part
        for part in (
            match.group(1),
            f"{int(match.group(2)):02d}",
            f"{int(match.group(3)):02d}" if match.group(3) else None,
        )
        if part
    )


def parse_vehicle_fields(text: str, page_title: str = "") -> dict:
    lines = clean_lines(text)
    clean_title = re.sub(r"\s+[가-힣]{2,5}\s+중고차\s*:.*$", "", page_title).strip()
    if not clean_title:
        clean_title = next(
            (
                line
                for line in lines
                if len(line) > 12 and not any(token in line for token in ("내차", "엔카", "Copyright"))
            ),
            "",
        )
    registration_number = _value_after_label(text, ("차량번호",))
    vin_match = re.search(
        r"(?:차대번호|VIN)\s*[:：]?\s*([A-HJ-NPR-Z0-9]{17})", text or "", re.I
    )
    displacement_match = re.search(
        r"(?:배기량|엔진)\s*[:：]?\s*([0-9]{3,5}(?:,[0-9]{3})?)\s*(?:cc|㏄)",
        text or "",
        re.I,
    )
    transmission = _value_after_label(text, ("변속기", "미션"))
    if not transmission:
        if "자동변속기" in text or "오토" in text:
            transmission = "AUTOMATIC"
        elif "수동변속기" in text or "수동" in text:
            transmission = "MANUAL"
    exterior_color = _value_after_label(text, ("외장색", "색상", "색 상"))
    if not exterior_color:
        color_match = re.search(r"(?:^|\n)\s*[-·]?\s*색\s*상\s*[:：]\s*([^\n]+)", text or "")
        exterior_color = color_match.group(1).strip() if color_match else None
    interior_color = _value_after_label(text, ("내장색", "내장 색상"))
    body_type = _value_after_label(text, ("차종", "차체형상", "차체형식"))
    if not body_type:
        body_type = next(
            (
                code
                for keyword, code in (
                    ("SUV", "SUV"),
                    ("세단", "SEDAN"),
                    ("쿠페", "COUPE"),
                    ("해치백", "HATCHBACK"),
                    ("왜건", "WAGON"),
                    ("픽업", "PICKUP"),
                )
                if keyword.lower() in text.lower()
            ),
            None,
        )
    return {
        "title": clean_title or page_title,
        "trim": clean_title,
        "production_date": parse_year_month(text),
        "registration_date": _date_value(text, ("최초등록일", "등록일자", "등록일")),
        "registration_number": registration_number,
        "vin": vin_match.group(1) if vin_match else None,
        "transmission": transmission,
        "engine_displacement_cc": (
            int(displacement_match.group(1).replace(",", "")) if displacement_match else None
        ),
        "body_type": body_type,
        "exterior_color": exterior_color,
        "interior_color": interior_color,
    }


def _evidence(text: str, terms: tuple[str, ...], limit: int = 12) -> list[str]:
    result: list[str] = []
    lines = clean_lines(text)
    for index, line in enumerate(lines):
        if any(term in line for term in terms):
            combined = " · ".join(lines[index : min(index + 2, len(lines))])
            if combined not in result:
                result.append(combined)
        if len(result) >= limit:
            break
    return result


def _condition(status: str, evidence: list[str], value: Any = None) -> dict:
    result = {"status": status, "original_evidence": evidence}
    if value is not None:
        result["value"] = value
    return result


def _payment(text: str, label: str) -> tuple[int | None, int | None, list[str]]:
    pattern = rf"{re.escape(label)}\s*(?:\n|\s)+(?:총\s*)?([0-9,]+)\s*원\s*\((\d+)\s*회\)"
    match = re.search(pattern, text or "")
    evidence = _evidence(text, (label,), 4)
    if match:
        return int(match.group(1).replace(",", "")), int(match.group(2)), evidence
    if re.search(rf"{re.escape(label)}\s*(?:\n|\s)+없음", text or ""):
        return 0, 0, evidence
    return None, None, evidence


def _binary_condition(
    text: str,
    terms: tuple[str, ...],
    positive: tuple[str, ...] = ("있음", "유", "확인", "교환", "판금", "도색"),
    negative: tuple[str, ...] = ("없음", "무", "정상", "미해당"),
) -> dict:
    evidence = _evidence(text, terms)
    joined = "\n".join(evidence)
    if any(re.search(rf"{re.escape(term)}.*(?:{'|'.join(map(re.escape, positive))})", joined) for term in terms):
        if not any(re.search(rf"{re.escape(term)}.*(?:없음|무사고|정상)", joined) for term in terms):
            return _condition("CONFIRMED", evidence)
    if any(re.search(rf"{re.escape(term)}.*(?:{'|'.join(map(re.escape, negative))})", joined) for term in terms):
        return _condition("NOT_FOUND", evidence)
    return _condition("UNVERIFIED", evidence)


def parse_condition(text: str) -> tuple[dict, str]:
    own_amount, own_count, own_evidence = _payment(text, "내차 피해")
    third_amount, third_count, third_evidence = _payment(text, "타차 가해")
    counts = [value for value in (own_count, third_count) if value is not None]
    amounts = [value for value in (own_amount, third_amount) if value is not None]
    claim_count = sum(counts) if counts else None
    total_amount = sum(amounts) if amounts else None
    insurance_evidence = list(dict.fromkeys(own_evidence + third_evidence))
    insurance_status = (
        "CONFIRMED"
        if claim_count and claim_count > 0
        else "NOT_FOUND"
        if claim_count == 0
        else "UNVERIFIED"
    )
    owner_match = re.search(r"소유자\s*변경(?:이력)?\s*(?:\n|\s)*(?:총\s*)?(\d+)\s*회", text or "")
    owner_count = int(owner_match.group(1)) if owner_match else None
    explicit_no_accident = bool(re.search(r"(?:무사고 확인|무사고 진단)", text or ""))
    explicit_accident = bool(re.search(r"(?<!무)(?:사고차량|사고차|유사고)", text or ""))

    condition = {
        "insurance_events": _condition(insurance_status, insurance_evidence, claim_count),
        "insurance_claim_count": _condition(insurance_status, insurance_evidence, claim_count),
        "total_insurance_amount": _condition(insurance_status, insurance_evidence, total_amount),
        "own_vehicle_payments": _condition(
            "CONFIRMED" if own_count and own_count > 0 else "NOT_FOUND" if own_count == 0 else "UNVERIFIED",
            own_evidence,
            own_amount,
        ),
        "third_party_payments": _condition(
            "CONFIRMED" if third_count and third_count > 0 else "NOT_FOUND" if third_count == 0 else "UNVERIFIED",
            third_evidence,
            third_amount,
        ),
        "replaced_panels": _binary_condition(text, ("교환", "외부패널 진단")),
        "body_repair": _binary_condition(text, ("판금",)),
        "paint_work": _binary_condition(text, ("도색",)),
        "structural_damage": _binary_condition(text, ("주요골격", "프레임 진단", "골격")),
        "flood_history": _binary_condition(text, ("침수",)),
        "rental_use": _binary_condition(text, ("렌트", "대여용")),
        "commercial_use": _binary_condition(text, ("영업용",)),
        "owner_changes": _condition(
            "CONFIRMED" if owner_count and owner_count > 0 else "NOT_FOUND" if owner_count == 0 else "UNVERIFIED",
            _evidence(text, ("소유자 변경", "소유자변경")),
            owner_count,
        ),
        "inspection_report": _condition(
            "CONFIRMED" if "성능점검을 등록한 차량" in text else "UNVERIFIED",
            _evidence(text, ("성능점검", "성능기록부")),
        ),
    }
    dangerous = any(
        condition[key]["status"] == "CONFIRMED"
        for key in ("structural_damage", "flood_history")
    )
    if explicit_accident or dangerous:
        summary = "ACCIDENT_VEHICLE"
    elif insurance_status == "CONFIRMED":
        summary = "INSURANCE_CLAIM"
    elif explicit_no_accident and not dangerous:
        summary = "NO_PROBLEMS_STATED"
    else:
        summary = "UNVERIFIED"
    return condition, summary


def classify_accident(text: str):
    condition, summary = parse_condition(text)
    status = "CONFIRMED" if summary in {"ACCIDENT_VEHICLE", "INSURANCE_CLAIM"} else (
        "NOT_FOUND" if summary == "NO_PROBLEMS_STATED" else "UNVERIFIED"
    )
    return {
        "status": status,
        "summary": summary,
        "original_evidence": list(
            dict.fromkeys(
                line
                for item in condition.values()
                for line in item.get("original_evidence", [])
            )
        )[:60],
    }


def parse_insurance_amounts(text: str):
    return [
        int(value.replace(",", ""))
        for value in re.findall(r"([0-9]{1,3}(?:,[0-9]{3})+)\s*원", text or "")
    ]


def parse_options(text: str) -> dict:
    options: dict[str, bool | int] = {}
    labels = {
        "sunroof": ("선루프",),
        "led_headlights": ("LED 헤드램프", "LED헤드램프", "LED 라이트", "LED라이트"),
        "parking_sensors": ("주차감지센서", "주차 감지 센서"),
        "rear_camera": ("후방카메라", "후방 카메라"),
        "automatic_climate": ("자동에어컨", "자동 에어컨", "풀오토 에어컨"),
        "smart_key": ("스마트키", "스마트 키"),
        "navigation": ("내비게이션",),
        "heated_seat": ("열선시트", "열선 시트"),
        "ventilated_seat": ("통풍시트", "통풍 시트"),
        "leather_seat": ("가죽시트", "가죽 시트", "천연가죽 시트"),
        "memory_seat": (
            "메모리 시트",
            "메모리시트",
            "운전석 메모리",
            "앞좌석메모리",
            "앞좌석 메모리",
            "자세 메모리",
            "메모리 시스템",
            "메모리시스템",
            "전동 + 메모리",
        ),
        "power_tailgate": ("파워 전동 트렁크", "전동 트렁크", "파워트렁크"),
    }
    lines = clean_lines(text)
    negative_markers = ("없음", "미적용", "미장착", "해당없음", "선택안함")
    for key, names in labels.items():
        detected: bool | None = None
        for index, line in enumerate(lines):
            compact_line = re.sub(r"\s+", "", line)
            for name in names:
                compact_name = re.sub(r"\s+", "", name)
                if compact_name not in compact_line:
                    continue
                context = " ".join(lines[index : index + 2])
                detected = not any(marker in context for marker in negative_markers)
                break
            if detected is not None:
                break
        if detected is not None:
            options[key] = detected
    count_match = re.search(r"(\d+)개 옵션 모두보기", text or "")
    if count_match:
        options["reported_count"] = int(count_match.group(1))
    return options


TRACKED_DETAIL_FIELDS = (
    "title",
    "trim",
    "year_month",
    "mileage_km",
    "fuel",
    "drivetrain",
    "transmission",
    "engine_displacement_cc",
    "body_type",
    "exterior_color",
    "interior_color",
    "registration_number",
    "condition_summary",
    "new_car_price_percent",
    "under_contract",
    "offer_type",
    "rental_monthly_payment_krw",
    "rental_term_months",
    "rental_acquisition_price_krw",
    "vehicle_price_krw",
    "lease_monthly_payment_krw",
    "lease_term_months",
)


def material_changes(old: dict, new: dict) -> list[dict]:
    old = {**old, "offer_type": old.get("offer_type") or "SALE"}
    new = {**new, "offer_type": new.get("offer_type") or "SALE"}
    changes = [
        {"field": key, "old": old.get(key), "new": new.get(key)}
        for key in TRACKED_DETAIL_FIELDS
        if old.get(key) != new.get(key)
    ]
    old_options = old.get("options") or {}
    new_options = new.get("options") or {}
    changed_options = [
        key
        for key in sorted(set(old_options) | set(new_options))
        if old_options.get(key) != new_options.get(key)
    ]
    if changed_options:
        changes.append({"field": "options", "old": None, "new": changed_options[:12]})
    old_condition = old.get("condition") or {}
    new_condition = new.get("condition") or {}
    changed_condition = [
        key
        for key in sorted(set(old_condition) | set(new_condition))
        if _condition_value(old_condition.get(key))
        != _condition_value(new_condition.get(key))
    ]
    if changed_condition:
        changes.append(
            {"field": "condition", "old": None, "new": changed_condition[:12]}
        )
    return changes[:12]


def _condition_value(value: Any):
    if not isinstance(value, dict):
        return value
    return {
        key: value.get(key)
        for key in ("status", "value")
        if key in value
    }


def condition_signature(condition: dict | None) -> dict:
    return {
        key: _condition_value(value)
        for key, value in (condition or {}).items()
    }


def accident_signature(accident: dict | None) -> dict:
    value = accident or {}
    return {
        "status": value.get("status"),
        "summary": value.get("summary"),
        "condition": condition_signature(value.get("condition")),
    }


def fingerprint_listing(item: dict[str, Any]):
    keys = (
        "canonical_car_id", "title", "year_month", "mileage_km", "price_krw",
        "fuel", "drivetrain", "transmission", "options", "condition",
        "offer_type", "rental_monthly_payment_krw", "rental_term_months",
        "rental_acquisition_price_krw", "vehicle_price_krw",
        "lease_monthly_payment_krw", "lease_term_months",
    )
    comparable = {key: item.get(key) for key in keys}
    comparable["offer_type"] = item.get("offer_type") or "SALE"
    comparable["condition"] = condition_signature(item.get("condition"))
    return hashlib.sha256(
        json.dumps(
            comparable,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def price_change(old: int | None, new: int | None):
    if not old or not new or old == new:
        return None
    return {
        "type": "PRICE_DROP" if new < old else "PRICE_INCREASE",
        "old": old,
        "new": new,
        "difference": abs(new - old),
        "percent": round(abs(new - old) / old * 100, 2),
    }


def should_read_detail(
    mode: str, is_new=False, list_changed=False, incomplete=False, manual=False
):
    return mode == "ACCURATE" or is_new or list_changed or incomplete or manual


def detail_read_kind(
    mode: str,
    *,
    is_new: bool = False,
    list_price_changed: bool = False,
    list_price_missing: bool = False,
    missing_from_search: bool = False,
    manual: bool = False,
) -> str | None:
    """Choose the smallest Encar read that can satisfy a scan decision."""

    if mode == "ACCURATE" or is_new or manual:
        return "FULL"
    if list_price_changed or list_price_missing or missing_from_search:
        return "PRICE_STATUS"
    return None


def apply_missing(success: bool, found: bool, count: int, status: str):
    """Advance project-local search state.

    Search membership is not proof of listing availability. In particular,
    repeated search misses must never promote a listing to SOLD.
    """
    if not success:
        return count, status
    if found:
        return 0, "RELISTED" if status == "NOT_FOUND_IN_SEARCH" else "FOUND"
    count += 1
    return count, "NOT_FOUND_IN_SEARCH"


def merge_aliases(records: list[dict]):
    merged = {}
    for record in records:
        canonical = record.get("canonical_car_id") or record.get("car_id")
        base = merged.setdefault(canonical, {"canonical_car_id": canonical, "aliases": []})
        base.update(
            {key: value for key, value in record.items() if value not in (None, "", [])}
        )
        base["aliases"] = sorted(
            set(
                base.get("aliases", [])
                + [
                    value
                    for value in (record.get("source_car_id"), record.get("car_id"))
                    if value and value != canonical
                ]
            )
        )
    return list(merged.values())
