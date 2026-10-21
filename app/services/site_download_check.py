"""Реальная загрузка локального ролика через отдельный yt-dlp, без внешней сети."""

import functools
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import paths
from app.core.models import LicenseInfo, VideoResult
from app.services.downloader import DownloadJob, download_option, run_job


def main(directory: Path) -> int:
    # Эти модули выбираются динамически: проверяем их наличие также в портативном EXE.
    from yt_dlp.extractor import get_info_extractor
    from yt_dlp_ejs.yt import solver

    for name in ("Youtube", "VK", "Rutube", "Odnoklassniki", "ZenYandex", "MailRu", "Generic"):
        assert get_info_extractor(name)
    assert (Path(solver.__file__).parent / "core.min.js").is_file()
    page = (directory / "site_download" / "page.html").read_text(encoding="utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if "fixture_session=local-check" not in self.headers.get("Cookie", ""):
                self.send_error(401)
            elif self.path == "/video":
                data = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        Handler, directory=str(directory / "site_player")))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/video"
        result = VideoResult("Локальная проверка", "fixture", "Тестовый ролик", "", None, 2,
                             None, None, None, url, license=LicenseInfo("Неизвестна"))
        file = download_option(result)
        assert file is not None and file.site and not result.files
        with tempfile.TemporaryDirectory(prefix="copy-download-check-") as temporary:
            job = DownloadJob(result, file, None, "Локальная проверка", Path(temporary), convert=False,
                              cookies=[{"name": "fixture_session", "value": "local-check", "domain": "127.0.0.1"}])
            saved, _note = run_job(job, None)
            assert saved.read_bytes() == (directory / "site_player" / "landscape.mp4").read_bytes()
            sidecar = saved.with_suffix(".txt").read_text(encoding="utf-8")
            assert "Окончательное подтверждение лицензии — за пользователем" in sidecar
            assert "Скачано по исходной странице" in sidecar
            assert not job.cookies
        assert not list((paths.data_dir() / "download-jobs").iterdir())
        return 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
