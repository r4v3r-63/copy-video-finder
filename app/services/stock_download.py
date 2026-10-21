"""Отдельная загрузка: общий загрузчик файлов, без добавления в каталог услуг."""

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app import paths
from app.services.downloader import Cancelled, DownloadError, DownloadJob, file_stem, probe, run_job


def digest(file: Path, cancel) -> str:
    value = hashlib.sha256()
    with file.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            if cancel.is_set():
                raise Cancelled()
            value.update(block)
    return value.hexdigest()


def existing_download(job: DownloadJob) -> Path | None:
    # Старое имя 0.26.0 тоже узнаём: обновление не заставляет качать тот же файл снова.
    for unique in (True, False):
        metadata = job.base_dir / (file_stem(None, job.result, unique=unique) + ".json")
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
            name = data["filename"]
            if Path(name).name != name:
                continue
            file = job.base_dir / name
            if (data["source"] == job.result.source and data["id"] == job.result.source_id
                    and file.is_file() and not file.is_symlink() and file.stat().st_size == data["size"]
                    and digest(file, job.cancel) == data["sha256"]):
                return file
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return None


def run_stock_job(job, session, progress=None, ffmpeg=None, ffprobe=None):
    if old := existing_download(job):
        return old, "уже скачан, размер и SHA-256 проверены"
    # Общий механизм сохраняет .part, обрабатывает Range/If-Range, отмену и Retry-After.
    requested = job.file.height if job.file.site else None
    file, note = run_job(job, session, progress, ffmpeg, ffprobe)
    if job.file.size and file.stat().st_size != job.file.size:
        file.replace(file.with_name(file.name + ".invalid"))
        raise DownloadError("Размер файла отличается от данных API. Неполный файл отложен; повторите скачивание.")
    info = probe(file, ffprobe) if ffprobe else None
    if ffprobe and info is None:
        file.replace(file.with_name(file.name + ".invalid"))
        raise DownloadError("Файл повреждён или не содержит читаемого видео. Повторите скачивание.")
    actual = min(job.file.width, job.file.height) if job.file.width and job.file.height else job.file.height
    if requested and actual and requested != actual:
        note += f"; качество {requested}p недоступно, получено {actual}p"
    data = {"source": job.result.source, "id": job.result.source_id, "original_url": job.result.page_url,
            "downloaded_at": datetime.now().astimezone().isoformat(),
            "duration": info["duration"] if info else job.result.duration,
            "width": info["width"] if info else job.file.width, "height": info["height"] if info else job.file.height,
            "size": file.stat().st_size, "path": str(file.resolve()), "filename": file.name,
            "sha256": digest(file, job.cancel), "license": asdict(job.result.license) if job.result.license else None,
            "license_confirmed": False}
    paths.atomic_write_text(file.with_suffix(".json"), json.dumps(data, ensure_ascii=False, indent=2))
    return file, note
