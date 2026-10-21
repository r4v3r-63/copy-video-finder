import pytest

from app.core.licenses import ALLOWED
from app.core.models import SearchFilters
from app.sources.base import AuthError, NotFoundError, RateLimitError
from app.sources.cache import Cache
from app.sources.pixabay import PixabaySource
from tests.fakes import Clock, FakeResponse, FakeSession, fixture_text


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path, clock):
    c = Cache(tmp_path / "cache.sqlite", now=clock)
    yield c
    c.close()


def source(cache, clock, *responses, key="secret"):
    return PixabaySource(key, cache, session=FakeSession(*responses), now=clock)


def test_parse_hits(cache, clock):
    src = source(cache, clock, FakeResponse(text=fixture_text("pixabay_search.json")))
    first, second = src.search("laminator", SearchFilters())
    assert first.key == ("Pixabay", "125")
    assert first.title == "laminator, office, paper"
    assert (first.width, first.height, first.duration) == (1920, 1080, 12)
    assert [f.quality for f in first.files] == ["Full HD", "HD", "SD", "SD"]
    assert first.preview_url == "https://cdn.pixabay.com/video/2020/01/01/125-1_medium.jpg"
    assert first.page_url == "https://pixabay.com/videos/id-125/"
    assert first.author == "Example-User"
    assert first.license.name == "Pixabay Content License" and first.license.status == ALLOWED
    # Пустой вариант large пропускается, размер берётся из лучшего доступного файла.
    assert (second.width, second.height) == (1280, 720)
    assert all(f.url for f in second.files)


def test_params_language_and_key_not_cached(cache, clock):
    src = source(cache, clock, FakeResponse(text='{"hits": []}'))
    src.search("ламинатор" * 20, SearchFilters(), lang="ru")
    params = src.session.calls[0]["params"]
    assert src.session.calls[0]["url"] == "https://pixabay.com/api/videos/"
    assert params["key"] == "secret" and params["lang"] == "ru" and params["video_type"] == "film"
    assert len(params["q"]) == 100
    keys = [row[0] for row in cache._db.execute("SELECT key FROM responses")]
    assert keys and all("secret" not in k for k in keys)


def test_cached_24_hours(cache, clock):
    src = source(cache, clock, FakeResponse(text='{"hits": []}'))
    src.search("a", SearchFilters())
    src.search("a", SearchFilters())
    assert len(src.session.calls) == 1
    clock.t += 24 * 3600 + 1
    src.search("a", SearchFilters())
    assert len(src.session.calls) == 2


def test_minute_limit(cache, clock):
    src = source(cache, clock, FakeResponse(text='{"hits": []}'))
    for i in range(100):
        src.search(f"q{i}", SearchFilters())
    with pytest.raises(RateLimitError, match="в минуту"):
        src.search("more", SearchFilters())
    clock.t += 61
    src.search("more", SearchFilters())
    assert len(src.session.calls) == 101


def test_server_limit_headers(cache, clock):
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "30"}
    src = source(cache, clock, FakeResponse(text='{"hits": []}', headers=headers))
    src.search("a", SearchFilters())
    with pytest.raises(RateLimitError):
        src.search("b", SearchFilters())
    clock.t += 31
    src.search("b", SearchFilters())


def test_bad_or_missing_key(cache, clock):
    with pytest.raises(AuthError, match="отклонён"):
        source(cache, clock, FakeResponse(400, "[ERROR 400] Invalid or missing API key")).search("a", SearchFilters())
    src = source(cache, clock, key="")
    assert not src.is_configured()
    with pytest.raises(AuthError, match="не задан"):
        src.search("a", SearchFilters())
    assert src.session.calls == []


def test_details_not_found(cache, clock):
    with pytest.raises(NotFoundError):
        source(cache, clock, FakeResponse(text='{"hits": []}')).get_details("1")
