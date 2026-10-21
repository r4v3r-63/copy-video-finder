"""Проверка реального встроенного плеера: локальная страница и локальное видео, без видеосайтов."""

import functools
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PyQt6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.widgets.site_player import SitePlayer  # noqa: E402


def main(directory: Path) -> int:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(["Проверка встроенного просмотра"])
    panel = SitePlayer()
    panel.resize(800, 500)
    panel.show()
    video = "landscape.mp4" if sys.platform == "win32" else "landscape.webm"

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/":
                data = (f'<html><body><video id="clip" autoplay muted loop controls '
                        f'src="/{video}"></video></body></html>').encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(directory)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    passed = []
    closing = False

    def finish(ok):
        nonlocal closing
        if closing:
            return
        closing = True
        passed.append(ok)
        timer.stop()
        panel.shutdown()
        panel.close()
        app.quit()

    def report(value):
        try:
            for _ in range(2):
                if isinstance(value, str):
                    value = json.loads(value)
            if value and value.get("time", 0) > 0.1 and value.get("frames", 0) > 0:
                private = panel.view.private_mode if sys.platform == "win32" else panel.view.profile.isOffTheRecord()
                if not private:
                    finish(False)
                    return
                if sys.stdout is not None:
                    print("Встроенный плеер воспроизводит локальное видео: " + video, flush=True)
                finish(True)
        except (TypeError, ValueError):
            pass

    script = """JSON.stringify((function(){let v=document.getElementById('clip');
        return v?{time:v.currentTime,width:v.videoWidth,frames:v.getVideoPlaybackQuality().totalVideoFrames,
        error:v.error&&v.error.code}:null;})())"""

    def poll():
        view = panel.view
        if view is not None:
            if sys.platform == "win32":
                view.evaluate(script)
            else:
                view.page().runJavaScript(script, report)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(300)
    panel.open(f"http://127.0.0.1:{server.server_port}/")
    if sys.platform == "win32" and panel.view is not None:
        panel.view.scriptFinished.connect(report)
    QTimer.singleShot(45000, lambda: finish(False))
    try:
        app.exec()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    return 0 if passed and passed[0] else 1
