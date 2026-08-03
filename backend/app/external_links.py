from __future__ import annotations

from urllib.parse import urlparse


TELEGRAM_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}


def validated_external_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    is_encar = host == "encar.com" or host.endswith(".encar.com")
    is_telegram = host in TELEGRAM_HOSTS
    if (
        parsed.scheme not in {"http", "https"}
        or not (is_encar or is_telegram)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("unsupported_external_url")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("unsupported_external_url") from exc
    if port not in {None, 80, 443}:
        raise ValueError("unsupported_external_url")
    return url
