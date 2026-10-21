"""Проверка реального встроенного плеера: локальная страница и локальное видео, без видеосайтов."""

import functools
import json
import logging
import os
import sys
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PyQt6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.widgets.site_player import SitePlayer  # noqa: E402


def main(directory: Path) -> int:
    logging.getLogger(__name__).info("Проверка встроенного просмотра: вход")
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")

    def unhandled(kind, error, tb):
        frames = traceback.extract_tb(tb)
        where = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "без стека"
        message = f"Проверка встроенного плеера: {kind.__name__}, {where}"
        if sys.stdout is not None:
            print("::error::" + message, flush=True)
        sys.__excepthook__(kind, error, tb)

    sys.excepthook = unhandled
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
    reports = []
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
            reports[:] = [value]
            if value and value.get("time", 0) > 0.1 and value.get("frames", 0) > 0:
                private = panel.view.private_mode if sys.platform == "win32" else panel.view.profile.isOffTheRecord()
                if not private:
                    finish(False)
                    return
                if sys.stdout is not None:
                    print("Встроенный плеер воспроизводит локальное видео: " + video, flush=True)
                logging.getLogger(__name__).info("Проверка встроенного просмотра: кадры воспроизводятся")
                finish(True)
        except (TypeError, ValueError):
            pass

    script = """JSON.stringify((function(){let v=document.getElementById('clip');
        return v?{time:v.currentTime,width:v.videoWidth,frames:v.getVideoPlaybackQuality().totalVideoFrames,
        error:v.error&&v.error.code}:null;})())"""

    def poll():
        view = panel.view
        if panel.failure or getattr(view, "failure", ""):
            finish(False)
            return
        if view is not None:
            if sys.platform == "win32":
                view.evaluate(script)
            else:
                view.page().runJavaScript(script, report)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(300)
    logging.getLogger(__name__).info("Проверка встроенного просмотра: запуск локальной страницы")
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
    ok = bool(passed and passed[0])
    if not ok:
        diagnostic = {"status": panel.status.text(), "failure": panel.failure,
                      "native": getattr(panel.view, "failure", ""), "video": reports,
                      "private": getattr(panel.view, "private_mode", None)}
        # Только локальные тестовые данные: никаких URL площадок, паролей и ключей.
        message = json.dumps(diagnostic, ensure_ascii=False)
        logging.getLogger(__name__).error("Проверка встроенного плеера: %s", message)
        if sys.stdout is not None:
            print("::error::Проверка встроенного плеера: " + message, flush=True)
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as report_file:
                report_file.write("\nПроверка встроенного плеера: `" + message + "`\n")
    return 0 if ok else 1
