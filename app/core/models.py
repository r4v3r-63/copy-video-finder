"""Данные о роликах, общие для площадок, каталога, поиска и скачивания.

Ролик однозначно определяется парой (площадка, ID на площадке) — `VideoResult.key`.
"""

from dataclasses import dataclass, field


@dataclass
class VideoFile:
    quality: str  # например, "hd", "fhd", "4k" или подпись из API
    url: str
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    # url — страница ролика: ссылку на файл программа берёт с неё при скачивании (Videezy:
    # ссылка живёт только в сессии сайта). Источник умеет это в resolve_download().
    page: bool = False


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
    # Степень соответствия, которую подсказывает уровень запроса, нашедшего ролик
    # («смежный процесс» — нашёлся только по похожим операциям). Пусто — «точное».
    match_hint: str = ""
    found_by: str = ""  # запрос, по которому ролик нашёлся первым
    # Лицензия не свободная («все права защищены», NC, ND, не распознана): показывается только по
    # кнопке, чтобы посмотреть и договориться с автором; скачивать и засчитывать в покрытие нельзя.
    view_only: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.source_id)


@dataclass
class SearchFilters:
    min_duration: int = 5
    max_duration: int = 45
    orientation: str = "landscape"  # только горизонтальные; "portrait", "all" — для тестов и будущих настроек
    min_height: int = 720
