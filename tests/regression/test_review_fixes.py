"""Ошибки, найденные при ревью кода, — чтобы не вернулись."""

import json
import threading

import pytest

from app import paths
from app.core import processes


def test_built_in_tavily_and_brave_keys_are_used(tmp_path):
    """Сборка пишет ключ Tavily в defaults.json — программа должна его брать."""
    from app.storage.settings import built_in_keys
    defaults = tmp_path / "defaults.json"
    defaults.write_text(json.dumps({"tavily_api_key": "tvly-x", "brave_api_key": "b-x"}), encoding="utf-8")
    keys = built_in_keys(defaults)
    assert keys == {"tavily_api_key": "tvly-x", "brave_api_key": "b-x"}


def test_cleared_fields_stay_cleared():
    """Очищенные в редакторе слова-исключения не возвращаются из встроенного справочника."""
    categories = processes.load_catalog()
    laser = next(p for p in processes.all_processes(categories) if p.id == "digital_color_laser")
    assert laser.exclude  # во встроенном есть «3d»
    laser.exclude, laser.aliases = [], []
    processes.save_catalog(categories)
    loaded = {p.id: p for p in processes.all_processes(processes.load_catalog())}
    assert loaded["digital_color_laser"].exclude == [] and loaded["digital_color_laser"].aliases == []
    assert paths.processes_file().exists()


@pytest.mark.parametrize("url, skip", [
    ("https://x.com/user/status/1", True),
    ("https://www.dropbox.com/s/video.mp4", False),  # кончается на «x.com», но это не X
    ("https://www.netflix.com/title/1", False),
    ("https://m.youtube.com/watch?v=1", False),  # YouTube ищется (решение пользователя 20.10.2026)
    ("https://youtu.be/1", False),
    ("https://www.amazon.co.uk/x", True),
    ("https://s3.amazonaws.com/bucket/clip.mp4", False),
    ("https://www.gettyimages.de/videos/x", True),
    ("https://stock.adobe.com/video/1", True),
])
def test_skip_domains_match_whole_names(url, skip):
    from app.sources.web import skipped
    assert skipped(url) is skip


def test_numeric_duration_in_markup():
    from app.sources.web import _duration
    assert _duration(30) is None and _duration({"x": 1}) is None
    assert _duration("PT1M30S") == 90


def test_rate_limit_shared_between_instances(tmp_path):
    """Два экземпляра площадки в разных потоках вместе не превышают лимит."""
    from app.core.models import SearchFilters
    from app.sources.base import RateLimitError
    from app.sources.cache import Cache
    from app.sources.vimeo import VimeoSource
    from tests.fakes import FakeResponse, FakeSession

    cache = Cache(tmp_path / "c.sqlite")
    try:
        sources = [VimeoSource("t", cache, session=FakeSession(FakeResponse(text='{"data": []}'))) for _ in range(2)]
        for s in sources:
            s.limiter.limits = [(5, 3600, "в час")]
        errors, start = [], threading.Barrier(2)

        def run(src, n):
            start.wait()
            for i in range(5):
                try:
                    src.search(f"q{n}-{i}", SearchFilters())
                except RateLimitError:
                    errors.append(1)

        threads = [threading.Thread(target=run, args=(s, n)) for n, s in enumerate(sources)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sent = sum(len(s.session.calls) for s in sources)
        assert sent == 5 and len(errors) == 5
    finally:
        cache.close()


def test_coverr_premium_license_kept():
    from app.core import licenses
    from app.core.models import LicenseInfo, VideoResult
    from app.sources.coverr import CoverrSource
    r = VideoResult(source="Coverr", source_id="1", title="x", description="", preview_url=None, duration=10,
                    width=1920, height=1080, has_audio=None, page_url="",
                    license=LicenseInfo("Coverr Premium", status=licenses.REJECTED))
    assert CoverrSource("k", None).get_license(r).status == licenses.REJECTED
