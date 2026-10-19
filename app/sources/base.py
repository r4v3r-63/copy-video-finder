"""Общий интерфейс площадки.

Ролик однозначно определяется парой (площадка, ID на площадке). Ссылки на файлы
берутся только из ответа API — конструировать их запрещено.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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


@dataclass
class VideoFile:
    quality: str  # например, "hd", "fhd", "4k" или подпись из API
    url: str
    width: int | None = None
    height: int | None = None
    mime: str | None = None


@dataclass
class LicenseInfo:
    name: str  # как указано площадкой, например "CC BY-SA 4.0"
    url: str | None = None  # ссылка на условия лицензии
    status: str = "Требует проверки"
    note: str = ""


@dataclass
class VideoResult:
    source: str
    source_id: str
    title: str
    description: str
    preview_url: str | None
    duration: float | None
    width: int | None
    height: int | None
    has_audio: bool | None
    page_url: str
    files: list[VideoFile] = field(default_factory=list)
    author: str = ""
    author_url: str | None = None
    license: LicenseInfo | None = None
    relevance: int = 0  # близость к теме поиска и к полиграфии; по ней сортируется выдача
    # Отдельная ссылка для просмотра, если площадка даёт её отдельно от файлов скачивания (Coverr).
    preview_video: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.source_id)


@dataclass
class SearchFilters:
    min_duration: int = 5
    max_duration: int = 45
    orientation: str = "all"  # "landscape", "portrait", "all"
    min_height: int = 720


class Source(ABC):
    name: str = ""
    homepage: str = ""  # ссылка на площадку показывается рядом с её результатами
    # Искать ли слова темы и в описании ролика, а не только в названии/тегах.
    topic_in_description: bool = True
    # Языки, на которых площадка ищет: запросы на других языках ей не отправляются.
    languages: frozenset[str] = frozenset({"ru", "en"})

    def is_configured(self) -> bool:
        """Есть ли всё нужное для запросов (например, ключ API)."""
        return True

    @abstractmethod
    def search(self, query: str, filters: SearchFilters, page: int = 1, lang: str = "en") -> list[VideoResult]:
        """Поиск по фразе; lang — язык фразы ("ru" или "en") для параметра языка API."""
        ...

    @abstractmethod
    def get_details(self, source_id: str) -> VideoResult:
        ...

    @abstractmethod
    def get_license(self, result: VideoResult) -> LicenseInfo:
        ...
