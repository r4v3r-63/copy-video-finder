"""Скачивание роликов: папка услуги, файл лицензии рядом, докачка, конвертация в MP4.

Скачивается только по ссылке из ответа API и только по действию пользователя —
массового автоматического скачивания нет (требование Pixabay).
"""

import json
import logging
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from app import licenses, paths
from app.processes import Process
from app.sources.base import VideoFile, VideoResult
from app.sources.http import USER_AGENT

log = logging.getLogger(__name__)

CHUNK = 256 * 1024
RETRIES = 3
MAX_WAIT = 30  # секунд: дольше по Retry-After не ждём, а сообщаем пользователю
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EXT_BY_MIME = {"video/mp4": ".mp4", "video/webm": ".webm", "video/ogg": ".ogv", "application/ogg": ".ogv",
                "video/quicktime": ".mov"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # не показывать консоль FFmpeg в Windows


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class DownloadJob:
    result: VideoResult
    file: VideoFile
    process: Process | None
    query_text: str
    base_dir: Path
    convert: bool = True
    id: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)


def short_side(f: VideoFile) -> int:
    if f.width and f.height:
        return min(f.width, f.height)
    return f.height or 0


def choose_file(result: VideoResult, allow_4k: bool = False) -> VideoFile | None:
    """Лучший вариант не выше Full HD (или 4K, если разрешено); MP4 предпочтительнее при равном качестве."""
    limit = 2160 if allow_4k else 1080
    files = [f for f in result.files if short_side(f) <= limit] or sorted(result.files, key=short_side)[:1]
    if not files:
        return None
    return max(files, key=lambda f: (short_side(f), extension(f) == ".mp4"))


def safe_name(text: str, limit: int = 80) -> str:
    text = _FORBIDDEN.sub(" ", text)
    text = " ".join(text.split()).strip(" .")
    return text[:limit].rstrip(" .") or "без названия"


def target_folder(base: Path, process: Process | None, query_text: str) -> Path:
    if process:
        return base / safe_name(process.category) / safe_name(process.name)
    return base / "Без услуги" / safe_name(query_text)


def extension(f: VideoFile) -> str:
    suffix = Path(urlparse(f.url).path).suffix.lower()
    if suffix in (".mp4", ".webm", ".ogv", ".ogg", ".mov", ".m4v"):
        return ".ogv" if suffix == ".ogg" else suffix
    return _EXT_BY_MIME.get((f.mime or "").split(";")[0].strip(), ".mp4")


def file_stem(process: Process | None, result: VideoResult) -> str:
    """Имя файла: техпроцесс_площадка_IDролика (ID в каталоге добавится на этапе 5)."""
    source = re.sub(r"[^a-z0-9]+", "", result.source.lower())
    return safe_name(f"{process.id if process else 'zapros'}_{source}_{result.source_id}", 120)


def license_text(job: DownloadJob, video_path: Path, downloaded_at: datetime, probe: dict | None) -> str:
    r, lic = job.result, job.result.license
    lines = [
        f"Файл: {video_path.name}",
        f"Название: {r.title or '—'}",
        f"Услуга: {job.process.name + ' (' + job.process.category + ')' if job.process else job.query_text}",
        f"Площадка: {r.source}, ID ролика: {r.source_id}",
        f"Страница ролика: {r.page_url}",
        f"Автор: {r.author or '—'}" + (f" ({r.author_url})" if r.author_url else ""),
        f"Лицензия: {lic.name if lic else 'не указана'}",
        f"Условия лицензии: {lic.url if lic and lic.url else '—'}",
        f"Статус: {lic.status if lic else licenses.REVIEW}",
    ]
    if lic and lic.note:
        lines.append(f"Примечание: {lic.note}")
    if lic and lic.status == licenses.ATTRIBUTION:
        lines.append("Атрибуция (показывать в титре): "
                     + licenses.attribution_text(r.author, r.title, lic.name, r.page_url))
    if r.has_audio:
        lines.append("Есть звуковая дорожка — проверить права на музыку.")
    lines += [
        f"Качество: {job.file.quality}" + (f" ({job.file.width}×{job.file.height})" if job.file.width else ""),
        f"Скачано по ссылке из API: {job.file.url}",
        f"Дата скачивания и проверки лицензии: {downloaded_at:%Y-%m-%d %H:%M}",
    ]
    if probe:
        lines.append(f"Проверка файла: {probe['width']}×{probe['height']}, {probe['duration']:.1f} с")
    lines.append("")
    lines.append("Окончательное подтверждение лицензии — за пользователем.")
    return "\n".join(lines) + "\n"


def fetch(url: str, dest: Path, session, progress=None, cancel: threading.Event | None = None,
          retries: int = RETRIES, pause: float = 2.0) -> None:
    """Скачать в dest.part с докачкой (Range) и повторами, затем переименовать в dest."""
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with session.get(url, headers=headers, stream=True, timeout=30) as resp:
                if resp.status_code == 416 and have:  # уже скачано полностью
                    break
                if resp.status_code == 404:
                    raise DownloadError("Файл удалён с площадки.")
                if resp.status_code == 429:
                    wait = _retry_after(resp.headers)
                    if wait <= MAX_WAIT and attempt < retries:
                        time.sleep(wait)
                        continue
                    raise DownloadError("Площадка временно ограничила скачивание. "
                                        f"Повторите через {max(round(wait / 60), 1)} мин.")
                if resp.status_code not in (200, 206):
                    raise DownloadError(f"Площадка вернула ошибку {resp.status_code}.")
                if resp.status_code == 200:
                    have = 0  # сервер не умеет докачку — начинаем заново
                total = have + int(resp.headers.get("Content-Length") or 0)
                with open(part, "ab" if have else "wb") as out:
                    for chunk in resp.iter_content(CHUNK):
                        if cancel and cancel.is_set():
                            raise Cancelled()
                        out.write(chunk)
                        have += len(chunk)
                        if progress:
                            progress(have, total)
                if total and have < total:
                    raise requests.ConnectionError("соединение оборвалось")
                break
        except requests.RequestException as exc:
            log.warning("Скачивание %s, попытка %d: %s", url, attempt, exc)
            if attempt == retries:
                raise DownloadError(f"Нет связи: {exc}") from exc
            time.sleep(pause * attempt)
    if not part.exists() or part.stat().st_size == 0:
        raise DownloadError("Скачан пустой файл.")
    part.replace(dest)


def _retry_after(headers) -> float:
    try:
        return max(float(headers.get("Retry-After", 60)), 0)
    except ValueError:
        return 60.0


def probe(path: Path, ffprobe: Path | None) -> dict | None:
    """Длительность и разрешение файла через ffprobe; None, если проверить нечем."""
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW,
        )
        data = json.loads(out.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {"width": int(stream["width"]), "height": int(stream["height"]),
                "duration": float(data["format"]["duration"])}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        log.warning("ffprobe не смог прочитать %s: %s", path, exc)
        return None


def convert_to_mp4(src: Path, dest: Path, ffmpeg: Path, cancel: threading.Event | None = None) -> None:
    tmp = dest.with_name(dest.stem + ".converting.mp4")
    cmd = [str(ffmpeg), "-y", "-v", "error", "-i", str(src), "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WINDOW)
    while proc.poll() is None:
        if cancel and cancel.is_set():
            proc.kill()
            proc.wait()
            tmp.unlink(missing_ok=True)
            raise Cancelled()
        time.sleep(0.2)
    err = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"Не удалось конвертировать в MP4: {err.strip()[-300:]}")
    tmp.replace(dest)


def run_job(job: DownloadJob, session, progress=None, ffmpeg: Path | None = None,
            ffprobe: Path | None = None, now=datetime.now) -> tuple[Path, str]:
    """Скачать ролик; вернуть путь к итоговому файлу и примечание для пользователя."""
    folder = target_folder(job.base_dir, job.process, job.query_text)
    folder.mkdir(parents=True, exist_ok=True)
    stem = file_stem(job.process, job.result)
    ext = extension(job.file)
    want_mp4 = job.convert and ext != ".mp4"
    final = folder / (stem + (".mp4" if want_mp4 and ffmpeg else ext))
    if final.exists():
        return final, "уже был скачан"

    raw = folder / (stem + ext)
    fetch(job.file.url, raw, session, progress, job.cancel)
    note = ""
    if want_mp4:
        if ffmpeg:
            if progress:
                progress(-1, -1)  # сигнал «конвертация»
            convert_to_mp4(raw, final, ffmpeg, job.cancel)
            raw.unlink(missing_ok=True)
        else:
            note = "FFmpeg не найден — файл сохранён без конвертации"
    info = probe(final, ffprobe)
    if ffprobe and info is None:
        note = (note + "; " if note else "") + "файл не удалось проверить — возможно, он повреждён"
    (folder / (stem + ".txt")).write_text(license_text(job, final, now(), info), encoding="utf-8")
    return final, note


class DownloadQueue(QThread):
    """Очередь скачивания: по одному файлу, не блокируя интерфейс."""

    progress = pyqtSignal(int, int, int)  # id задания, скачано байт, всего (-1, -1 — конвертация)
    done = pyqtSignal(int, str, str)  # id, путь к файлу, примечание
    failed = pyqtSignal(int, str)  # id, сообщение

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: queue.Queue[DownloadJob | None] = queue.Queue()
        self._next_id = 1
        self.jobs: dict[int, DownloadJob] = {}
        self.session = requests.Session()

    def add(self, job: DownloadJob) -> int:
        job.id = self._next_id
        self._next_id += 1
        self.jobs[job.id] = job
        self._jobs.put(job)
        if not self.isRunning():
            self.start()
        return job.id

    def stop(self):
        for job in self.jobs.values():
            job.cancel.set()
        self._jobs.put(None)
        self.wait(10000)

    def run(self):
        ffmpeg, ffprobe = paths.ffmpeg_tool("ffmpeg"), paths.ffmpeg_tool("ffprobe")
        while True:
            job = self._jobs.get()
            if job is None:
                return
            if job.cancel.is_set():
                continue
            try:
                path, note = run_job(job, self.session, lambda d, t, j=job.id: self.progress.emit(j, d, t),
                                     ffmpeg, ffprobe)
                self.done.emit(job.id, str(path), note)
            except Cancelled:
                self.failed.emit(job.id, "Скачивание отменено.")
            except DownloadError as exc:
                self.failed.emit(job.id, str(exc))
            except OSError as exc:
                log.exception("Ошибка записи файла")
                self.failed.emit(job.id, f"Не удалось записать файл: {exc}")
            except Exception:  # noqa: BLE001
                log.exception("Ошибка скачивания")
                self.failed.emit(job.id, "Непредвиденная ошибка скачивания, подробности в журнале.")
