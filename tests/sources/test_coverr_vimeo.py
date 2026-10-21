from datetime import UTC

import pytest
from PyQt6.QtCore import QCoreApplication

from app.core import licenses
from app.core.models import SearchFilters
from app.core.query import EN, RU, SearchPlan, SearchQuery
from app.services.search import Search, preview_file
from app.sources.base import AuthError, RateLimitError
from app.sources.cache import Cache
from app.sources.coverr import CoverrSource
from app.sources.vimeo import NO_FILES_NOTE, VimeoSource
from tests.fakes import Clock, FakeResponse, FakeSession, fixture_text


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path, clock):
    c = Cache(tmp_path / "cache.sqlite", now=clock)
    yield c
    c.close()


def coverr(cache, clock, *responses, key="ckey"):
    return CoverrSource(key, cache, session=FakeSession(*responses), now=clock)


def vimeo(cache, clock, *responses, token="vtoken"):
    return VimeoSource(token, cache, session=FakeSession(*responses), now=clock)


# --- Coverr ---

def test_coverr_parse_real_response(cache, clock):
    src = coverr(cache, clock, FakeResponse(text=fixture_text("coverr_search.json")))
    found = src.search("printing", SearchFilters())
    first = found[0]
    assert first.key[0] == "Coverr" and first.title
    assert isinstance(first.duration, float) and first.duration > 0  # в ответе — строка
    assert first.width and first.height
    assert first.page_url.startswith("https://coverr.co/videos/") and first.page_url != "https://coverr.co/videos/"
    assert first.license.name == "Coverr License" and first.license.status == licenses.ALLOWED
    assert "обучения ИИ" in first.license.note
    # Скачивание — только через mp4_download (так Coverr учитывает скачивания), просмотр — mp4_preview.
    assert len(first.files) == 1 and first.files[0].url.startswith("https://cdn.coverr.co/")
    assert preview_file(first).url == first.preview_video
    # Все ролики проходят фильтр длительности без ошибок сравнения строки с числом.
    from app.services.search import rejection_reason
    for r in found:
        rejection_reason(r, SearchFilters())


def test_coverr_premium_rejected_ai_marked(cache, clock):
    src = coverr(cache, clock, FakeResponse(text=fixture_text("coverr_search.json")))
    by_id = {r.source_id: r for r in src.search("printing", SearchFilters())}
    assert by_id["PREMIUM01"].license.status == licenses.REJECTED
    assert by_id["AIGEN01"].license.status == licenses.ALLOWED
    assert "создан ИИ" in by_id["AIGEN01"].license.note


def test_coverr_request(cache, clock):
    src = coverr(cache, clock, FakeResponse(text='{"hits": []}'))
    src.search("printer", SearchFilters(), page=2)
    call = src.session.calls[0]
    assert call["url"] == "https://api.coverr.co/videos"
    assert call["headers"] == {"Authorization": "Bearer ckey"}
    assert call["params"] == {"query": "printer", "page": 1, "page_size": 20, "urls": "true"}
    keys = [row[0] for row in cache._db.execute("SELECT key FROM responses")]
    assert all("ckey" not in k for k in keys)


def test_coverr_hourly_limit_50(cache, clock):
    src = coverr(cache, clock, FakeResponse(text='{"hits": []}'))
    for i in range(50):
        src.search(f"q{i}", SearchFilters())
    with pytest.raises(RateLimitError, match="50 запросов в час"):
        src.search("more", SearchFilters())


def test_coverr_english_only_and_key_required(cache, clock):
    assert CoverrSource.languages == {"en"}
    src = coverr(cache, clock, key="")
    assert not src.is_configured()
    with pytest.raises(AuthError):
        src.search("x", SearchFilters())


def test_worker_sends_only_supported_languages(cache, clock):
    QCoreApplication.instance() or QCoreApplication([])
    src = coverr(cache, clock, FakeResponse(text='{"hits": []}'))
    plan = SearchPlan(None, [SearchQuery("ламинатор", RU), SearchQuery("laminator", EN)])
    Search(plan, [src], SearchFilters()).run()
    assert [c["params"]["query"] for c in src.session.calls] == ["laminator"]


# --- Vimeo ---

def test_vimeo_parse_and_licenses(cache, clock):
    src = vimeo(cache, clock, FakeResponse(text=fixture_text("vimeo_search.json")))
    by, by_sa, nc_nd = src.search("printing", SearchFilters())
    assert by.key == ("Vimeo", "111")
    assert by.title == "Screen printing workshop"
    assert by.description.startswith("screen printing, posters")
    assert VimeoSource.topic_in_description is False  # теги Vimeo ненадёжны
    assert by.page_url == "https://vimeo.com/111" and by.author == "Print Studio"
    assert by.preview_url == "https://i.vimeocdn.com/video/111_640x360"
    assert by.license.name == "CC BY" and by.license.status == licenses.ATTRIBUTION
    assert by.license.url == "https://creativecommons.org/licenses/by/3.0/"
    assert [f.quality for f in by.files] == ["Full HD", "SD"]  # исходник не предлагается
    assert by_sa.license.status == licenses.ATTRIBUTION and licenses.SHARE_ALIKE_NOTE in by_sa.license.note
    assert nc_nd.license.status == licenses.REJECTED


def test_vimeo_without_download_links(cache, clock):
    src = vimeo(cache, clock, FakeResponse(text=fixture_text("vimeo_search.json")))
    by_sa = src.search("x", SearchFilters())[1]
    assert by_sa.files == [] and NO_FILES_NOTE in by_sa.license.note


def test_vimeo_request(cache, clock):
    src = vimeo(cache, clock, FakeResponse(text='{"data": []}'))
    src.search("bookbinding", SearchFilters())
    call = src.session.calls[0]
    assert call["url"] == "https://api.vimeo.com/videos"
    assert call["headers"]["Authorization"] == "bearer vtoken"
    assert "version=3.4" in call["headers"]["Accept"]
    assert call["params"]["filter"] == "CC" and "license" in call["params"]["fields"]


def test_vimeo_rate_limit_headers(cache, clock):
    from datetime import datetime
    reset = datetime.fromtimestamp(clock.t + 120, UTC).isoformat()
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}
    src = vimeo(cache, clock, FakeResponse(text='{"data": []}', headers=headers))
    src.search("a", SearchFilters())
    with pytest.raises(RateLimitError):
        src.search("b", SearchFilters())
    clock.t += 121
    src.search("b", SearchFilters())


def test_vimeo_token_required(cache, clock):
    src = vimeo(cache, clock, token="")
    with pytest.raises(AuthError, match="Токен Vimeo не задан"):
        src.search("x", SearchFilters())


def test_not_downloadable_results_go_last(cache, clock):
    QCoreApplication.instance() or QCoreApplication([])
    src = vimeo(cache, clock, FakeResponse(text=fixture_text("vimeo_search.json")))
    plan = SearchPlan(None, [SearchQuery("screen printing", EN), SearchQuery("bookbinding", EN)])
    worker = Search(plan, [src], SearchFilters())
    found = []
    worker.on_found = found.extend
    worker.run()
    by_id = {r.source_id: r for r in found}
    assert by_id["111"].relevance > by_id["222"].relevance  # 222 без ссылок на файл


def test_batch_waits_for_minute_limit(cache, clock):
    """Пакетный поиск пережидает поминутный лимит, а не пропускает площадку; лимит не обходится."""
    from datetime import datetime
    reset = datetime.fromtimestamp(clock.t + 40, UTC).isoformat()
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}
    src = vimeo(cache, clock, FakeResponse(text='{"data": []}', headers=headers), FakeResponse(text='{"data": []}'))
    waited = []

    class Stop:  # вместо настоящего ожидания — сдвиг часов
        def wait(self, seconds):
            waited.append(seconds)
            clock.t += seconds
            return False

    src.stop = Stop()
    src.search("a", SearchFilters())
    src.max_wait = 70
    src.search("b", SearchFilters())
    assert waited and 39 <= waited[0] <= 42
    assert len(src.session.calls) == 2


def test_long_limit_not_waited(cache, clock):
    from datetime import datetime
    reset = datetime.fromtimestamp(clock.t + 3600, UTC).isoformat()
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}
    src = vimeo(cache, clock, FakeResponse(text='{"data": []}', headers=headers))
    src.search("a", SearchFilters())
    src.max_wait = 70
    with pytest.raises(RateLimitError):
        src.search("b", SearchFilters())


def test_batch_problem_shown_once():
    from app.workers.batch import BatchWorker
    worker = BatchWorker([], [], [], SearchFilters(), None, None, set())
    worker._on_problem("Исчерпан лимит Vimeo (50 запросов в минуту). Повторите через 12 с.")
    worker._on_problem("Исчерпан лимит Vimeo (50 запросов в минуту). Повторите через 3 с.")
    assert worker.problems == ["Исчерпан лимит Vimeo (50 запросов в минуту)."]


def test_one_second_limit_is_waited_in_normal_search(cache, clock):
    """Лимит «1 запрос в секунду» (Tavily) при одновременных запросах — короткая пауза, а не ошибка."""
    src = vimeo(cache, clock, FakeResponse(text='{"data": []}'))
    src.limits = []
    from app.sources.cache import RateLimiter
    src.limiter = RateLimiter(cache, src.name, [(1, 1.0, "в секунду")], now=clock)
    waited = []

    class Stop:
        def wait(self, seconds):
            waited.append(seconds)
            clock.t += seconds
            return False

    src.stop = Stop()
    src.search("a", SearchFilters())
    src.search("b", SearchFilters())  # max_wait = 0, но пауза в секунду — ждём
    assert waited and waited[0] <= 1.5
