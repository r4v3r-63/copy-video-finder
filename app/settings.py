"""Настройки программы: ключи API и параметры поиска.

Хранятся в «Данные/settings.json». Ключи в коде не хранятся и не коммитятся.
Встроенные ключи сборка кладёт в defaults.json рядом с exe (из секретов GitHub);
ключ, введённый пользователем, главнее встроенного.
"""

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app import paths

log = logging.getLogger(__name__)

SETTINGS_VERSION = 2  # 2 — предел длительности по умолчанию 180 с


@dataclass
class Settings:
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    coverr_api_key: str = ""
    vimeo_token: str = ""  # личный токен доступа Vimeo (права public)
    europeana_api_key: str = ""
    min_duration: int = 5
    max_duration: int = 180  # с версии 0.7.0; раньше 45 — на стоках мало коротких роликов о полиграфии
    only_without_attribution: bool = False
    download_dir: str = ""  # пусто — «Данные/Видео»
    allow_4k: bool = False  # по умолчанию скачивается максимум Full HD
    convert_to_mp4: bool = True  # WebM/OGV с Wikimedia конвертируются в MP4
    check_updates: bool = True  # проверять новую версию на GitHub при запуске
    link_check_days: int = 7  # раз в сколько дней проверять, не удалены ли ролики с площадок (0 — не проверять)
    skipped_version: str = ""  # версия, которую пользователь попросил не предлагать

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
        # Старый предел по умолчанию (45 с) поднимается до нового; своё значение пользователя не трогаем.
        if raw.get("max_duration") == 45 and raw.get("settings_version", 0) < SETTINGS_VERSION:
            raw["max_duration"] = cls.max_duration
        return cls(**{k: v for k, v in raw.items() if k in known})

    def key(self, name: str) -> str:
        """Ключ API: введённый пользователем, иначе встроенный в сборку."""
        return getattr(self, name) or built_in_keys().get(name, "")

    def save(self, path: Path | None = None) -> None:
        path = path or paths.settings_file()
        tmp = path.with_suffix(".tmp")
        data = {**asdict(self), "settings_version": SETTINGS_VERSION}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


KEY_FIELDS = ("pexels_api_key", "pixabay_api_key", "coverr_api_key", "vimeo_token", "europeana_api_key")


def built_in_keys(path: Path | None = None) -> dict[str, str]:
    path = path or paths.defaults_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Не удалось прочитать встроенные ключи %s: %s", path, exc)
        return {}
    return {k: str(v).strip() for k, v in raw.items() if k in KEY_FIELDS and str(v).strip()}
