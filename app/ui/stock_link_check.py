"""Сквозная проверка новой вкладки и исходных байтов в исходниках и Windows EXE."""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")

from PyQt6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.core import processes  # noqa: E402
from app.sources import pixabay  # noqa: E402
from app.storage.settings import Settings  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main(directory: Path) -> int:
    media = (directory / "site_player" / "landscape.mp4").read_bytes()
    raw = json.loads((directory / "stock_link" / "pixabay.json").read_text(encoding="utf-8"))
    raw["total"] = raw["totalHits"] = len(raw["hits"])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            data = json.dumps(raw).encode() if self.path.startswith("/api?") else media
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if self.path.startswith("/api?") else "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint = f"http://127.0.0.1:{server.server_port}"
    for item in raw["hits"]:
        for file in item["videos"].values():
            if file.get("url"):
                file.update(url=endpoint + "/video.mp4", size=len(media))
            file.pop("thumbnail", None)
    original_api = pixabay.API
    pixabay.API = endpoint + "/api"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(["Проверка загрузки по ссылке"])
    window = MainWindow(Settings(check_updates=False, link_check_days=0), processes.load_catalog())
    tab = window.stock_link
    source = pixabay.PixabaySource("fixture", window.cache)
    tab.ctx.all_sources = lambda: [source]
    passed = []
    stage = 0
    closing = False

    def finish(ok):
        nonlocal closing
        if closing:
            return
        closing = True
        passed.append(ok)
        timer.stop()
        window.close()
        app.quit()

    def poll():
        nonlocal stage
        try:
            if stage == 0 and tab.listing_worker and not tab.listing_worker.isRunning() and tab.items:
                tab.minimum.setText("10")
                tab.maximum.setText("30")
                assert {item.source_id for item in tab.model.items} == {"201", "202"}
                assert tab.download_all.isEnabled()
                tab.quantity_count.setValue(1)
                tab.model.setData(tab.model.index(0, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
                tab.start_download(True)
                stage = 1
            elif stage == 1 and tab.completed:
                assert not tab.errors and not window.catalog.entries()
                outputs = list(Path(tab.folder.text()).glob("*.mp4"))
                assert len(outputs) == 1 and outputs[0].read_bytes() == media
                metadata = json.loads(outputs[0].with_suffix(".json").read_text(encoding="utf-8"))
                assert not metadata["license_confirmed"] and metadata["id"] == "201"
                assert not list(Path(tab.folder.text()).glob("*.part"))
                finish(True)
        except (AssertionError, OSError, ValueError, KeyError):
            finish(False)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    with tempfile.TemporaryDirectory(prefix="copy-stock-check-") as temporary:
        tab.folder.setText(temporary)
        tab.url.setText("https://pixabay.com/videos/search/?orientation=horizontal&time_lapse=true")
        tab.search()
        QTimer.singleShot(45000, lambda: finish(False))
        try:
            app.exec()
        finally:
            window.close()
            window.catalog.close()
            window.cache.close()
            server.shutdown()
            server.server_close()
            thread.join()
            pixabay.API = original_api
    return 0 if passed and passed[0] else 1
