"""Настройки программы: ключи API и параметры поиска.

Хранятся в «Данные/settings.json». Ключи в коде не хранятся и не коммитятся.
"""

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app import paths

log = logging.getLogger(__name__)


@dataclass
class Settings:
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    min_duration: int = 5
    max_duration: int = 45
    only_without_attribution: bool = False
    download_dir: str = ""  # пусто — «Данные/Видео»
    allow_4k: bool = False  # по умолчанию скачивается максимум Full HD
    convert_to_mp4: bool = True  # WebM/OGV с Wikimedia конвертируются в MP4

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or paths.settings_file()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Не удалось прочитать настройки %s: %s", path, exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or paths.settings_file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
