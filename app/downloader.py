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
import tempfile
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
from app.sources.base import SourceError, VideoFile, VideoResult
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
    catalog_id: int | None = None
    id: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    loop_hint: bool | None = None  # подсказка «подходит для зацикливания» после скачивания
    trim: int = 0  # подготовка к показу: оставить первые N секунд (0 — не обрезать)
    mute: bool = False  # подготовка к показу: убрать звук
    prepared: bool = False  # обрезка и снятие звука выполнены
    # Для файла со страницы сайта (VideoFile.page): функция источника (файл, сессия) → ссылка на файл.
    resolver: object = None


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


def file_stem(process: Process | None, result: VideoResult, catalog_id: int | None = None) -> str:
    """Имя файла: IDвкаталоге_техпроцесс_площадка_IDролика."""
    source = re.sub(r"[^a-z0-9]+", "", result.source.lower())
    prefix = f"{catalog_id}_" if catalog_id else ""
    return safe_name(f"{prefix}{process.id if process else 'zapros'}_{source}_{result.source_id}", 120)


def license_text(job: DownloadJob, video_path: Path, downloaded_at: datetime, probe: dict | None) -> str:
    r, lic = job.result, job.result.license
    lines = [
        f"Файл: {video_path.name}",
        f"Номер в каталоге: {job.catalog_id}" if job.catalog_id else "Номер в каталоге: —",
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
    if job.prepared:
        changes = ([f"обрезан до {job.trim} с"] if job.trim > 0 else []) + (["звук убран"] if job.mute else [])
        lines.append("Подготовлен к показу: " + ", ".join(changes) + ". Это изменение ролика — для лицензий с ND "
                     "оно недопустимо (такие ролики программа не скачивает).")
    if r.has_audio and not (job.prepared and job.mute):
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
    """Скачать в dest.part с докачкой (Range) и повторами, затем переименовать в dest.

    Докачка — только того же файла: с If-Range (ETag или дата изменения от первого ответа);
    если файл на сервере изменился, сервер отдаёт его целиком (200) и скачивание начинается заново.
    """
    part = dest.with_name(dest.name + ".part")
    validator_file = dest.with_name(dest.name + ".part.etag")
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        validator = validator_file.read_text(encoding="utf-8").strip() if have and validator_file.exists() else ""
        if have and not validator:
            # Без ETag/даты изменения не проверить, тот ли это файл: докачка могла бы склеить две версии.
            part.unlink()
            have = 0
        if have:
            headers["Range"] = f"bytes={have}-"
            headers["If-Range"] = validator
        try:
            with session.get(url, headers=headers, stream=True, timeout=30) as resp:
                if resp.status_code == 416 and have:
                    # «Диапазон за концом файла» при If-Range (файл тот же): готово, если совпал и размер.
                    if _range_total(resp.headers) == have:
                        break
                    part.unlink(missing_ok=True)
                    validator_file.unlink(missing_ok=True)
                    if attempt == retries:
                        raise DownloadError("Файл на площадке изменился — скачайте ещё раз.")
                    continue
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
                    have = 0  # сервер не умеет докачку или файл изменился — начинаем заново
                    new_validator = resp.headers.get("ETag") or resp.headers.get("Last-Modified") or ""
                    if new_validator:
                        validator_file.write_text(new_validator, encoding="utf-8")
                    else:
                        validator_file.unlink(missing_ok=True)
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
    validator_file.unlink(missing_ok=True)


def _range_total(headers) -> int | None:
    """Полный размер файла из «Content-Range: bytes */1234» (или «bytes 0-9/1234»)."""
    m = re.search(r"/(\d+)\s*$", headers.get("Content-Range") or "")
    return int(m.group(1)) if m else None


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


def _frame(path: Path, ffmpeg: Path, last: bool) -> bytes | None:
    """Первый или последний кадр ролика в PNG (через FFmpeg, без временных файлов)."""
    seek = ["-sseof", "-0.3"] if last else []
    try:
        out = subprocess.run([str(ffmpeg), "-v", "error", *seek, "-i", str(path), "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, timeout=60, creationflags=_NO_WINDOW)
        return out.stdout or None
    except (OSError, subprocess.SubprocessError):
        return None


def loop_hint(path: Path, ffmpeg: Path) -> bool | None:
    """Похожи ли первый и последний кадр: ролик можно зациклить без заметного скачка.

    None — сравнить не удалось. Это подсказка, окончательно решает пользователь.
    """
    from app import similar
    first, last = _frame(path, ffmpeg, last=False), _frame(path, ffmpeg, last=True)
    if not first or not last:
        return None
    try:
        return similar.distance(similar.dhash(first), similar.dhash(last)) <= similar.LOOP_DISTANCE
    except (OSError, ValueError):
        return None


def convert_to_mp4(src: Path, dest: Path, ffmpeg: Path, cancel: threading.Event | None = None,
                   trim: int = 0, mute: bool = False) -> None:
    """Перекодировать в MP4 (H.264); trim — оставить первые N секунд, mute — без звука."""
    tmp = dest.with_name(dest.stem + ".converting.mp4")
    audio = ["-an"] if mute else ["-c:a", "aac", "-b:a", "128k"]
    limit = ["-t", str(trim)] if trim > 0 else []
    cmd = [str(ffmpeg), "-y", "-v", "error", "-i", str(src), *limit, "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-pix_fmt", "yuv420p", *audio, "-movflags", "+faststart", str(tmp)]
    # Ошибки FFmpeg — во временный файл: из непрочитанного канала (PIPE) при длинном выводе
    # FFmpeg встал бы, а цикл ниже ждал бы его вечно.
    with tempfile.TemporaryFile() as log_file:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_file, creationflags=_NO_WINDOW)
        while proc.poll() is None:
            if cancel and cancel.is_set():
                proc.kill()
                proc.wait()
                tmp.unlink(missing_ok=True)
                raise Cancelled()
            time.sleep(0.2)
        log_file.seek(0)
        err = log_file.read().decode("utf-8", "replace")
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"Не удалось конвертировать в MP4: {err.strip()[-300:]}")
    tmp.replace(dest)


def run_job(job: DownloadJob, session, progress=None, ffmpeg: Path | None = None,
            ffprobe: Path | None = None, now=datetime.now) -> tuple[Path, str]:
    """Скачать ролик; вернуть путь к итоговому файлу и примечание для пользователя."""
    folder = target_folder(job.base_dir, job.process, job.query_text)
    folder.mkdir(parents=True, exist_ok=True)
    stem = file_stem(job.process, job.result, job.catalog_id)
    file = job.file
    if file.page:
        if not job.resolver:
            raise DownloadError("Ссылку на файл даёт только страница ролика — скачайте его в браузере.")
        try:
            url = job.resolver(file, session)
        except SourceError as exc:
            raise DownloadError(exc.message) from exc
        except requests.RequestException as exc:
            raise DownloadError(f"Не удалось открыть страницу ролика: {exc}") from exc
        file = VideoFile(file.quality, url, file.width, file.height, file.mime)
    ext = extension(file)
    prepare = job.trim > 0 or job.mute  # подготовка к показу: обрезать, убрать звук
    want_mp4 = (job.convert and ext != ".mp4") or prepare
    final = folder / (stem + (".mp4" if want_mp4 and ffmpeg else ext))
    license_file = folder / (stem + ".txt")
    wanted = variant(file, job.trim if ffmpeg else 0, job.mute and bool(ffmpeg))
    if final.exists():
        saved = saved_variant(license_file)
        if saved == wanted or (saved is None and _legacy_same(license_file, file, wanted)):
            return final, "уже был скачан"
        note_replaced = "заменён: прежний файл был в другом качестве или с другой подготовкой к показу"
    else:
        note_replaced = ""

    # Исходник — под отдельным именем, если его будут перекодировать в файл с тем же расширением.
    raw = folder / (stem + (".download" if want_mp4 and ffmpeg else "") + ext)
    fetch(file.url, raw, session, progress, job.cancel)
    note = note_replaced
    if want_mp4:
        if ffmpeg:
            if progress:
                progress(-1, -1)  # сигнал «конвертация»
            convert_to_mp4(raw, final, ffmpeg, job.cancel, trim=job.trim, mute=job.mute)
            raw.unlink(missing_ok=True)
            job.prepared = prepare
        else:
            note = (note + "; " if note else "") + \
                "FFmpeg не найден — файл сохранён как есть, без конвертации и подготовки к показу"
    info = probe(final, ffprobe)
    if ffprobe and info is None:
        note = (note + "; " if note else "") + "файл не удалось проверить — возможно, он повреждён"
    elif ffmpeg:
        job.loop_hint = loop_hint(final, ffmpeg)
    license_file.write_text(license_text(job, final, now(), info) + f"{VARIANT_PREFIX}{wanted}\n",
                            encoding="utf-8")
    return final, note


VARIANT_PREFIX = "Вариант файла: "


def variant(file: VideoFile, trim: int, mute: bool) -> str:
    """Чем этот файл отличается от других скачиваний того же ролика: качество и подготовка к показу."""
    size = f"{file.width}×{file.height}" if file.width and file.height else "?"
    return f"{file.quality} {size}, обрезка {trim} с, без звука: {'да' if mute else 'нет'}"


def _legacy_same(license_file: Path, file: VideoFile, wanted: str) -> bool:
    """Файл версий до 0.18 (без строки варианта) — тот же, только если оба без подготовки к показу
    и качество совпало. Параметры старой подготовки надёжно не восстановить — тогда скачиваем заново."""
    if not wanted.endswith("обрезка 0 с, без звука: нет"):
        return False
    try:
        text = license_file.read_text(encoding="utf-8")
    except OSError:
        return False
    return f"Качество: {file.quality}" in text and "Подготовлен к показу" not in text


def saved_variant(license_file: Path) -> str | None:
    """Вариант уже скачанного файла — из последней строки его файла лицензии (None — не записан)."""
    try:
        lines = license_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found = [line[len(VARIANT_PREFIX):] for line in lines if line.startswith(VARIANT_PREFIX)]
    return found[-1] if found else None


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
        self.wait()  # отмена прерывает и скачивание, и FFmpeg

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
