import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .parser import (
    ACCIDENT_TERMS,
    classify_accident,
    detect_drivetrain,
    detect_fuel,
    extract_car_id,
    extract_real_encar_id,
    fingerprint_listing,
    parse_insurance_amounts,
    parse_mileage_km,
    parse_new_car_price_percent,
    parse_contract_status,
    parse_options,
    parse_detail_price_krw,
    parse_price_krw,
    parse_condition,
    parse_vehicle_fields,
    parse_year_month,
)

CAPTCHA_MARKERS = ("captcha", "자동입력 방지", "로봇이 아닙니다", "보안문자")
SOLD_MARKERS = (
    "이 차량은 판매되었거나 삭제된 차량입니다",
    "판매되었거나 삭제된 차량",
    "해당 차량은 판매가 완료",
    "판매가 완료된 차량",
    "이미 판매된 차량입니다",
    "판매 종료된 차량입니다",
    "삭제된 차량입니다",
    "존재하지 않는 차량입니다",
)
NO_RESULTS_MARKERS = (
    "검색 결과가 없습니다",
    "조건에 맞는 차량이 없습니다",
    "등록된 차량이 없습니다",
    "검색된 차량이 없습니다",
)


class ScanError(RuntimeError):
    code = "READ_ERROR"


class CaptchaError(ScanError):
    code = "CAPTCHA"


class TimeoutScanError(ScanError):
    code = "TIMEOUT"


class IncompleteDetailError(ScanError):
    code = "INCOMPLETE_PAGE"


class IncompleteSearchError(ScanError):
    code = "INCOMPLETE_SEARCH"


class IncompletePaginationError(ScanError):
    code = "INCOMPLETE_PAGINATION"


class IdentityMismatchError(ScanError):
    code = "IDENTITY_MISMATCH"

    def __init__(self, requested_id: str, resolved_id: str):
        self.requested_id = requested_id
        self.resolved_id = resolved_id
        # Kept for compatibility with existing diagnostics and callers.
        self.displayed_id = resolved_id
        super().__init__(
            f"Encar resolved listing {resolved_id} for requested {requested_id}"
        )


class PriceConfirmationError(ScanError):
    code = "PRICE_NOT_CONFIRMED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _has_captcha(text: str) -> bool:
    lower = text.lower()
    return any(marker.lower() in lower for marker in CAPTCHA_MARKERS)


def is_sold_page(text: str) -> bool:
    return any(marker in (text or "") for marker in SOLD_MARKERS)


def resolve_listing_identity(
    requested_id: str,
    structured_text: str,
    sold: bool = False,
    resolved_url: str | None = None,
) -> tuple[str, str | None]:
    requested_id = str(requested_id)
    displayed_id = extract_real_encar_id(structured_text)
    resolved_id = extract_car_id(resolved_url or "")
    # Encar's URL carId and the page's Korean `등록번호` are different
    # identifiers for the same live listing. Only a redirect to a different
    # URL identity is evidence that Encar substituted another vehicle.
    if not sold and resolved_id and resolved_id != requested_id:
        raise IdentityMismatchError(requested_id, resolved_id)
    return requested_id, displayed_id


def _wait_for_search_results(page, wait_seconds: int = 8) -> None:
    deadline = utcnow().timestamp() + max(wait_seconds, 3)
    while utcnow().timestamp() < deadline:
        if extract_car_id(page.content()):
            break
        page.wait_for_timeout(1000)
    for _ in range(3):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(900)


def _open_page(page, url: str) -> None:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                # Encar keeps analytics connections open; stable content is
                # checked separately before parsing.
                pass
            return
        except (PlaywrightTimeout, PlaywrightError) as exc:
            last_error = exc
            message = str(exc)
            is_timeout = isinstance(exc, PlaywrightTimeout) or any(
                marker in message
                for marker in ("ERR_TIMED_OUT", "Timeout", "timeout")
            )
            if not is_timeout:
                raise
            if attempt == 0:
                page.wait_for_timeout(2500)
    raise TimeoutScanError(str(last_error)) from last_error


def _wait_for_detail_content(page, wait_seconds: int = 14) -> str:
    deadline = utcnow().timestamp() + max(wait_seconds, 5)
    best = ""
    previous_signal = None
    stable_reads = 0
    while utcnow().timestamp() < deadline:
        text = page.locator("body").inner_text(timeout=8_000)
        if len(text) > len(best):
            best = text
        if _has_captcha(text) or is_sold_page(text):
            return text
        signal = (
            parse_detail_price_krw(text),
            parse_mileage_km(text),
            parse_year_month(text),
            parse_new_car_price_percent(text),
            parse_contract_status(text),
            len(text) // 500,
        )
        has_core_fields = bool(signal[0] and signal[1] is not None and len(text) >= 900)
        if has_core_fields and signal == previous_signal:
            stable_reads += 1
        else:
            stable_reads = 0
        if stable_reads >= 2:
            return text
        previous_signal = signal
        page.wait_for_timeout(700)
    return best


@dataclass(frozen=True)
class SearchPage:
    rows: dict[str, dict]
    page_number: int
    total_pages: int
    total_results: int | None
    has_next: bool


@dataclass(frozen=True)
class SearchCollection:
    rows: dict[str, dict]
    pages_visited: int
    total_pages: int
    total_results: int | None


def _fragment_state(url: str) -> dict | None:
    fragment = urlsplit(url).fragment
    if not fragment.startswith("!"):
        return None
    try:
        state = json.loads(unquote(fragment[1:]))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def search_page_number(url: str) -> int | None:
    state = _fragment_state(url)
    if state is not None:
        try:
            return max(1, int(state.get("page") or 1))
        except (TypeError, ValueError):
            return None
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    if "page" not in query:
        return None
    try:
        return max(1, int(query["page"]))
    except (TypeError, ValueError):
        return None


def search_url_for_page(url: str, page_number: int) -> str:
    """Change only pagination state while preserving every Encar search filter."""
    page_number = max(1, int(page_number))
    parts = urlsplit(url)
    state = _fragment_state(url)
    if state is not None:
        state = {**state, "page": page_number, "cursor": None}
        fragment = "!" + quote(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            safe="",
        )
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, parts.query, fragment)
        )
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    query = dict(query_items)
    if "page" not in query and page_number > 1:
        raise IncompletePaginationError(
            "Encar search URL does not expose a page parameter"
        )
    query["page"] = str(page_number)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def _pagination_metadata(page, body_text: str, requested_page: int, limit: int) -> tuple[int, int | None, bool]:
    controls = page.locator("a, button").evaluate_all(
        """els => els.map(el => {
          const container = el.closest(
            '[class*=paginate], [class*=pagination], [class*=paging], ' +
            '[class*=page_navi], [class~="page"]'
          );
          if (!container) return null;
          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          if (style.display === 'none' || style.visibility === 'hidden' ||
              rect.width === 0 || rect.height === 0) return null;
          return {
            text: (el.innerText || el.textContent || '').trim(),
            ariaCurrent: el.getAttribute('aria-current') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            title: el.getAttribute('title') || '',
            rel: el.getAttribute('rel') || '',
            dataPage: el.getAttribute('data-page') || '',
            className: String(el.className || ''),
            disabled: Boolean(el.disabled) ||
              el.getAttribute('aria-disabled') === 'true'
          };
        }).filter(Boolean)"""
    )
    page_numbers: set[int] = set()
    active_page = None
    next_enabled = False
    for control in controls:
        text = str(control.get("text") or "").strip()
        page_value = text if text.isdigit() else str(control.get("dataPage") or "")
        if page_value.isdigit() and 0 < int(page_value) <= 10_000:
            number = int(page_value)
            page_numbers.add(number)
            active_marker = " ".join(
                (
                    str(control.get("ariaCurrent") or ""),
                    str(control.get("className") or ""),
                )
            ).lower()
            if (
                control.get("ariaCurrent") == "page"
                or any(
                    marker in active_marker
                    for marker in ("active", "current", "selected", " on")
                )
            ):
                active_page = number
        next_marker = " ".join(
            (
                text,
                str(control.get("ariaLabel") or ""),
                str(control.get("title") or ""),
                str(control.get("rel") or ""),
                str(control.get("className") or ""),
            )
        ).lower()
        if not control.get("disabled") and any(
            marker in next_marker
            for marker in ("다음", "next", "pagination-next", "paging-next")
        ):
            next_enabled = True

    url_page = search_page_number(str(getattr(page, "url", "") or ""))
    observed_page = active_page or url_page
    if observed_page is not None and observed_page != requested_page:
        raise IncompletePaginationError(
            f"Requested Encar page {requested_page}, but page {observed_page} was displayed"
        )

    total_results = None
    for pattern in (
        r"(?:총|전체)\s*([\d,]+)\s*대",
        r"([\d,]+)\s*대의\s*(?:차량|매물)",
    ):
        match = re.search(pattern, body_text)
        if match:
            total_results = int(match.group(1).replace(",", ""))
            break
    total_pages = max(page_numbers, default=requested_page)
    if total_results is not None and limit > 0:
        total_pages = max(total_pages, math.ceil(total_results / limit))
    has_next = (
        next_enabled
        or requested_page + 1 in page_numbers
        or requested_page < total_pages
    )
    return total_pages, total_results, has_next


def _collect_search_page(
    page,
    url: str,
    search_name: str = "",
    requested_page: int = 1,
) -> SearchPage:
    """Collect one verified result page, using visible listing links only."""
    try:
        _open_page(page, url)
        _wait_for_search_results(page)
        body_text = page.locator("body").inner_text(timeout=15_000)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        if isinstance(exc, ScanError):
            raise
        raise TimeoutScanError(str(exc)) from exc
    if _has_captcha(body_text):
        raise CaptchaError("Encar CAPTCHA detected")

    anchors = page.locator("a").evaluate_all(
        """els => els.map(a => {
          const closest = a.closest('li') || a.closest('tr') ||
            a.closest('[class*=list]') || a.closest('[class*=item]') ||
            a.closest('[class*=card]') || a.parentElement;
          return {
            href: a.href || '',
            text: a.innerText || '',
            closestText: closest ? (closest.innerText || '') : '',
            outerHTML: a.outerHTML || ''
          };
        })"""
    )
    found: dict[str, dict] = {}
    for anchor in anchors:
        href = str(anchor.get("href") or "")
        source_id = extract_car_id(href)
        if not source_id:
            continue
        candidate = {
            "source": search_name,
            "source_car_id": source_id,
            "url": href if href.startswith("http") and "/cars/detail/" in href
            else f"https://fem.encar.com/cars/detail/{source_id}",
            "list_text": str(anchor.get("closestText") or anchor.get("text") or "").strip(),
            "found_at": utcnow().isoformat(),
        }
        previous = found.get(source_id)
        if not previous or len(candidate["list_text"]) > len(previous["list_text"]):
            found[source_id] = candidate

    if not found and not any(marker in body_text for marker in NO_RESULTS_MARKERS):
        raise IncompleteSearchError(
            "Encar search page did not expose listing result links"
        )
    state = _fragment_state(url) or {}
    try:
        limit = max(1, int(state.get("limit") or 20))
    except (TypeError, ValueError):
        limit = 20
    total_pages, total_results, has_next = _pagination_metadata(
        page,
        body_text,
        requested_page,
        limit,
    )
    return SearchPage(
        rows=found,
        page_number=requested_page,
        total_pages=total_pages,
        total_results=total_results,
        has_next=has_next,
    )


def collect_search(page, url: str, search_name: str = "") -> dict[str, dict]:
    """Backward-compatible first-page collection."""
    first_page_url = search_url_for_page(url, 1)
    return _collect_search_page(
        page,
        first_page_url,
        search_name,
        requested_page=1,
    ).rows


def collect_search_pages(
    page,
    url: str,
    search_name: str = "",
    *,
    all_pages: bool = False,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[dict], None] | None = None,
    max_pages: int = 50,
) -> SearchCollection:
    """Collect page 1 or a complete, identity-deduplicated Encar result set."""
    rows: dict[str, dict] = {}
    fingerprints: set[tuple[str, ...]] = set()
    requested_page = 1
    expected_pages = 1
    total_results = None

    while requested_page <= max_pages:
        if checkpoint:
            checkpoint()
        page_url = search_url_for_page(url, requested_page)
        current = _collect_search_page(
            page,
            page_url,
            search_name,
            requested_page=requested_page,
        )
        if requested_page > 1 and not current.rows:
            raise IncompletePaginationError(
                f"Encar page {requested_page} was expected but contained no listings"
            )
        fingerprint = tuple(sorted(current.rows))
        if fingerprint in fingerprints:
            raise IncompletePaginationError(
                f"Encar repeated result page while requesting page {requested_page}"
            )
        fingerprints.add(fingerprint)
        rows.update(current.rows)
        expected_pages = max(expected_pages, current.total_pages)
        if current.total_results is not None:
            total_results = current.total_results

        complete = not all_pages or not current.has_next
        if progress:
            progress(
                {
                    "current_page": requested_page,
                    "total_pages": max(expected_pages, requested_page),
                    "pages_visited": requested_page,
                    "found_count": len(rows),
                    "total_results": total_results,
                    "complete": complete,
                }
            )
        if checkpoint:
            checkpoint()
        if complete:
            return SearchCollection(
                rows=rows,
                pages_visited=requested_page,
                total_pages=max(expected_pages, requested_page),
                total_results=total_results,
            )
        requested_page += 1
        page.wait_for_timeout(800)

    raise IncompletePaginationError(
        f"Encar pagination exceeded the safety limit of {max_pages} pages"
    )


def parse_list_row(item: dict) -> dict:
    text = item.get("list_text") or ""
    return {
        "title": next((line.strip() for line in text.splitlines() if len(line.strip()) > 8), None),
        "price_krw": parse_price_krw(text),
        "mileage_km": parse_mileage_km(text),
        "year_month": parse_year_month(text),
    }


def listing_sections(body_text: str) -> tuple[str, str, str]:
    """Return stable main, options, and condition sections from an Encar page."""
    lines = [" ".join(line.split()) for line in (body_text or "").splitlines()]
    lines = [line for line in lines if line]

    condition_start = next(
        (index for index, line in enumerate(lines) if line == "차량 상태"),
        None,
    )
    seller_start = next(
        (
            index
            for index, line in enumerate(lines)
            if line == "판매자 정보"
            and index > (condition_start if condition_start is not None else 40)
        ),
        None,
    )
    main_end = seller_start if seller_start is not None else len(lines)
    main_text = "\n".join(lines[:main_end])

    options_start = next(
        (index for index, line in enumerate(lines[:main_end]) if line == "주요옵션"),
        None,
    )
    options_end = None
    if options_start is not None:
        options_end = next(
            (
                index + 1
                for index in range(options_start, main_end)
                if "개 옵션 모두보기" in lines[index]
            ),
            main_end,
        )
    options_text = (
        "\n".join(lines[options_start:options_end])
        if options_start is not None and options_end is not None
        else ""
    )

    condition_end = None
    if condition_start is not None:
        condition_end = next(
            (
                index
                for index in range(condition_start + 1, main_end)
                if lines[index] in {"보증 현황", "엔카금융", "판매자 정보"}
            ),
            main_end,
        )
    condition_text = (
        "\n".join(lines[condition_start:condition_end])
        if condition_start is not None and condition_end is not None
        else ""
    )
    return main_text, options_text, condition_text


def _relevant_lines(text: str, terms: list[str], limit: int = 60) -> list[str]:
    result = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean and any(term in clean for term in terms) and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _replace_file(path: Path, content: bytes) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return str(path), hashlib.sha256(content).hexdigest()


def read_detail(page, item: dict, storage: Path) -> dict:
    """Read one detail page and persist only the current screenshot/main image."""
    body_text = ""
    try:
        for attempt in range(2):
            _open_page(page, item["url"])
            _wait_for_detail_content(page, 12)
            for _ in range(4):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(700)
            page.mouse.wheel(0, -10000)
            body_text = _wait_for_detail_content(page, 12)
            if (
                _has_captcha(body_text)
                or is_sold_page(body_text)
                or (
                    parse_detail_price_krw(body_text)
                    and parse_mileage_km(body_text) is not None
                )
            ):
                break
            if attempt == 0:
                page.wait_for_timeout(1800)
        else:
            missing = [
                name
                for name, value in (
                    ("price", parse_detail_price_krw(body_text)),
                    ("mileage", parse_mileage_km(body_text)),
                )
                if value is None
            ]
            raise IncompleteDetailError(
                "Encar detail page did not finish loading: " + ", ".join(missing)
            )
    except ScanError:
        raise
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise TimeoutScanError(str(exc)) from exc
    if _has_captcha(body_text):
        raise CaptchaError("Encar CAPTCHA detected")

    title = page.title()
    full_text = "\n".join((title, item.get("list_text") or "", body_text))
    main_text, options_text, condition_text = listing_sections(body_text)
    structured_text = "\n".join((title, main_text))
    source_id = str(item["source_car_id"])
    sold = is_sold_page(body_text)
    detail_price = parse_detail_price_krw(body_text)
    canonical, displayed_id = resolve_listing_identity(
        source_id,
        structured_text,
        sold,
        resolved_url=page.url,
    )
    accident = classify_accident(condition_text)
    condition, condition_summary = parse_condition(condition_text)
    vehicle = parse_vehicle_fields(structured_text, title)
    options = parse_options(options_text)
    data = {
        "canonical_car_id": canonical,
        "source_car_id": source_id,
        "displayed_car_id": displayed_id,
        "source": item.get("source"),
        "source_url": item["url"],
        "url": f"https://fem.encar.com/cars/detail/{canonical}",
        "title": vehicle["title"] or title,
        "trim": vehicle["trim"],
        "year_month": parse_year_month(structured_text),
        "production_date": vehicle["production_date"],
        "registration_date": vehicle["registration_date"],
        "registration_number": vehicle["registration_number"],
        "vin": vehicle["vin"],
        "mileage_km": parse_mileage_km(structured_text),
        "price_krw": None if sold else detail_price,
        "price_source": None if sold else "DETAIL_PRIMARY",
        "new_car_price_percent": (
            None if sold else parse_new_car_price_percent(full_text)
        ),
        "under_contract": (
            None
            if sold
            else parse_contract_status(
                "\n".join((item.get("list_text") or "", body_text))
            )
        ),
        "fuel": detect_fuel(structured_text),
        "drivetrain": detect_drivetrain(structured_text),
        "transmission": vehicle["transmission"],
        "engine_displacement_cc": vehicle["engine_displacement_cc"],
        "body_type": vehicle["body_type"],
        "exterior_color": vehicle["exterior_color"],
        "interior_color": vehicle["interior_color"],
        "options": options,
        "accident": accident,
        "condition": condition,
        "condition_summary": condition_summary,
        "insurance_amounts": parse_insurance_amounts(condition_text),
        "raw_korean_lines": _relevant_lines(condition_text, ACCIDENT_TERMS),
        "raw_text_excerpt": full_text[:7000],
        "checked_at": utcnow().isoformat(),
        "loaded_sections": {
            "main": bool(main_text),
            "options": bool(options_text),
            "condition": bool(condition_text),
        },
        "parse_quality": "SOLD_PAGE" if sold else "COMPLETE",
        "sold": sold,
        "unavailable": False,
    }
    car_dir = storage / "cars" / canonical
    screenshot_path = car_dir / "screenshot.png"
    screenshot_bytes = page.screenshot(full_page=True)
    data["screenshot_path"], data["screenshot_checksum"] = _replace_file(
        screenshot_path, screenshot_bytes
    )

    og_image = None
    og = page.locator('meta[property="og:image"]')
    if og.count():
        og_image = og.first.get_attribute("content")
    image_candidates = page.locator("img").evaluate_all(
        """images => images.map((img, index) => {
          const rect = img.getBoundingClientRect();
          return {
            index,
            src: img.currentSrc || img.src || '',
            width: img.naturalWidth || rect.width,
            height: img.naturalHeight || rect.height,
            visibleWidth: rect.width,
            visibleHeight: rect.height
          };
        }).filter(item => item.src && item.width >= 480 && item.height >= 260)
          .sort((a, b) => (b.width * b.height) - (a.width * a.height))"""
    )
    candidate_urls = list(
        dict.fromkeys(
            [
                value
                for value in [og_image, *[item.get("src") for item in image_candidates]]
                if value and not str(value).startswith("data:")
            ]
        )
    )
    for image_url in candidate_urls:
        try:
            response = page.request.get(image_url, timeout=20_000)
            if response.ok:
                image_path = car_dir / "main-image.jpg"
                data["main_image_path"], data["main_image_checksum"] = _replace_file(
                    image_path, response.body()
                )
                data["main_image_source_url"] = image_url
                break
        except Exception:
            continue

    if not data.get("main_image_path") and image_candidates:
        try:
            locator = page.locator("img").nth(image_candidates[0]["index"])
            image_bytes = locator.screenshot(type="jpeg", quality=88)
            image_path = car_dir / "main-image.jpg"
            data["main_image_path"], data["main_image_checksum"] = _replace_file(
                image_path, image_bytes
            )
            data["main_image_source_url"] = "browser-crop"
        except Exception:
            data["main_image_source_url"] = og_image

    data["fingerprint"] = fingerprint_listing(data)
    return data
