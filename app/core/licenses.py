"""Автоматическая предварительная оценка лицензий (таблица раздела 4 ТЗ).

Окончательное подтверждение всегда за пользователем.
"""

import re
from urllib.parse import urlsplit

ALLOWED = "Разрешено"
ATTRIBUTION = "Разрешено с атрибуцией"
REJECTED = "Отклонено"
REVIEW = "Требует проверки"

# Статусы, которые показываются в результатах поиска.
SUITABLE = {ALLOWED, ATTRIBUTION}

SHARE_ALIKE_NOTE = "Смонтированный ролик тоже должен распространяться под BY-SA."

_REJECT_WORDS = {"NC", "ND", "NONCOMMERCIAL", "NODERIVS", "NODERIVATIVES", "EDITORIAL", "PERSONAL", "PREMIUM", "PAID",
                 "PRO"}  # Videezy Pro License — платная
_REJECT_PHRASES = ("NON COMMERCIAL", "NO DERIVATIVES", "NO DERIVS", "ПЛАТН", "РЕДАКЦИОН", "ЛИЧН")


def _tokens(name: str) -> tuple[str, list[str]]:
    text = re.sub(r"[-_/(),.]+", " ", name.upper())
    text = " ".join(text.split())
    return text, text.split()


def evaluate(name: str | None) -> tuple[str, str]:
    """Статус и пояснение для названия лицензии, как его отдаёт площадка."""
    if not name or not name.strip():
        return REVIEW, "Лицензия не указана."
    text, words = _tokens(name)
    if _REJECT_WORDS & set(words) or any(p in text for p in _REJECT_PHRASES):
        return REJECTED, "Запрет коммерческого использования, изменений или ограниченная лицензия."
    if text in ("PEXELS LICENSE", "PEXELS", "COVERR LICENSE", "COVERR") or text.startswith("PIXABAY"):
        return ALLOWED, ""
    if "CC0" in words or "PUBLIC DOMAIN" in text or (words and words[0] in ("PD", "PDM")):
        return ALLOWED, ""
    if text == "VIDEEZY FREE LICENSE":  # коммерческое использование с указанием Videezy.com
        return ATTRIBUTION, ""
    if text.startswith("CC BY SA") or text.startswith("CREATIVE COMMONS ATTRIBUTION SHARE ALIKE"):
        return ATTRIBUTION, SHARE_ALIKE_NOTE
    if text.startswith("CC BY") or text.startswith("CREATIVE COMMONS ATTRIBUTION"):
        return ATTRIBUTION, ""
    return REVIEW, "Лицензия не распознана — проверьте условия вручную."


_PAID = re.compile(r"\b(PREMIUM|PRO|PAID|EDITORIAL)\b|ПЛАТН|РЕДАКЦИОН")


def is_paid(name: str | None) -> bool:
    """Платная или редакционная лицензия: такие ролики не показываются даже «только посмотреть»."""
    return bool(_PAID.search((name or "").upper()))


def attribution_text(author: str, title: str, license_name: str, url: str) -> str:
    """Готовый текст авторства: «Автор — Название — Лицензия — ссылка»."""
    return " — ".join(part for part in (author, title, license_name, url) if part)


_CC_HOSTS = {"creativecommons.org", "www.creativecommons.org"}
_CC_CODES = {"by", "by-sa", "by-nd", "by-nc", "by-nc-sa", "by-nc-nd", "by-sa-nc", "by-nd-nc"}
_CC_PATH = re.compile(r"/(licenses|publicdomain)/([a-z\-]+)(?:/([\d.]+))?(?:/.*)?", re.IGNORECASE)


def name_from_url(url: str | None) -> str:
    """Название лицензии по ссылке на Creative Commons: «CC BY-SA 4.0», «CC0», «Public Domain».

    Для прочих ссылок (rightsstatements.org и т.п.) — пустая строка: такая лицензия «требует проверки».
    """
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    # Только настоящий домен Creative Commons: «not-creativecommons.org» или ссылка в параметре — не лицензия.
    if parts.scheme.lower() not in ("http", "https", "") or (parts.hostname or "") not in _CC_HOSTS:
        return ""
    m = _CC_PATH.fullmatch(parts.path)
    if not m:
        return ""
    kind, code, version = m.group(1).lower(), m.group(2).lower(), m.group(3) or ""
    if kind == "publicdomain":
        return "CC0" if code == "zero" else "Public Domain" if code == "mark" else ""
    if code not in _CC_CODES:
        return ""
    return f"CC {code.upper()} {version}".strip()
