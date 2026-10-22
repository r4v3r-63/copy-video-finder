"""Загрузчик страниц: ограничения, ошибки, приватность и реальное локальное скачивание."""

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from app.core import orientation
from app.diagnostics.site_download import main as check_download
from app.services import downloader
from app.services import site_downloader as site
from app.services.search import preview_file
from app.sources.web import parse_page
from tests.fakes import fixture_text

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def result():
    return replace(parse_page(fixture_text("manual_video.html"), "https://example.org/video/1"),
                   files=[], preview_video=None)


@pytest.mark.parametrize("url", ["file:///video.mp4", "javascript:alert(1)", "https://u:p@example.org/",
                                  "https://example.org:bad/a", "https://example.org/a\nsecret", "https://example.org/\\a"])
def test_invalid_page_is_not_downloadable(url):
    assert not site.available_page(url)
    assert downloader.download_option(replace(result(), page_url=url)) is None


def test_page_option_does_not_invent_direct_media_or_license():
    video = result()
    before = replace(video.license)
    file = downloader.download_option(video)
    assert file.site and file.url == video.page_url and file.height == 1080
    assert downloader.download_option(video, True).height == 2160
    assert video.files == [] and video.license == before
    assert preview_file(video) is None


def test_no_bypass_plugins_or_automatic_retries(tmp_path):
    options = site.extractor_options({"height": 1080}, tmp_path, lambda x: None)
    assert "1080" in options["format"]
    assert not options["geo_bypass"] and not options["allow_unplayable_formats"]
    assert options["remote_components"] == [] and options["js_runtimes"] == {}
    assert options["retries"] == options["fragment_retries"] == options["extractor_retries"] == 0
    assert options["noplaylist"] and not options["cachedir"]
    assert not options.get("cookiesfrombrowser") and not options.get("cookiefile")


def test_cookies_only_selected_domain_and_no_header_injection():
    items = [{"name": "session", "value": "synthetic", "domain": ".example.org", "http_only": True},
             {"name": "other", "value": "synthetic", "domain": "other.org"},
             {"name": "injected", "value": "bad\r\nheader", "domain": "example.org"}]
    jar = site.cookie_jar(items, "https://video.example.org/clip")
    assert [(c.name, c.value) for c in jar] == [("session", "synthetic")]
    assert next(iter(jar)).has_nonstandard_attr("HttpOnly")


@pytest.mark.parametrize(("message", "code"), [("HTTP Error 429", "limit"), ("DRM protected", "protected"),
    ("HTTP Error 401", "login"), ("Sign in required", "login"), ("HTTP Error 403", "unavailable"),
    ("Unsupported URL https://example.org/?session=private", "unsupported")])
def test_remote_errors_are_russian_and_do_not_leak_urls(message, code):
    assert site.error_code(message) == code
    assert "https://" not in site.ERRORS[code] and "private" not in site.ERRORS[code]


@pytest.mark.parametrize(("fixture", "error"), [("available", None), ("drm", "protected"),
    ("playlist", "playlist"), ("live", "playlist"), ("premium", "unavailable")])
def test_extractor_uses_fixture_and_rejects_protected_before_download(tmp_path, monkeypatch, fixture, error):
    import yt_dlp

    recorded = json.loads((FIXTURES / "site_download" / "metadata.json").read_text(encoding="utf-8"))[fixture]
    downloaded = []

    class Extractor:
        def __init__(self, options):
            self.cookiejar = site.cookie_jar([], "https://example.org/")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert not download
            return recorded

        def process_info(self, info):
            downloaded.append(info)
            (tmp_path / "video.mp4").write_bytes(b"fixture")

        def prepare_filename(self, info):
            return str(tmp_path / "video.mp4")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Extractor)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"url": "https://example.org/video/1"}), encoding="utf-8")
    status = site.run_extractor(request)
    report = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert status == (1 if error else 0)
    assert bool(downloaded) == (error is None)
    assert report.get("error") == error and not request.exists()
    assert "url" not in report and "cookies" not in report


def test_download_page_honours_cancel_before_start(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(site.SiteDownloadCancelled):
        site.download_page("https://example.org/video/1", tmp_path, 1080, None, cancel)
    assert not list(tmp_path.iterdir())


def test_page_job_cleans_temporary_data_and_keeps_orientation_guard(data_dir, tmp_path, monkeypatch):
    video = result()
    license_before = replace(video.license)
    job = downloader.DownloadJob(video, downloader.download_option(video), None, "Проверка", tmp_path,
                                 cookies=[{"name": "session", "value": "synthetic"}])

    def resolve(url, directory, *args):
        media = directory / "video.mp4"
        media.write_bytes(b"fixture")
        return {"path": media, "ext": ".mp4", "width": 1920, "height": 1080, "has_audio": False}

    monkeypatch.setattr(downloader, "download_page", resolve)

    def portrait(*args):
        video.content_orientation = orientation.PORTRAIT
        raise downloader.DownloadError("Содержимое ролика не горизонтальное")

    monkeypatch.setattr(downloader, "check_orientation", portrait)
    with pytest.raises(downloader.DownloadError, match="не горизонтальное"):
        downloader.run_job(job, None)
    assert video.license == license_before and job.cookies == []
    assert list((data_dir / "download-jobs" / "errors").rglob("*.invalid"))
    assert not list(tmp_path.rglob("*.mp4"))


def test_real_page_download_with_cookie_in_separate_process(data_dir):
    assert check_download(FIXTURES) == 0


def test_cancel_running_extractor_stops_child(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    cancel = threading.Event()
    requested = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requested.set()
            release.wait(5)
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def interrupt():
        requested.wait(5)
        cancel.set()

    interrupter = threading.Thread(target=interrupt)
    interrupter.start()
    try:
        with pytest.raises(site.SiteDownloadCancelled):
            site.download_page(f"http://127.0.0.1:{server.server_port}/video", tmp_path, 1080, None, cancel)
        assert requested.is_set() and not (tmp_path / "result.json").exists()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()
        interrupter.join()
