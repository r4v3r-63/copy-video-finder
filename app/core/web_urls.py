"""Допустимые адреса встроенного просмотра, без зависимости от браузера и Qt."""

from urllib.parse import urlsplit


def allowed_page(url: str) -> bool:
    """Во встроенном просмотре открываются только веб-страницы, без внешних протоколов и файлов."""
    try:
        parts = urlsplit(url)
        _port = parts.port
        return bool(parts.scheme in ("https", "http") and parts.hostname
                    and parts.username is None and parts.password is None
                    and not any(c.isspace() or ord(c) < 32 or c == "\\" for c in url))
    except ValueError:
        return False

