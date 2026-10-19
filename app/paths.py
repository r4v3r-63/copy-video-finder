"""Пути программы.

Все данные (настройки, ключи API, кэш, журнал, каталог) лежат в папке «Данные»
рядом с exe, а в режиме разработки — рядом с main.py. В реестр и системные
папки программа ничего не пишет.
"""

import os
import shutil
import sys
from pathlib import Path

DATA_DIR_NAME = "Данные"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Папка с exe (сборка) или с main.py (разработка)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resources_dir() -> Path:
    """Встроенные ресурсы: справочник услуг, словарь."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir())) / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def data_dir() -> Path:
    """Папка «Данные»; создаётся при первом обращении.

    Переменная COPYVF_DATA_DIR переопределяет путь (нужна для тестов).
    """
    override = os.environ.get("COPYVF_DATA_DIR")
    path = Path(override) if override else app_dir() / DATA_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return data_dir() / "settings.json"


def terms_file() -> Path:
    """Пользовательская копия словаря перевода (редактор словаря)."""
    return data_dir() / "terms_ru_en.json"


def processes_file() -> Path:
    """Пользовательская копия справочника, если её уже редактировали."""
    return data_dir() / "processes.json"


def defaults_file() -> Path:
    """Встроенные ключи API: сборка кладёт их рядом с exe из секретов GitHub, в репозитории их нет."""
    return app_dir() / "defaults.json"


def default_download_dir() -> Path:
    return data_dir() / "Видео"


def ffmpeg_tool(name: str) -> Path | None:
    """FFmpeg из папки программы («ffmpeg» рядом с exe или в корне проекта), иначе из PATH."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    candidates = [app_dir() / "ffmpeg" / exe]
    if not is_frozen():
        candidates.append(app_dir().parent / "ffmpeg" / exe)
    for path in candidates:
        if path.is_file():
            return path
    found = shutil.which(name)
    return Path(found) if found else None


def log_file() -> Path:
    return data_dir() / "app.log"


def lock_file() -> Path:
    return data_dir() / "app.lock"
