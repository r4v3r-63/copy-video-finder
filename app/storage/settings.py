"""Настройки программы: ключи API и параметры поиска.

Хранятся в «Данные/settings.json». Ключи в коде не хранятся и не коммитятся.
Встроенные ключи сборка кладёт в defaults.json рядом с exe (из секретов GitHub);
ключ, введённый пользователем, главнее встроенного.
"""

import json
import logging
import math
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app import paths

log = logging.getLogger(__name__)
_SAVE_LOCK = threading.Lock()

SETTINGS_VERSION = 2  # 2 — предел длительности по умолчанию 180 с


@dataclass
class Settings:
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    coverr_api_key: str = ""
    vimeo_token: str = ""  # личный токен доступа Vimeo (права public)
    europeana_api_key: str = ""
    tavily_api_key: str = ""  # Tavily — бесплатный поиск по интернету (1000 в месяц); без ключа — DuckDuckGo
    brave_api_key: str = ""  # Brave Search API — платный, необязательно
    vk_app_id: str = ""  # ID своего приложения VK (dev.vk.com) — для входа
    vk_token: str = ""  # ключ VK после входа в браузере; только здесь, в «Данные/settings.json»
    vk_refresh_token: str = ""  # ключ обновления VK ID: ключ доступа живёт около часа и продлевается сам
    vk_device_id: str = ""
    vk_token_expires: float = 0.0
    min_duration: int = 5
    max_duration: int = 180  # с версии 0.7.0; раньше 45 — на стоках мало коротких роликов о полиграфии
    only_without_attribution: bool = False
    download_dir: str = ""  # пусто — «Данные/Видео»
    allow_4k: bool = False  # по умолчанию скачивается максимум Full HD
    convert_to_mp4: bool = True  # другие форматы — в H.264; иначе перенос потоков в MP4 без перекодирования
    trim_seconds: int = 0  # подготовка к показу: обрезать скачанный ролик до N секунд (0 — не обрезать)
    mute_audio: bool = False  # подготовка к показу: убирать звук из скачанного ролика
    check_updates: bool = True  # проверять новую версию на GitHub при запуске
    link_check_days: int = 7  # раз в сколько дней проверять, не удалены ли ролики с площадок (0 — не проверять)
    theme: str = "system"  # оформление: system — как в Windows, light, dark
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
        if not isinstance(raw, dict):
            log.error("Настройки должны содержать объект JSON")
            return cls()
        known = {f.name for f in fields(cls)}
        # Старый предел по умолчанию (45 с) поднимается до нового; своё значение пользователя не трогаем.
        version = raw.get("settings_version", 0)
        if not isinstance(version, int):
            version = 0
        if raw.get("max_duration") == 45 and version < SETTINGS_VERSION:
            raw["max_duration"] = cls.max_duration
        defaults = cls()
        values = {}
        for key, value in raw.items():
            if key not in known:
                continue
            default = getattr(defaults, key)
            valid = type(value) is type(default)
            if isinstance(default, float):
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            elif type(default) is int:
                valid = type(value) is int and value >= 0
            if valid:
                values[key] = value
        settings = cls(**values)
        settings.max_duration = max(settings.min_duration, settings.max_duration)
        return settings

    def key(self, name: str) -> str:
        """Ключ API: введённый пользователем, иначе встроенный в сборку."""
        return getattr(self, name) or built_in_keys().get(name, "")

    def save(self, path: Path | None = None) -> None:
        path = path or paths.settings_file()
        with _SAVE_LOCK:
            data = {**asdict(self), "settings_version": SETTINGS_VERSION}
            paths.atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


KEY_FIELDS = ("pexels_api_key", "pixabay_api_key", "coverr_api_key", "vimeo_token", "europeana_api_key",
              "tavily_api_key", "brave_api_key")


def built_in_keys(path: Path | None = None) -> dict[str, str]:
    path = path or paths.defaults_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Не удалось прочитать встроенные ключи %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v.strip() for k, v in raw.items() if k in KEY_FIELDS and isinstance(v, str) and v.strip()}
