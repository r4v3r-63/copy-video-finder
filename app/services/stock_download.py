"""Отдельная загрузка: общий загрузчик файлов, без добавления в каталог услуг."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path, PureWindowsPath
from queue import Empty, Queue

import requests

from app import paths
from app.services.downloader import Cancelled, DownloadError, DownloadJob, file_stem, probe, run_job
from app.storage.download_files import metadata_file, migrate_folder, quarantine


def digest(file: Path, cancel) -> str:
    value = hashlib.sha256()
    with file.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            if cancel.is_set():
                raise Cancelled()
            value.update(block)
    return value.hexdigest()


def existing_download(job: DownloadJob) -> Path | None:
    migrate_folder(job.base_dir, (file_stem(None, job.result, unique=True), file_stem(None, job.result)))
    # Старое имя 0.26.0 тоже узнаём: обновление не заставляет качать тот же файл снова.
    for unique in (True, False):
        metadata = metadata_file(job.base_dir / (file_stem(None, job.result, unique=unique) + ".mp4"))
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
            name = data["filename"]
            if Path(name).name != name or PureWindowsPath(name).name != name or Path(name).suffix != ".mp4":
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
    info = probe(file, ffprobe) if ffprobe else None
    if ffprobe and info is None:
        quarantine(file, file.parent)
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
    paths.atomic_write_text(metadata_file(file), json.dumps(data, ensure_ascii=False, indent=2))
    return file, note


DEFAULT_PARALLEL_DOWNLOADS = 4
MAX_PARALLEL_DOWNLOADS = 8


def run_stock_queue(jobs, cancel, paused, progress, done, failed, ffmpeg=None, ffprobe=None,
                    parallel=DEFAULT_PARALLEL_DOWNLOADS):
    """Ограниченная очередь: отдельная HTTP-сессия на поток, завершение всех перед возвратом."""
    if type(parallel) is not int or not 1 <= parallel <= MAX_PARALLEL_DOWNLOADS:
        raise ValueError("Число одновременных загрузок должно быть от 1 до 8.")
    queue = Queue()
    for index, job in enumerate(jobs):
        queue.put((index, job))

    def consume():
        with requests.Session() as session:
            while True:
                try:
                    index, job = queue.get_nowait()
                except Empty:
                    return
                while paused.is_set() and not cancel.wait(0.1):
                    pass
                if cancel.is_set():
                    failed(index, "Скачивание отменено.")
                    continue
                try:
                    report = lambda received, total, i=index: progress(i, received, total)  # noqa: E731
                    file, note = run_stock_job(job, session, report, ffmpeg, ffprobe)
                    done(index, str(file), note)
                except Cancelled:
                    failed(index, "Скачивание отменено.")
                except (DownloadError, OSError) as exc:
                    message = (str(exc) if isinstance(exc, DownloadError)
                               else "Нет места или нет прав на запись в папку.")
                    failed(index, message)
                except Exception:  # noqa: BLE001 — ошибка одного файла не останавливает очередь
                    failed(index, "Не удалось скачать ролик. Проверьте доступность и повторите.")

    if jobs:
        with ThreadPoolExecutor(max_workers=min(parallel, len(jobs))) as pool:
            futures = [pool.submit(consume) for _ in range(min(parallel, len(jobs)))]
            for future in futures:
                future.result()
