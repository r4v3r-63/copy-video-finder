"""Ссылки выдачи, реальные границы API, фильтры, разрешения и отдельные загрузки."""

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.stock_link import ListingFilters, choose_quality, parse_link
from app.services.downloader import Cancelled, DownloadJob
from app.services.stock_download import existing_download, run_stock_job
from app.services.stock_listing import collect_listing
from app.sources.archive import ArchiveSource
from app.sources.base import RateLimitError
from app.sources.cache import Cache
from app.sources.listings import ArchiveListing, PixabayListing, make_listing
from app.sources.pixabay import PixabaySource, parse_hit
from app.storage.download_files import metadata_file
from app.workers.stock_link import StockDownloadWorker
from tests.fakes import FakeResponse, FakeSession, StreamResponse, StreamSession, fixture_text


@pytest.fixture
def videos():
    return [parse_hit(item) for item in json.loads(fixture_text("stock_link/pixabay.json"))["hits"]]


@pytest.fixture
def cache(tmp_path):
    value = Cache(tmp_path / "cache.sqlite")
    yield value
    value.close()


def test_pixabay_url_reproduces_only_supported_parameters():
    link = parse_link("https://pixabay.com/ru/videos/search/печать/?orientation=horizontal&time_lapse=true&order=latest")
    assert link.source == "Pixabay" and link.batch_enabled
    assert link.params["q"] == "печать" and link.params["lang"] == "ru" and link.params["order"] == "latest"
    assert link.orientation == "landscape" and "time_lapse" not in link.params and "orientation" not in link.params
    assert any("без подтверждения таймлапса" in message for message in link.warnings)
    assert parse_link("https://pixabay.com/videos/search/?orientation=horizontal&time_lapse=true").params["q"] == ""


@pytest.mark.parametrize("url", ["file:///video", "https://pixabay.com.evil.org/videos/search/",
    "https://u:p@pixabay.com/videos/search/", "https://pixabay.com/videos/id-123/",
    "https://pixabay.com/videos/search/?page=0", "https://pixabay.com/videos/search/?page=abc",
    "https://pixabay.com/videos/search/?q=a&q=b", "https://pixabay.com:444/videos/search/",
    "https://pixabay.com/videos/search/?min_width=-1"])
def test_invalid_or_unsupported_links(url):
    with pytest.raises(ValueError):
        parse_link(url)


@pytest.mark.parametrize(("minimum", "maximum", "ids"), [
    (None, None, [201, 202, 203, 204, 205, 206, 207]), (10, 30, [201, 202, 206, 207]),
    (30, None, [202, 204]), (None, 10, [201, 203]), (0, 0, []), (None, 0, [])])
def test_duration_bounds_are_inclusive_and_unknown_is_excluded(videos, minimum, maximum, ids):
    filters = ListingFilters(minimum, maximum)
    assert [int(video.source_id) for video in videos if filters.allows(video)] == ids


@pytest.mark.parametrize(("minimum", "maximum"), [(-1, None), (None, -1), (31, 30), (float('nan'), None),
                                                       (None, float('inf'))])
def test_invalid_duration(minimum, maximum):
    with pytest.raises(ValueError):
        ListingFilters(minimum, maximum)


def test_orientation_and_duration_work_together(videos):
    assert [v.source_id for v in videos if ListingFilters(10, 30, "landscape").allows(v)] == ["201", "202"]
    assert [v.source_id for v in videos if ListingFilters(10, 30, "portrait").allows(v)] == ["206"]
    assert not ListingFilters(orientation="portrait").allows(replace(videos[0], width=None))
    file, note = choose_quality(videos[0], 720)
    assert file and note and file.size == 2048
    assert choose_quality(videos[0], 1080)[1] == ""


def test_pixabay_pagination_reaches_real_500_limit_and_deduplicates(cache, videos):
    base = json.loads(fixture_text("stock_link/pixabay.json"))["hits"][0]
    pages = []
    for start, end in ((1, 201), (201, 401), (401, 501)):
        hits = [{**base, "id": i} for i in range(start, end)]
        pages.append(FakeResponse(text=json.dumps({"total": 900, "totalHits": 500, "hits": hits})))
    source = PixabaySource("fixture-key", cache, session=FakeSession(*pages))
    found = []
    collect_listing(parse_link("https://pixabay.com/videos/search/"), PixabayListing(source), threading.Event(),
                    lambda items, total, available: found.extend(items))
    assert len(found) == 500 and len({v.key for v in found}) == 500
    assert [c["params"]["page"] for c in source.session.calls] == [1, 2, 3]
    assert all(c["params"]["per_page"] == 200 for c in source.session.calls)
    assert not any("fixture-key" in row[0] for row in cache._db.execute("SELECT key FROM responses"))


def test_archive_bulk_has_no_artificial_result_limit(cache):
    pages = []
    for start, end in ((1, 101), (101, 201), (201, 206)):
        docs = [{"identifier": str(i), "title": "Ролик", "runtime": "20",
                 "licenseurl": "https://creativecommons.org/licenses/by/4.0/"} for i in range(start, end)]
        pages.append(FakeResponse(text=json.dumps({"response": {"numFound": 205, "docs": docs}})))
    source = ArchiveSource(cache, FakeSession(*pages))
    found = []
    link = parse_link("https://archive.org/search?query=printing&sort[]=date+desc")
    assert link.batch_enabled
    collect_listing(link, ArchiveListing(source), threading.Event(), lambda items, *_args: found.extend(items))
    assert len(found) == 205 and len(source.session.calls) == 3
    assert source.session.calls[0]["params"]["sort[]"] == ["date desc"]


def test_listing_cancel_and_duplicate_page_protection(videos):
    from app.sources.base import SourceError
    from app.sources.listings import ListingPage

    class Adapter:
        def page(self, *args):
            return ListingPage([videos[0], videos[0]], 10, 10, 2)

    found = []
    with pytest.raises(SourceError, match="повторила"):
        collect_listing(parse_link("https://pixabay.com/videos/search/"), Adapter(), threading.Event(),
                        lambda items, *_args: found.extend(items))
    assert len(found) == 1
    cancelled = threading.Event()
    cancelled.set()
    collect_listing(parse_link("https://pixabay.com/videos/search/"), Adapter(), cancelled,
                    lambda *_args: pytest.fail("Отменённый поиск не должен публиковать страницы"))


def test_missing_key_and_rate_limit_are_not_bypassed(cache):
    link = parse_link("https://pixabay.com/videos/search/")
    with pytest.raises(Exception, match="не задан"):
        make_listing(link, [PixabaySource("", cache)])
    source = PixabaySource("fixture-key", cache, FakeSession(FakeResponse(429, headers={"Retry-After": "60"})))
    with pytest.raises(RateLimitError):
        PixabayListing(source).page(link, 1)
    with pytest.raises(RateLimitError):
        PixabayListing(source).page(link, 2)
    assert len(source.session.calls) == 1


def job_for(video, folder):
    file, _note = choose_quality(video, 0)
    file = replace(file, size=None)
    return DownloadJob(video, file, None, "По ссылке", folder, convert=False,
                       check_content=False, flat_folder=True)


def test_download_keeps_original_bytes_metadata_and_does_not_confirm_license(videos, tmp_path):
    payload = Path("tests/fixtures/site_player/landscape.mp4").read_bytes()
    job = job_for(videos[0], tmp_path)
    session = StreamSession(StreamResponse(payload))
    file, _note = run_stock_job(job, session)
    assert file.read_bytes() == payload and "201" in file.name and len(session.calls) == 1
    metadata = json.loads(metadata_file(file).read_text(encoding="utf-8"))
    assert not metadata["license_confirmed"] and metadata["source"] == "Pixabay"
    assert metadata["size"] == len(payload) and metadata["original_url"] == videos[0].page_url
    assert videos[0].content_orientation == ""
    assert not list(tmp_path.glob("*.part"))
    second = job_for(videos[0], tmp_path)
    assert existing_download(second) == file
    assert run_stock_job(second, StreamSession())[1].startswith("уже скачан")
    file.write_bytes(b"corrupted")
    assert existing_download(second) is None
    repaired, _note = run_stock_job(second, StreamSession(StreamResponse(payload)))
    assert repaired.read_bytes() == payload


def test_cancel_while_paused_then_restart(videos, tmp_path):
    job = job_for(videos[0], tmp_path)
    job.paused.set()
    timer = threading.Timer(0.1, job.cancel.set)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            run_stock_job(job, StreamSession(StreamResponse(b"fixture", headers={"ETag": "v1"})))
    finally:
        timer.join()
    assert not list(tmp_path.glob("*.mp4"))
    restarted = job_for(videos[0], tmp_path)
    file, _note = run_stock_job(restarted, StreamSession(StreamResponse(b"fixture")))
    assert file.read_bytes() == b"fixture"


def test_queue_continues_after_file_error_and_supports_pause(videos, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt

    from app.services import stock_download

    jobs = [job_for(video, tmp_path) for video in videos[:2]]
    worker = StockDownloadWorker(jobs)
    worker.pause()
    assert all(job.paused.is_set() for job in jobs)
    worker.resume()
    assert not any(job.paused.is_set() for job in jobs)
    failed, done = [], []
    worker.failed.connect(lambda index, text: failed.append(index), Qt.ConnectionType.DirectConnection)
    worker.done.connect(lambda index, path, note: done.append(index), Qt.ConnectionType.DirectConnection)

    def run(job, *args):
        if job is jobs[0]:
            raise OSError("нет места")
        return tmp_path / "video.mp4", ""

    monkeypatch.setattr(stock_download, "run_stock_job", run)
    worker.run()
    assert failed == [0] and done == [1]


def test_api_size_mismatch_is_not_recorded_as_success(videos, tmp_path):
    from app.services.downloader import DownloadError

    job = job_for(videos[0], tmp_path)
    job.file.size = 100
    with pytest.raises(DownloadError, match="Размер файла"):
        run_stock_job(job, StreamSession(StreamResponse(b"short")))
    assert list((tmp_path / "Данные" / "download-jobs" / "errors").rglob("*.invalid"))
    assert not list(tmp_path.glob("*.mp4")) and not list(tmp_path.glob("*.json"))


def test_unreadable_video_is_quarantined(videos, tmp_path, monkeypatch):
    from app.services import stock_download
    from app.services.downloader import DownloadError

    monkeypatch.setattr(stock_download, "probe", lambda *args: None)
    with pytest.raises(DownloadError, match="повреждён"):
        run_stock_job(job_for(videos[0], tmp_path), StreamSession(StreamResponse(b"not video")),
                      ffprobe=tmp_path / "ffprobe")
    assert list((tmp_path / "Данные" / "download-jobs" / "errors").rglob("*.invalid"))
    assert not list(tmp_path.glob("*.mp4")) and not list(tmp_path.glob("*.json"))


def test_api_error_does_not_publish_a_page(cache):
    from app.sources.base import AuthError

    link = parse_link("https://pixabay.com/videos/search/")
    source = PixabaySource("fixture-key", cache, FakeSession(FakeResponse(400, "Invalid API key")))
    with pytest.raises(AuthError):
        collect_listing(link, PixabayListing(source), threading.Event(),
                        lambda *args: pytest.fail("При отказе ключа не должно быть результатов"))


def test_network_error_does_not_expose_api_key(cache):
    import requests

    from app.workers.stock_link import ListingWorker

    class BrokenSession:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("request /api/videos/?key=fixture-private-value timed out")

    source = PixabaySource("fixture-private-value", cache, BrokenSession())
    worker = ListingWorker(parse_link("https://pixabay.com/videos/search/"), PixabayListing(source))
    problems = []
    worker.problem.connect(problems.append)
    worker.run()
    assert len(problems) == 1 and "таймаут" in problems[0]
    assert "fixture-private-value" not in problems[0] and "key=" not in problems[0]


def test_long_ids_have_distinct_output_names(videos):
    from app.services.downloader import file_stem

    first = replace(videos[0], source_id="x" * 130 + "1")
    second = replace(videos[0], source_id="x" * 130 + "2")
    assert file_stem(None, first) == file_stem(None, second)
    assert file_stem(None, first, unique=True) != file_stem(None, second, unique=True)


def test_previous_0260_metadata_is_recognised(videos, tmp_path):
    from app.services.downloader import file_stem

    job = job_for(videos[0], tmp_path)
    file, _note = run_stock_job(job, StreamSession(StreamResponse(b"fixture")))
    old_name = file_stem(None, job.result)
    metadata = json.loads(metadata_file(file).read_text(encoding="utf-8"))
    old_file = tmp_path / (old_name + file.suffix)
    file.replace(old_file)
    metadata_file(file).unlink()
    metadata["filename"] = old_file.name
    (tmp_path / (old_name + ".json")).write_text(json.dumps(metadata), encoding="utf-8")
    assert existing_download(job) == old_file


@pytest.mark.parametrize("count", [0, -1, 1.5, True])
def test_invalid_batch_quantity(videos, count):
    from app.core.stock_link import limit_downloads

    with pytest.raises(ValueError, match="положительным целым"):
        limit_downloads(videos, count, batch_enabled=True)


def test_batch_quantity_counts_unique_unfinished_results(videos):
    from app.core.stock_link import limit_downloads

    selected = limit_downloads([videos[0], videos[0], *videos[1:]], 2, batch_enabled=True,
                               completed={videos[0].key})
    assert [video.source_id for video in selected] == ["202", "203"]
    assert len(limit_downloads(videos, None, batch_enabled=True)) == 7
    assert len(limit_downloads(videos, 1000, batch_enabled=True)) == 7


def test_parallel_queue_overlaps_files_and_keeps_sessions_separate(videos, tmp_path, monkeypatch):
    from app.services import stock_download

    jobs = [job_for(video, tmp_path) for video in videos[:5]]
    barrier = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    active = peak = 0
    sessions = {}
    completed, progress = [], []

    def run(job, session, report, *args):
        nonlocal active, peak
        index = jobs.index(job)
        with lock:
            active += 1
            peak = max(peak, active)
            thread = threading.get_ident()
            assert thread not in sessions or sessions[thread] is session
            sessions[thread] = session
        if index < 2:
            barrier.wait()
        report(index + 1, 100)
        with lock:
            active -= 1
        return tmp_path / f"{index}.mp4", ""

    monkeypatch.setattr(stock_download, "run_stock_job", run)
    stock_download.run_stock_queue(jobs, threading.Event(), threading.Event(),
                                   lambda i, received, total: progress.append((i, received)),
                                   lambda i, path, note: completed.append(i),
                                   lambda i, message: pytest.fail(message), parallel=2)
    assert peak == 2 and active == 0
    assert len({id(session) for session in sessions.values()}) == 2
    assert sorted(completed) == list(range(5))
    assert sorted(progress) == [(i, i + 1) for i in range(5)]


def test_parallel_cancel_does_not_start_remaining_jobs_and_joins_active(videos, tmp_path, monkeypatch):
    from app.services import stock_download

    jobs = [job_for(video, tmp_path) for video in videos[:5]]
    worker = StockDownloadWorker(jobs, parallel=2)
    barrier = threading.Barrier(2, timeout=5)
    started, failed = [], []

    def run(job, *args):
        started.append(job.result.key)
        barrier.wait()
        worker.request_stop()
        assert job.cancel.is_set() and not job.paused.is_set()
        raise Cancelled()

    monkeypatch.setattr(stock_download, "run_stock_job", run)
    stock_download.run_stock_queue(jobs, worker.cancel, worker.paused, lambda *args: None,
                                   lambda *args: pytest.fail("Отменённый файл не завершён"),
                                   lambda i, text: failed.append(i), parallel=2)
    assert len(started) == 2 and sorted(failed) == list(range(5))
    assert all(job.cancel.is_set() for job in jobs)


def test_parallel_pause_does_not_start_files_until_resume(videos, tmp_path, monkeypatch):
    from app.services import stock_download

    worker = StockDownloadWorker([job_for(video, tmp_path) for video in videos[:2]], parallel=2)
    started = threading.Event()
    worker.pause()
    monkeypatch.setattr(stock_download, "run_stock_job",
                        lambda *args: (started.set() or tmp_path / "video.mp4", ""))
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert not started.wait(0.1)
        worker.resume()
        assert started.wait(5)
    finally:
        worker.request_stop()
        thread.join(5)
    assert not thread.is_alive()
