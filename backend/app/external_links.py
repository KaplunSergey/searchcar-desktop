from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .sources.registry import source_catalog


TELEGRAM_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}


def validated_source_image_url(source_key: str, value) -> str | None:
    """Browser preview only; domains are source metadata, not arbitrary URLs."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
        domains = next((item.get('image_domains',[]) for item in source_catalog() if item['key']==source_key),[])
        if (parsed.scheme=='https' and parsed.hostname in domains and not parsed.username and not parsed.password
                and parsed.port in (None,443) and not any(ord(c)<32 for c in value)):
            return value
    except ValueError:
        pass
    return None


def validated_external_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("unsupported_external_url") from exc
    is_encar = host == "encar.com" or host.endswith(".encar.com")
    is_telegram = host in TELEGRAM_HOSTS
    query = parse_qs(parsed.query, keep_blank_values=True)
    is_calendar = (parsed.scheme == "https" and host == "calendar.google.com"
                   and port in (None, 443) and not any(ord(char) < 32 or ord(char) == 127 for char in url)
                   and parsed.path == "/calendar/render" and not parsed.fragment
                   and query.get("action") == ["TEMPLATE"]
                   and set(query) <= {"action", "text", "dates", "details"}
                   and all(len(values) == 1 for values in query.values()))
    is_calendar_page = (parsed.scheme == "https" and host == "searchcar-auction-calendar-preview.pages.dev"
                        and port in (None, 443) and parsed.path == "/" and not parsed.query and not parsed.params
                        and bool(parsed.fragment) and len(url) <= 2048
                        and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    is_source = parsed.scheme == "https" and any(
        host in source["domains"] for source in source_catalog()
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not (is_encar or is_telegram or is_source or is_calendar or is_calendar_page)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("unsupported_external_url")
    if port not in {None, 80, 443}:
        raise ValueError("unsupported_external_url")
    return url
