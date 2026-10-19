"""Общий интерфейс площадки.

Ролик однозначно определяется парой (площадка, ID на площадке). Ссылки на файлы
берутся только из ответа API — конструировать их запрещено.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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

    @abstractmethod
    def search(self, query: str, filters: SearchFilters, page: int = 1) -> list[VideoResult]:
        ...

    @abstractmethod
    def get_details(self, source_id: str) -> VideoResult:
        ...

    @abstractmethod
    def get_license(self, result: VideoResult) -> LicenseInfo:
        ...
