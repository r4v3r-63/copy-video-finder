"""Разбор ссылок выдачи и локальные фильтры независимого раздела загрузки."""

import math
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlsplit

from app.core.content import is_drawn
from app.core.models import VideoFile, VideoResult


@dataclass
class ListingLink:
    source: str
    params: dict = field(default_factory=dict)
    orientation: str = "all"
    page: int = 1
    warnings: list[str] = field(default_factory=list)
    batch_enabled: bool = False  # возможность ручного запуска очереди, не подтверждение прав источника


def parse_link(url: str) -> ListingLink:
    try:
        parsed = urlsplit(url.strip())
        if (parsed.scheme not in ("https", "http") or parsed.username or parsed.password
                or parsed.port not in (None, 80, 443) or not parsed.hostname
                or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url.strip())):
            raise ValueError()
        host = parsed.hostname.lower().removeprefix("www.")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(values) != 1 for key, values in query.items() if key != "sort[]"):
            raise ValueError("Параметр ссылки задан несколько раз.")
        page = int(query.get("page", query.get("pagi", ["1"]))[0])
        if page < 1:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise ValueError("Некорректная ссылка: нужен HTTP(S)-адрес выдачи с корректным номером страницы.") from exc
    if host == "pixabay.com":
        parts = parsed.path.strip("/").split("/")
        lang = "en"
        if parts and parts[0] in {"en", "ru", "de", "fr", "es", "it", "pt", "ja", "ko", "zh", "nl", "pl", "cs"}:
            lang = parts.pop(0)
        if parts[:2] != ["videos", "search"] or len(parts) > 3:
            raise ValueError("Pixabay: вставьте ссылку на выдачу /videos/search/, а не на отдельный ролик.")
        text = query.get("q", [unquote(parts[2]) if len(parts) == 3 else ""])[0]
        if len(text) > 100:
            raise ValueError("Pixabay API поддерживает запрос не длиннее 100 символов.")
        params = {"q": text, "lang": lang, "video_type": "film", "safesearch": "true", "order": "popular"}
        warnings = []
        for key, allowed in {"video_type": {"all", "film", "animation"}, "order": {"popular", "latest"},
                             "safesearch": {"true", "false"}, "editors_choice": {"true", "false"}}.items():
            if key in query:
                value = query[key][0].lower()
                if value not in allowed:
                    raise ValueError(f"Pixabay: некорректный параметр {key}.")
                params[key] = value
        if params["video_type"] != "film":
            warnings.append("Тип видео из URL заменён на film: аниме и мультяшная анимация исключены.")
            params["video_type"] = "film"
        warnings.append("Аниме и мультяшная анимация исключаются по типу, названию и тегам. "
                        "Ролики без таких меток требуют просмотра.")
        for key in ("min_width", "min_height"):
            if key in query:
                try:
                    number = int(query[key][0])
                    if number < 0:
                        raise ValueError()
                    params[key] = number
                except ValueError as exc:
                    raise ValueError(f"Pixabay: {key} должен быть неотрицательным целым числом.") from exc
        if query.get("category", [""])[0]:
            params["category"] = query["category"][0]
        orient = query.get("orientation", ["all"])[0]
        if orient not in ("horizontal", "vertical", "all", "any", ""):
            raise ValueError("Неизвестная ориентация в ссылке Pixabay.")
        orientation = {"horizontal": "landscape", "vertical": "portrait"}.get(orient, "all")
        if "time_lapse" in query:
            warnings.append("Pixabay Video API не поддерживает фильтр time_lapse: точное воспроизведение невозможно. "
                            "Показаны кандидаты без подтверждения таймлапса; проверьте содержимое самостоятельно.")
        known = set(params) | {"q", "orientation", "page", "pagi", "time_lapse"}
        ignored = sorted(set(query) - known)
        if ignored:
            warnings.append("Не воспроизведены параметры URL: " + ", ".join(ignored) + ".")
        warnings.append("Pixabay API предоставляет не более 500 результатов одного запроса; "
                        "выдача сайта может отличаться.")
        return ListingLink("Pixabay", params, orientation, page, warnings, True)
    if host == "archive.org" and parsed.path.rstrip("/") in ("/search", "/advancedsearch.php"):
        text = query.get("query", query.get("q", [""]))[0].strip()
        if not text:
            raise ValueError("Internet Archive: ссылка должна содержать query или q.")
        warnings = ["Internet Archive: доступны публичные видео с открытой лицензией в метаданных. "
                    "Условия каждого ролика нужно проверить отдельно."]
        ignored = set(query) - {"query", "q", "page", "sort[]", "sort"}
        if ignored:
            warnings.append("Не воспроизведены параметры URL: " + ", ".join(sorted(ignored)) + ".")
        params = {"q": text}
        if sorts := query.get("sort[]", query.get("sort")):
            params["sort[]"] = sorts
        return ListingLink("Internet Archive", params, "all", page, warnings, True)
    raise ValueError("Источник не поддерживается. Доступны выдача видео Pixabay и поиск Internet Archive.")


@dataclass(frozen=True)
class ListingFilters:
    minimum: float | None = None
    maximum: float | None = None
    orientation: str = "all"
    quality: int = 0  # 0 — максимальное; 1080/720 — ближайшее доступное

    def __post_init__(self):
        for number in (self.minimum, self.maximum):
            if number is not None and (not math.isfinite(number) or number < 0):
                raise ValueError("Продолжительность должна быть неотрицательным числом.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Минимальная продолжительность не должна превышать максимальную.")
        if self.orientation not in ("all", "landscape", "portrait") or self.quality not in (0, 720, 1080):
            raise ValueError("Некорректная ориентация или качество.")

    def allows(self, result: VideoResult) -> bool:
        if is_drawn(result):
            return False
        if self.minimum is not None or self.maximum is not None:
            duration = result.duration
            if duration is None or not math.isfinite(duration):
                return False
            if self.minimum is not None and duration < self.minimum:
                return False
            if self.maximum is not None and duration > self.maximum:
                return False
        if self.orientation != "all":
            if not result.width or not result.height:
                return False
            if self.orientation == "landscape" and result.width <= result.height:
                return False
            if self.orientation == "portrait" and result.width >= result.height:
                return False
        return True


def choose_quality(result: VideoResult, quality: int) -> tuple[VideoFile | None, str]:
    files = [file for file in result.files if file.url and not file.page and not file.site]
    if not files:
        return None, "Прямая ссылка отсутствует; загрузчик страницы выберет доступный поток."
    def size(file):
        return min(file.width, file.height) if file.width and file.height else file.height or 0
    if not quality:
        return max(files, key=size), ""
    selected = min(files, key=lambda file: (abs(size(file) - quality), size(file) > quality))
    note = "" if size(selected) == quality else f"Качество {quality}p недоступно: выбрано {selected.quality}."
    return selected, note


def limit_downloads(results: list[VideoResult], count: int | None, *, batch_enabled: bool,
                    completed=frozenset()) -> list[VideoResult]:
    """Ограничить один запуск после фильтров; завершённые ролики повторно не занимают лимит."""
    if count is not None and (type(count) is not int or count < 1):
        raise ValueError("Количество видео должно быть положительным целым числом.")
    unique = {}
    for result in results:
        if result.key not in completed:
            unique.setdefault(result.key, result)
    candidates = list(unique.values())
    selected = candidates if count is None else candidates[:count]
    if not batch_enabled and len(selected) > 1:
        raise ValueError("Источник разрешает только индивидуальную загрузку выбранного ролика.")
    return selected
