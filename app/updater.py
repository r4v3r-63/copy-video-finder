"""Автообновление: проверка последнего выпуска на GitHub и замена файлов программы.

Запущенный exe в Windows заменить нельзя, поэтому новая версия распаковывается
в «Данные/update», программа запускает командный файл и закрывается; он ждёт
её выхода, копирует новые файлы поверх старых (robocopy) и запускает программу.
Папка «Данные» с настройками, ключами и роликами не трогается.
"""

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app import __version__, paths
from app.downloader import fetch

log = logging.getLogger(__name__)

REPO = "r4v3r-63/copy-video-finder"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
EXE_NAME = "CopyVideoFinder.exe"
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str  # «0.4.1»
    notes: str
    page_url: str
    zip_url: str
    zip_name: str
    sha256_url: str | None


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text or "")
    return tuple(int(x) for x in match.groups()) if match else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return bool(new and old and new > old)


def parse_release(data: dict) -> Release | None:
    """Выпуск с ZIP для Windows; None — если это черновик, предварительный выпуск или ZIP нет."""
    if data.get("draft") or data.get("prerelease"):
        return None
    version = parse_version(data.get("tag_name", ""))
    assets = data.get("assets") or []
    zip_asset = next((a for a in assets if a.get("name", "").endswith("-windows.zip")), None)
    if not version or not zip_asset:
        return None
    sha = next((a for a in assets if a.get("name") == zip_asset["name"] + ".sha256"), None)
    return Release(
        version=".".join(map(str, version)),
        notes=(data.get("body") or "").strip(),
        page_url=data.get("html_url") or RELEASES_PAGE,
        zip_url=zip_asset["browser_download_url"],
        zip_name=zip_asset["name"],
        sha256_url=sha["browser_download_url"] if sha else None,
    )


def check_latest(session, current: str = __version__) -> Release | None:
    """Новый выпуск на GitHub или None. Ошибки сети не мешают работе — просто нет обновления."""
    resp = session.get(LATEST_URL, headers={"Accept": "application/vnd.github+json"}, timeout=15)
    if resp.status_code != 200:
        log.info("Проверка обновлений: GitHub ответил %s", resp.status_code)
        return None
    release = parse_release(resp.json())
    if release and is_newer(release.version, current):
        return release
    return None


def can_self_update() -> bool:
    """Заменять файлы умеет только собранная программа для Windows."""
    return paths.is_frozen() and sys.platform == "win32"


def update_dir() -> Path:
    return paths.data_dir() / "update"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(release: Release, session, progress=None, cancel=None) -> Path:
    """Скачать ZIP выпуска только с опубликованной контрольной суммой и проверить её."""
    if not release.sha256_url:
        raise UpdateError("У выпуска нет контрольной суммы. Откройте страницу выпуска для ручного скачивания.")
    if release.zip_name != f"CopyVideoFinder-v{release.version}-windows.zip":
        raise UpdateError("Недопустимое имя файла обновления.")
    if any(urlsplit(url).scheme != "https" for url in (release.zip_url, release.sha256_url)):
        raise UpdateError("Ссылки обновления должны использовать HTTPS.")
    resp = session.get(release.sha256_url, timeout=15)
    expected = (resp.text.split() or [""])[0].lower() if resp.status_code == 200 else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateError("Не удалось получить контрольную сумму обновления.")
    target = update_dir()
    target.mkdir(parents=True, exist_ok=True)
    zip_path = target / release.zip_name
    zip_path.unlink(missing_ok=True)
    fetch(release.zip_url, zip_path, session, progress, cancel)
    if sha256_of(zip_path) != expected:
        zip_path.unlink(missing_ok=True)
        raise UpdateError("Контрольная сумма обновления не совпала — файл повреждён при скачивании.")
    return zip_path


def extract(zip_path: Path, dest: Path) -> Path:
    """Распаковать и вернуть папку, где лежит CopyVideoFinder.exe."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                target = (dest / member).resolve()
                if root != target and root not in target.parents:
                    raise UpdateError("В архиве обновления недопустимые пути.")
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise UpdateError("Архив обновления повреждён.") from exc
    found = next(dest.rglob(EXE_NAME), None)
    if not found:
        raise UpdateError(f"В архиве обновления нет {EXE_NAME}.")
    if (found.parent / paths.DATA_DIR_NAME).exists():
        raise UpdateError("В архиве обновления есть папка «Данные» — такое обновление не ставится.")
    return found.parent


def _cmd_escape(path: Path) -> str:
    return str(path).replace("%", "%%")


def write_script(new_dir: Path, app_dir: Path, pid: int, log_path: Path) -> Path:
    """Командный файл, который заменит файлы после выхода программы и запустит её снова."""
    new, app, log_file = _cmd_escape(new_dir), _cmd_escape(app_dir), _cmd_escape(log_path)
    script = f"""@echo off
chcp 65001 >nul
set "PID={pid}"
set "NEW={new}"
set "APP={app}"
set "LOG={log_file}"
echo === Обновление %date% %time% >> "%LOG%"
:wait
tasklist /FI "PID eq %PID%" /NH 2>nul | find " %PID% " >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
if not exist "%NEW%\\{EXE_NAME}" (
  echo Нет новой версии в %NEW% >> "%LOG%"
  goto start
)
robocopy "%NEW%" "%APP%" /E /R:10 /W:1 /NP /NFL /NDL >> "%LOG%"
if errorlevel 8 (
  echo Ошибка копирования, код %errorlevel% >> "%LOG%"
) else (
  echo Готово >> "%LOG%"
)
:start
start "" "%APP%\\{EXE_NAME}"
"""
    path = update_dir() / "update.cmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script.replace("\n", "\r\n"), encoding="utf-8")
    return path


def launch(script: Path) -> None:
    """Запустить командный файл отдельно от программы и без окна консоли."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True,
                     cwd=str(script.parent.parent))


def prepare_and_launch(zip_path: Path) -> None:
    """Распаковать обновление и запустить замену файлов; после вызова программа должна закрыться."""
    if not can_self_update():
        raise UpdateError("Обновление на месте работает только в собранной программе для Windows.")
    new_dir = extract(zip_path, update_dir() / "new")
    app_dir = paths.app_dir()
    if not (app_dir / EXE_NAME).exists():
        raise UpdateError(f"Не найден {EXE_NAME} в папке программы.")
    script = write_script(new_dir, app_dir, os.getpid(), paths.data_dir() / "update.log")
    launch(script)


def cleanup() -> None:
    """Удалить остатки прошлого обновления (при запуске новой версии)."""
    shutil.rmtree(update_dir(), ignore_errors=True)
