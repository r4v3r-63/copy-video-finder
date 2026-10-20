"""Шоурил для экрана: скачанные ролики услуги или категории склеиваются в один MP4.

Каждый ролик обрезается до нескольких секунд, приводится к 1920×1080 (с полями, без искажения
пропорций), появляется и уходит через затемнение. Звука нет. Рядом с MP4 пишется список роликов
с авторами и лицензиями — для роликов «с атрибуцией» авторов нужно указать при показе.
"""

import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app import catalog as cat
from app import licenses
from app.downloader import probe

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # без чёрного окна консоли в Windows
CLIP_SECONDS = 8
MIN_CLIP = 1.5  # короче — ролик не берётся: затемнение съест его целиком
FADE = 0.5
WIDTH, HEIGHT, FPS = 1920, 1080, 30
_MATCH_ORDER = {cat.EXACT: 0, cat.PARTIAL: 1, cat.RELATED: 2, cat.PRODUCT: 3}


class ShowreelError(Exception):
    pass


class Cancelled(Exception):
    pass


def suitable(e: cat.Entry) -> bool:
    """В шоурил идёт скачанный, не отклонённый ролик со свободной лицензией, подтверждённой пользователем."""
    lic = e.result.license
    return (e.status != cat.REJECTED and e.license_confirmed and lic is not None
            and lic.status in licenses.SUITABLE and e.has_file)


def showreel_entries(store: cat.Catalog, process_ids: list[str]) -> list[cat.Entry]:
    """Ролики для шоурила в порядке услуг; внутри услуги точные — первыми. Каждый ролик — один раз."""
    order = {pid: i for i, pid in enumerate(process_ids)}
    picked: dict[int, tuple] = {}
    for e in store.entries():
        if not suitable(e):
            continue
        for pid, match in e.links.items():
            if pid in order:
                rank = (order[pid], _MATCH_ORDER.get(match, 4), e.id)
                if e.id not in picked or rank < picked[e.id][0]:
                    picked[e.id] = (rank, e)
    return [e for _, e in sorted(picked.values(), key=lambda x: x[0])]


def clip_lengths(entries: list[cat.Entry], seconds: float, ffprobe: Path | None) -> list[float]:
    """Сколько секунд взять от каждого ролика: не больше seconds и не больше самого ролика."""
    lengths = []
    for e in entries:
        info = probe(Path(e.downloaded_path), ffprobe)
        duration = info["duration"] if info else e.result.duration
        lengths.append(round(min(seconds, duration), 2) if duration else float(seconds))
    return lengths


def build_command(files: list[Path], lengths: list[float], dest: Path, ffmpeg: Path, fade: float = FADE) -> list[str]:
    """Команда FFmpeg: обрезка, 1920×1080 с полями, затемнение в начале и в конце, склейка, без звука."""
    inputs, chains = [], []
    for i, (file, length) in enumerate(zip(files, lengths, strict=True)):
        inputs += ["-t", f"{length:.2f}", "-i", str(file)]
        chain = (f"[{i}:v]trim=0:{length:.2f},setpts=PTS-STARTPTS,"
                 f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                 f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS},format=yuv420p")
        if fade > 0:
            chain += f",fade=t=in:st=0:d={fade},fade=t=out:st={max(length - fade, 0):.2f}:d={fade}"
        chains.append(chain + f"[v{i}]")
    joined = "".join(f"[v{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=1:a=0[out]"
    return [str(ffmpeg), "-y", "-v", "error", *inputs, "-filter_complex", ";".join([*chains, joined]),
            "-map", "[out]", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(dest)]


def make_showreel(entries: list[cat.Entry], dest: Path, ffmpeg: Path, ffprobe: Path | None,
                  seconds: float = CLIP_SECONDS, fade: float = FADE, progress=None,
                  cancel: threading.Event | None = None) -> list[cat.Entry]:
    """Собрать шоурил; вернуть ролики, которые в него вошли. progress(доля 0..1)."""
    if cancel and cancel.is_set():
        raise Cancelled()
    lengths = clip_lengths(entries, seconds, ffprobe)
    used = [(e, n) for e, n in zip(entries, lengths, strict=True) if n >= MIN_CLIP]
    if not used:
        raise ShowreelError("Нет роликов длиннее полутора секунд.")
    fade = min(fade, min(n for _, n in used) / 3)
    total = sum(n for _, n in used)
    dest = Path(dest)
    tmp = dest.with_name(dest.stem + ".making.mp4")
    cmd = build_command([Path(e.downloaded_path) for e, _ in used], [n for _, n in used], tmp, ffmpeg, fade)
    # У stdout отдельные дескрипторы записи и чтения: чтение файла не блокируется при молчании FFmpeg.
    with tempfile.TemporaryDirectory(prefix="copyvf-showreel-") as logs:
        progress_path = Path(logs) / "progress.txt"
        with progress_path.open("w") as output, progress_path.open("r") as reader, \
                tempfile.TemporaryFile() as log_file:
            proc = subprocess.Popen(cmd, stdout=output, stderr=log_file, creationflags=_NO_WINDOW)
            pending = ""
            try:
                while True:
                    if cancel and cancel.is_set():
                        raise Cancelled()
                    pending += reader.read()
                    lines = pending.split("\n")
                    pending = lines.pop()
                    for line in lines:
                        if line.startswith("out_time_us=") and progress:
                            try:
                                progress(min(int(line.split("=", 1)[1]) / 1e6 / total, 1.0))
                            except ValueError:
                                pass
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
            except Cancelled:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                tmp.unlink(missing_ok=True)
                raise
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            log_file.seek(0)
            err = log_file.read().decode("utf-8", "replace")
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise ShowreelError(f"FFmpeg не смог собрать шоурил: {err.strip()[-300:]}")
    if cancel and cancel.is_set():
        tmp.unlink(missing_ok=True)
        raise Cancelled()
    tmp.replace(dest)
    return [e for e, _ in used]


def credits_text(title: str, entries: list[cat.Entry], now=None) -> str:
    """Список роликов шоурила: автор, площадка, лицензия, страница."""
    date = (now or datetime.now()).strftime("%d.%m.%Y")
    lines = [f"Шоурил «{title}» — {date}", f"Роликов: {len(entries)}. Звук убран, каждый ролик обрезан.", ""]
    attribution = False
    for i, e in enumerate(entries, 1):
        r = e.result
        lic = r.license
        if lic and lic.status == licenses.ATTRIBUTION:
            attribution = True
        lines += [f"{i}. № {e.id} · {r.title}",
                  f"   Автор: {r.author or 'не указан'} · {r.source} · "
                  f"{lic.name if lic else ''} ({lic.status if lic else ''})",
                  f"   {r.page_url}", ""]
    if attribution:
        lines.append("Для роликов «с атрибуцией» при показе нужно указать авторов — например, табличкой "
                     "у экрана или кадром в конце показа.")
    if any(e.result.license and "SA" in e.result.license.name.upper().replace("-", " ").split()
           for e in entries):
        lines.append(licenses.SHARE_ALIKE_NOTE)
    lines.append("Ролики изменены (обрезаны, без звука): лицензии из списка это разрешают.")
    return "\n".join(lines) + "\n"


class ShowreelWorker(QThread):
    """Сборка шоурила в фоне: progress — проценты, done — текст ошибки (пустой — готово)."""
    progress = pyqtSignal(int)
    done = pyqtSignal(str, list)

    def __init__(self, title: str, entries: list[cat.Entry], dest: Path, ffmpeg: Path, ffprobe: Path | None,
                 seconds: float, fade: float, parent=None):
        super().__init__(parent)
        self.title, self.entries, self.dest = title, entries, Path(dest)
        self.ffmpeg, self.ffprobe, self.seconds, self.fade = ffmpeg, ffprobe, seconds, fade
        self.cancel = threading.Event()

    def run(self):
        try:
            used = make_showreel(self.entries, self.dest, self.ffmpeg, self.ffprobe, self.seconds, self.fade,
                                 lambda share: self.progress.emit(int(share * 100)), self.cancel)
            self.dest.with_suffix(".txt").write_text(credits_text(self.title, used), encoding="utf-8")
        except Cancelled:
            self.done.emit("Сборка отменена.", [])
        except (ShowreelError, OSError) as exc:
            self.done.emit(str(exc), [])
        else:
            self.done.emit("", used)
