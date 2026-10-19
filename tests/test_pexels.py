import pytest

from app.cache import Cache
from app.licenses import ALLOWED
from app.sources.base import AuthError, NotFoundError, RateLimitError, SearchFilters
from app.sources.pexels import PexelsSource, parse_video
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
    return PexelsSource(key, cache, session=FakeSession(*responses), now=clock)


def test_parse_search_results(cache, clock):
    src = source(cache, clock, FakeResponse(text=fixture_text("pexels_search.json")))
    results = src.search("laminator", SearchFilters())
    assert [r.source_id for r in results] == ["1001", "1002", "1003"]
    first = results[0]
    assert first.key == ("Pexels", "1001")
    assert first.title == "Woman using a laminating machine"
    assert first.author == "Anna Example"
    assert first.duration == 15 and (first.width, first.height) == (1920, 1080)
    assert [f.quality for f in first.files] == ["4K", "Full HD", "SD"]
    assert first.license.name == "Pexels License" and first.license.status == ALLOWED
    assert results[2].title == ""  # в адресе нет описания


def test_file_links_only_from_api(cache, clock):
    src = source(cache, clock, FakeResponse(text=fixture_text("pexels_search.json")))
    urls = {f.url for r in src.search("x", SearchFilters()) for f in r.files}
    assert urls == {
        "https://videos.pexels.com/video-files/1001/sd_640_360.mp4",
        "https://videos.pexels.com/video-files/1001/hd_1920_1080.mp4",
        "https://videos.pexels.com/video-files/1001/uhd_3840_2160.mp4",
        "https://videos.pexels.com/video-files/1002/hd_1080_1920.mp4",
        "https://videos.pexels.com/video-files/1003/hd_1280_720.mp4",
    }


def test_request_params_and_language(cache, clock):
    src = source(cache, clock, FakeResponse(text='{"videos": []}'))
    src.search("ламинатор", SearchFilters(orientation="landscape", min_height=1080), lang="ru")
    call = src.session.calls[0]
    assert call["url"] == "https://api.pexels.com/videos/search"
    assert call["headers"] == {"Authorization": "secret"}
    assert call["params"]["locale"] == "ru-RU"
    assert call["params"]["orientation"] == "landscape"
    assert call["params"]["size"] == "medium"
    src.search("laminator", SearchFilters(), lang="en")
    assert src.session.calls[1]["params"]["locale"] == "en-US"
    assert "orientation" not in src.session.calls[1]["params"]


def test_responses_cached_for_24_hours(cache, clock):
    src = source(cache, clock, FakeResponse(text=fixture_text("pexels_search.json")))
    src.search("laminator", SearchFilters())
    src.search("laminator", SearchFilters())
    assert len(src.session.calls) == 1
    clock.t += 24 * 3600 + 1
    src.search("laminator", SearchFilters())
    assert len(src.session.calls) == 2


def test_cache_key_does_not_contain_api_key(cache, clock):
    src = source(cache, clock, FakeResponse(text='{"videos": []}'))
    src.search("x", SearchFilters())
    keys = [row[0] for row in cache._db.execute("SELECT key FROM responses")]
    assert keys and all("secret" not in k for k in keys)


def test_hourly_limit_not_exceeded(cache, clock):
    src = source(cache, clock, FakeResponse(text='{"videos": []}'))
    for i in range(200):
        src.search(f"q{i}", SearchFilters())
    with pytest.raises(RateLimitError, match="200 запросов в час"):
        src.search("one more", SearchFilters())
    assert len(src.session.calls) == 200
    clock.t += 3601
    src.search("one more", SearchFilters())
    assert len(src.session.calls) == 201


def test_monthly_limit(cache, clock):
    with cache._db:
        cache._db.executemany("INSERT INTO requests VALUES (?, ?)", [("Pexels", clock.t)] * 20000)
    clock.t += 3601  # часовое окно прошло, месячное — нет
    src = source(cache, clock, FakeResponse(text='{"videos": []}'))
    with pytest.raises(RateLimitError, match="в месяц"):
        src.search("x", SearchFilters())
    assert src.session.calls == []


def test_server_reported_limit_blocks_until_reset(cache, clock):
    headers = {"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": str(clock.t + 600)}
    src = source(cache, clock, FakeResponse(text='{"videos": []}', headers=headers))
    src.search("a", SearchFilters())
    with pytest.raises(RateLimitError):
        src.search("b", SearchFilters())
    clock.t += 601
    src.search("b", SearchFilters())
    assert len(src.session.calls) == 2


def test_missing_key(cache, clock):
    src = source(cache, clock, key="")
    with pytest.raises(AuthError, match="не задан"):
        src.search("x", SearchFilters())
    assert src.session.calls == []


def test_bad_key_and_not_found(cache, clock):
    with pytest.raises(AuthError, match="отклонён"):
        source(cache, clock, FakeResponse(401)).search("x", SearchFilters())
    with pytest.raises(NotFoundError):
        source(cache, clock, FakeResponse(404)).get_details("5")


def test_get_details(cache, clock):
    import json
    video = json.loads(fixture_text("pexels_search.json"))["videos"][0]
    src = source(cache, clock, FakeResponse(text=json.dumps(video)))
    r = src.get_details("1001")
    assert src.session.calls[0]["url"] == "https://api.pexels.com/videos/videos/1001"
    assert r == parse_video(video)
