"""Пути программы.

Все данные (настройки, ключи API, кэш, журнал, каталог) лежат в папке «Данные»
рядом с exe, а в режиме разработки — рядом с main.py. В реестр и системные
папки программа ничего не пишет.
"""

import os
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


def processes_file() -> Path:
    """Пользовательская копия справочника, если её уже редактировали."""
    return data_dir() / "processes.json"


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(exist_ok=True)
    return path


def log_file() -> Path:
    return data_dir() / "app.log"


def lock_file() -> Path:
    return data_dir() / "app.lock"
