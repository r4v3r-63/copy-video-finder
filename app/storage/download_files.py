"""Служебные данные скачиваний отдельно от папки готовых MP4."""

import hashlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path, PureWindowsPath

from app import paths

MEDIA = (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".ogv")
MIGRATION_LOCK = threading.Lock()


def _folder_key(folder: Path) -> str:
    name = os.path.normcase(paths.portable_path(folder.resolve()))
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def metadata_file(video: Path, suffix=".json") -> Path:
    folder = paths.data_dir() / "download-info" / _folder_key(video.parent)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (video.stem + suffix)


def working_file(video: Path) -> Path:
    folder = paths.data_dir() / "download-jobs" / "files" / _folder_key(video.parent)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / video.name


def quarantine(file: Path, output_folder: Path) -> Path:
    folder = paths.data_dir() / "download-jobs" / "errors" / _folder_key(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (file.name + "." + uuid.uuid4().hex + ".invalid")
    shutil.move(str(file), str(target))
    return target


def _move_old(file, destination):
    if destination.exists():
        # Не затираем актуальные сведения старой копией.
        if file.read_bytes() == destination.read_bytes():
            file.unlink()
            return
        destination = destination.with_name(destination.stem + ".legacy-" + uuid.uuid4().hex + destination.suffix)
    shutil.move(str(file), str(destination))


def migrate_folder(folder: Path, stems=()):
    """Перенести только распознанные файлы программы; пользовательские файлы не удалять."""
    with MIGRATION_LOCK:
        _migrate_folder(folder, stems)


def _migrate_folder(folder: Path, stems):
    if not folder.is_dir():
        return
    files = [file for file in folder.iterdir() if file.is_file() and not file.is_symlink()]
    known = set(stems)
    for file in files:
        if file.suffix not in (".txt", ".json"):
            continue
        try:
            text = file.read_text(encoding="utf-8")
            if file.suffix == ".json":
                data = json.loads(text)
                name = data.get("filename", "")
                owned = (isinstance(name, str) and name == Path(name).name == PureWindowsPath(name).name
                         and Path(name).stem == file.stem and Path(name).suffix in MEDIA
                         and all(key in data for key in ("source", "id", "sha256", "size", "original_url")))
            else:
                owned = (any(text.startswith(f"Файл: {file.stem}{ext}\n") for ext in MEDIA)
                         and "\nПлощадка: " in text
                         and "Окончательное подтверждение лицензии — за пользователем." in text)
            if owned:
                known.add(file.stem)
                _move_old(file, metadata_file(file, file.suffix))
        except (UnicodeError, ValueError, TypeError, AttributeError):
            continue
    for file in files:
        if not file.exists():
            continue
        for stem in known:
            remainder = file.name.removeprefix(stem)
            if remainder == file.name:
                continue
            if remainder.startswith(".download"):
                remainder = remainder.removeprefix(".download")
                if remainder in MEDIA or any(remainder.startswith(ext + ".part") for ext in MEDIA):
                    _move_old(file, working_file(folder / (stem + remainder)))
                    break
            if remainder == ".converting.mp4":
                quarantine(file, folder)
                break
            if any(remainder == ext + ".invalid" for ext in MEDIA):
                quarantine(file, folder)
                break
            if any(remainder in (ext + ".part", ext + ".part.json", ext + ".part.etag") for ext in MEDIA):
                _move_old(file, working_file(file))
                break
            if remainder in MEDIA and remainder != ".mp4":
                _move_old(file, working_file(file))
                break
