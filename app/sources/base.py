"""Общий интерфейс площадки.

Ролик однозначно определяется парой (площадка, ID на площадке). Ссылки на файлы
берутся только из ответа API — конструировать их запрещено.
"""

from abc import ABC, abstractmethod

from app.core.models import LicenseInfo, SearchFilters, VideoResult


class SourceError(Exception):
    """Ошибка площадки; текст — понятное пользователю сообщение на русском."""

    def __init__(self, source: str, message: str):
        super().__init__(message)
        self.source = source
        self.message = message


class AuthError(SourceError):
    """Ключ не задан или неверный."""


class RateLimitError(SourceError):
    """Лимит запросов исчерпан."""


class NetworkError(SourceError):
    """Нет связи с площадкой."""


class NotFoundError(SourceError):
    """Ролик удалён с площадки."""


class Source(ABC):
    name: str = ""
    homepage: str = ""  # ссылка на площадку показывается рядом с её результатами
    # Искать ли слова темы и в описании ролика, а не только в названии/тегах.
    topic_in_description: bool = True
    # Языки, на которых площадка ищет: запросы на других языках ей не отправляются.
    languages: frozenset[str] = frozenset({"ru", "en", "de", "fr"})
    # До какого уровня расширения поиска площадке отправлять запросы (у Coverr лимит 50 в час).
    max_level: int = 99

    def is_configured(self) -> bool:
        """Есть ли всё нужное для запросов (например, ключ API)."""
        return True

    @abstractmethod
    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        """Поиск по фразе; lang — язык фразы ("ru", "en", "de", "fr") для параметра языка API."""
        ...

    @abstractmethod
    def get_details(self, source_id: str) -> VideoResult:
        ...

    @abstractmethod
    def get_license(self, result: VideoResult) -> LicenseInfo:
        ...
