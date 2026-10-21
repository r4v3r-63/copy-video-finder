"""Скачивание доступного ролика по странице: отдельный отменяемый процесс yt-dlp."""

import http.cookiejar
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from app import paths
from app.sources.web import skipped


class SiteDownloadError(Exception):
    pass


class SiteDownloadCancelled(Exception):
    pass


def available_page(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        _port = parsed.port
        return bool(parsed.scheme in ("http", "https") and parsed.hostname
                    and parsed.username is None and parsed.password is None and not skipped(url)
                    and not any(c.isspace() or ord(c) < 32 or c == "\\" for c in url))
    except ValueError:
        return False


def error_code(message: str) -> str:
    message = message.lower()
    if "unsupported url" in message:
        return "unsupported"
    if any(word in message for word in ("429", "too many requests", "rate limit", "captcha", "bot")):
        return "limit"
    if any(word in message for word in ("drm", "protected")):
        return "protected"
    if any(word in message for word in ("401", "sign in", "login", "log in", "cookies", "authentication", "private")):
        return "login"
    if any(word in message for word in ("403", "geo", "unavailable", "not available", "removed", "404")):
        return "unavailable"
    return "failed"


ERRORS = {
    "limit": "Площадка временно ограничила скачивание. Повторите позже; лимит не обходится.",
    "protected": "Ролик защищён DRM. Программа не снимает защиту.",
    "login": "Площадка требует вход для скачивания. Войдите на её страницу в панели программы и повторите.",
    "unavailable": "Площадка не разрешила скачать этот ролик или он недоступен в вашей сети.",
    "unsupported": "Загрузчик пока не поддерживает скачивание с этой страницы.",
    "playlist": "Нужна страница отдельного ролика, а не плейлист или трансляция.",
    "failed": "Не удалось скачать ролик со страницы. Проверьте его доступность в панели просмотра.",
}


def stop_process(process):
    """Завершить загрузчик вместе с его FFmpeg/Deno, а не оставить дочерние процессы."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.wait()


def download_page(url: str, directory: Path, max_height: int, ffmpeg: Path | None,
                  cancel, progress=None, cookies: list | None = None) -> dict:
    if not available_page(url):
        raise SiteDownloadError("Эта страница не поддерживается для скачивания.")
    if cancel.is_set():
        raise SiteDownloadCancelled()
    request = directory / "request.json"
    deno = paths.app_dir() / "deno" / ("deno.exe" if sys.platform == "win32" else "deno")
    paths.atomic_write_text(request, json.dumps({"url": url, "height": max_height,
        "ffmpeg": str(ffmpeg) if ffmpeg else None, "deno": str(deno) if deno.is_file() else None,
        "cookies": cookies or []}, ensure_ascii=False))
    command = ([sys.executable] if paths.is_frozen() else [sys.executable, "-m", "app.main"])
    command += ["--download-site", str(request)]
    options = {"creationflags": 0x08000000 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
    if progress:
        progress(-2, -2)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    status_path = directory / "status.json"
    previous = None
    try:
        while process.poll() is None:
            if cancel.is_set():
                stop_process(process)
                raise SiteDownloadCancelled()
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                current = status.get("done"), status.get("total")
                if current != previous and isinstance(current[0], int) and progress:
                    progress(*current)
                    previous = current
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        if cancel.is_set():
            raise SiteDownloadCancelled()
        try:
            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SiteDownloadError(ERRORS["failed"]) from exc
        if process.returncode != 0 or result.get("error"):
            raise SiteDownloadError(ERRORS.get(result.get("error"), ERRORS["failed"]))
        file = directory / result.get("filename", "")
        if file.parent.resolve() != directory.resolve() or not file.is_file() or file.is_symlink():
            raise SiteDownloadError(ERRORS["failed"])
        return {**result, "path": file}
    finally:
        stop_process(process)


def extractor_options(request: dict, directory: Path, progress) -> dict:
    height = request.get("height") if request.get("height") in (0, 720, 1080, 2160) else 1080
    format_choice = (f"bestvideo[height<=?{height}]+bestaudio/best[height<=?{height}]/bestvideo+bestaudio/best"
                     if height else "bestvideo+bestaudio/best")
    deno = request.get("deno")
    return {"outtmpl": str(directory / "video.%(ext)s"),
            "format": format_choice,
            "merge_output_format": "mp4", "format_sort": ["ext:mp4:m4a"],
            "noplaylist": True, "playlistend": 1, "socket_timeout": 15,
            "retries": 0, "fragment_retries": 0, "extractor_retries": 0, "file_access_retries": 0,
            "skip_unavailable_fragments": False, "concurrent_fragment_downloads": 1,
            "geo_bypass": False, "allow_unplayable_formats": False,
            "cachedir": False, "quiet": True, "noprogress": True, "no_warnings": True,
            "js_runtimes": {"deno": {"path": deno}} if deno else {}, "remote_components": [],
            "ffmpeg_location": request.get("ffmpeg"), "progress_hooks": [progress]}


def cookie_jar(items: list, url: str) -> http.cookiejar.CookieJar:
    """Только cookies домена выбранной страницы; чужие браузеры и настройки не читаются."""
    jar = http.cookiejar.CookieJar()
    host = urlsplit(url).hostname or ""
    for item in items:
        domain = str(item.get("domain", ""))
        name, value = str(item.get("name", "")), str(item.get("value", ""))
        if not domain or not (host == domain.lstrip(".") or host.endswith("." + domain.lstrip("."))):
            continue
        if not name or any(c in name + value for c in "\r\n\x00"):
            continue
        jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, domain,
            domain.startswith("."), domain.startswith("."), item.get("path") or "/", True,
            bool(item.get("secure")), None, True, None, None, {"HttpOnly": bool(item.get("http_only"))}, False))
    return jar


def run_extractor(request_path: Path) -> int:
    """Дочерний процесс: в JSON выдаём только результат и наши коды, без подписанных URL и cookies."""
    import yt_dlp
    from yt_dlp.globals import plugin_dirs

    plugin_dirs.value = []  # пользовательские плагины и удалённые компоненты не подключаем

    directory = request_path.parent
    output = directory / "result.json"
    captured = []

    class Logger:
        def debug(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            captured.append(error_code(message))

    def progress(value):
        paths.atomic_write_text(directory / "status.json", json.dumps({
            "done": int(value.get("downloaded_bytes") or 0),
            "total": int(value.get("total_bytes") or value.get("total_bytes_estimate") or 0)}))

    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not available_page(request["url"]):
            raise ValueError("unsupported")
        request_path.unlink(missing_ok=True)  # cookies нужны только в памяти дочернего процесса
        options = extractor_options(request, directory, progress)
        options["logger"] = Logger()
        with yt_dlp.YoutubeDL(options) as downloader:
            for cookie in cookie_jar(request.get("cookies", []), request["url"]):
                downloader.cookiejar.set_cookie(cookie)
            info = downloader.extract_info(request["url"], download=False)
            if not info or info.get("_type") in ("playlist", "multi_video") or info.get("is_live"):
                raise ValueError("playlist")
            if info.get("availability") in ("premium_only", "subscriber_only", "needs_premium"):
                raise ValueError("unavailable")
            if info.get("has_drm"):
                raise ValueError("protected")
            downloader.process_info(info)
            filename = Path(info.get("filepath") or downloader.prepare_filename(info))
            if (filename.parent.resolve() != directory.resolve() or not filename.is_file()
                    or filename.suffix.lower() not in (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".ogv")):
                raise ValueError("failed")
            codecs = [f.get("acodec") for f in info.get("requested_formats", [info])]
            audio = any(codec != "none" for codec in codecs if codec) if any(codecs) else None
            result = {"filename": filename.name, "ext": filename.suffix.lower(),
                      "width": info.get("width"), "height": info.get("height"),
                      "duration": info.get("duration"), "has_audio": audio}
        paths.atomic_write_text(output, json.dumps(result))
        return 0
    except Exception as exc:  # noqa: BLE001 — из процесса не выводим исходные ошибки с URL или cookies
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in ERRORS else (
            captured[-1] if captured else error_code(str(exc)))
        paths.atomic_write_text(output, json.dumps({"error": code}))
        return 1
