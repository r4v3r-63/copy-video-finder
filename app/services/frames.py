"""Кадры из ролика: начало, середина и конец — чтобы оценить содержание, не смотря видео целиком.

FFmpeg читает ролик прямо по ссылке для просмотра (или из скачанного файла) и берёт по одному
кадру в нужных местах — весь файл при этом не скачивается.
"""

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # без чёрного окна консоли в Windows
FRAME_WIDTH = 320
TIMEOUT = 40  # секунд на кадр: по сети FFmpeg ищет нужное место не мгновенно


def frame_times(duration: float | None, count: int = 3) -> list[float]:
    """Где брать кадры: 10 %, 50 % и 90 % длительности; длительность неизвестна — 1, 5 и 10 с."""
    if not duration or duration <= 0:
        return [1.0, 5.0, 10.0][:count]
    if count == 1:
        return [duration / 2]
    return [round(duration * (0.1 + 0.8 * i / (count - 1)), 2) for i in range(count)]


def extract_frame(source: str, at: float, ffmpeg: Path, timeout: float = TIMEOUT,
                  cancel: threading.Event | None = None) -> bytes | None:
    """Один кадр в PNG; None — если взять не удалось (ролик короче, нет связи, битый файл) или отменено.

    FFmpeg по сети может думать долго, поэтому отмена (закрытие окна) сразу завершает его процесс.
    """
    cmd = [str(ffmpeg), "-v", "error", "-ss", f"{at:.2f}", "-i", source, "-frames:v", "1",
           "-vf", f"scale={FRAME_WIDTH}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if (cancel and cancel.is_set()) or time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    return None
                time.sleep(0.05)
            out.seek(0)
            data = out.read()
    except OSError:
        return None
    return data if proc.returncode == 0 and data else None


def extract_frames(source: str, duration: float | None, ffmpeg: Path, cancel: threading.Event | None = None,
                   count: int = 3) -> list[tuple[float, bytes]]:
    """Кадры (время, PNG) по порядку; пропавшие кадры пропускаются."""
    frames = []
    for at in frame_times(duration, count):
        if cancel and cancel.is_set():
            break
        image = extract_frame(source, at, ffmpeg, cancel=cancel)
        if image:
            frames.append((at, image))
    return frames
