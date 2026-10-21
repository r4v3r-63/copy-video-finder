"""Автообновление: проверка последнего выпуска на GitHub и замена файлов программы.

Запущенный exe в Windows заменить нельзя, поэтому новая версия распаковывается
в «Данные/update», программа запускает PowerShell и закрывается после подтверждения; он ждёт
её выхода, копирует новые файлы поверх старых (robocopy) и запускает программу.
Папка «Данные» с настройками, ключами и роликами не трогается.
"""

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app import __version__, paths
from app.services.downloader import fetch

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


def result_file() -> Path:
    return paths.data_dir() / "update-result.json"


def read_result() -> dict:
    try:
        value = json.loads(result_file().read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def process_running(pid: int) -> bool:
    if sys.platform != "win32" or type(pid) is not int or pid < 1:
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def installation_in_progress() -> bool:
    result = read_result()
    return result.get("state") in ("waiting", "copying") and process_running(result.get("installer_pid"))


def previous_failure() -> str:
    result = read_result()
    if not result or result.get("acknowledged") or result.get("state") == "installed":
        return ""
    if installation_in_progress():
        return ""
    result["acknowledged"] = True
    paths.atomic_write_text(result_file(), json.dumps(result, ensure_ascii=False))
    return ("Предыдущее обновление не завершилось. " +
            str(result.get("message") or "Работа установщика была прервана.") +
            " Подробности: Данные/update.log. При необходимости распакуйте новый ZIP вручную, сохранив папку Данные.")


def write_script(new_dir: Path, app_dir: Path, pid: int, log_path: Path,
                 version: str = "", restart_args=(), show_errors=True) -> Path:
    """Подготовить установщик без cmd.exe, зависимости от кодовой страницы и подстановок пути."""
    root = update_dir()
    root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    script = root / f"install-{token}.ps1"
    config = {"new": str(new_dir.resolve()), "app": str(app_dir.resolve()), "pid": pid,
              "log": str(log_path.resolve()), "result": str(result_file().resolve()),
              "ready": str(script.with_suffix(".ready")), "version": version,
              "sha256": sha256_of(new_dir / EXE_NAME), "restart_args": list(restart_args),
              "show_errors": show_errors}
    encoded = base64.b64encode(json.dumps(config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    template = (paths.resources_dir() / "update.ps1").read_text(encoding="utf-8-sig")
    script.write_text(template.replace("__CONFIG_BASE64__", encoded), encoding="utf-8-sig")
    return script


@contextmanager
def system_dll_search():
    """Системный установщик не должен наследовать каталог DLL замороженной программы."""
    if sys.platform != "win32" or not paths.is_frozen():
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    previous = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(previous), previous)
    if not kernel.SetDllDirectoryW(None):
        raise UpdateError("Не удалось подготовить запуск системного установщика.")
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(previous.value or None)


def launch(script: Path) -> None:
    """Закрывать приложение можно лишь после подтверждения запуска отдельного установщика."""
    encoded = base64.b64encode(script.read_text(encoding="utf-8-sig").encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    bootstrap = script.parent / "installer-start.log"
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1",
                   "PSModulePath": str(powershell.parent / "Modules")}
    with bootstrap.open("ab") as output, system_dll_search():
        process = subprocess.Popen(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=output, stderr=output, close_fds=True,
            cwd=str(script.parent),
            env=environment,
        )
    deadline = time.monotonic() + 15
    ready = script.with_suffix(".ready")
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise UpdateError("Windows не запустила установщик. Программа остаётся открытой. "
                              "Подробности: Данные/update/installer-start.log.")
        try:
            if ready.is_file() and ready.read_text(encoding="utf-8-sig").strip() == str(process.pid):
                return
        except OSError:
            pass  # PowerShell ещё закрывает файл подтверждения.
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise UpdateError("Установщик не подтвердил запуск. Программа остаётся открытой; повторите позже.")


def prepare_and_launch(zip_path: Path) -> None:
    if not can_self_update():
        raise UpdateError("Обновление на месте работает только в собранной программе для Windows.")
    if installation_in_progress():
        raise UpdateError("Предыдущее обновление ещё устанавливается. Дождитесь завершения.")
    new_dir = extract(zip_path, update_dir() / "new")
    app_dir = paths.app_dir()
    if not (app_dir / EXE_NAME).exists():
        raise UpdateError(f"Не найден {EXE_NAME} в папке программы.")
    target = parse_version(zip_path.name)
    version = ".".join(map(str, target)) if target else ""
    script = write_script(new_dir, app_dir, os.getpid(), paths.data_dir() / "update.log", version)
    launch(script)


def cleanup() -> None:
    """Не удалять распакованную версию во время установки и после ошибки."""
    result = read_result()
    if result.get("state") != "installed" or process_running(result.get("installer_pid")):
        return
    shutil.rmtree(update_dir(), ignore_errors=True)
