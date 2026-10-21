import pytest

from app.core import licenses
from app.core.models import SearchFilters
from app.sources.base import RateLimitError, SourceError
from app.sources.cache import Cache
from app.sources.http import USER_AGENT
from app.sources.wikimedia import WikimediaSource, strip_html
from tests.fakes import Clock, FakeResponse, FakeSession, fixture_text


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path, clock):
    c = Cache(tmp_path / "cache.sqlite", now=clock)
    yield c
    c.close()


def source(cache, clock, *responses):
    session = FakeSession(*responses)
    session.headers = {}
    return WikimediaSource(cache, session=session, now=clock)


def results(cache, clock):
    src = source(cache, clock, FakeResponse(text=fixture_text("wikimedia_search.json")))
    return src, src.search("laminator", SearchFilters())


def test_parse_real_response(cache, clock):
    src, found = results(cache, clock)
    assert len(found) == 5
    roll = next(r for r in found if r.title == "Heated Roll Lamination Machine")
    assert roll.source == "Wikimedia Commons"
    assert roll.description.startswith("Woman laminating signs")
    assert (roll.width, roll.height) == (640, 480)
    assert roll.duration == pytest.approx(91.269)
    assert roll.page_url.startswith("https://commons.wikimedia.org/wiki/File:")
    assert roll.preview_url.startswith("https://")
    assert roll.author == "ReginaDew"
    assert roll.author_url == "https://commons.wikimedia.org/wiki/User:ReginaDew"
    assert roll.has_audio is True  # исходник vp8 + vorbis
    assert roll.files[0].quality.endswith(", исходник")
    assert all(f.url.startswith("https://upload.wikimedia.org/") for f in roll.files)


def test_licenses_per_file(cache, clock):
    _, found = results(cache, clock)
    by_title = {r.title: r for r in found}
    roll = by_title["Heated Roll Lamination Machine"]
    assert roll.license.name == "CC BY-SA 4.0"
    assert roll.license.status == licenses.ATTRIBUTION
    assert roll.license.note == licenses.SHARE_ALIKE_NOTE
    assert roll.license.url == "https://creativecommons.org/licenses/by-sa/4.0"
    assert all(r.license.status == licenses.ATTRIBUTION for r in found)


def test_audio_unknown_vs_absent(cache, clock):
    _, found = results(cache, clock)
    needle = next(r for r in found if r.title == "Knitting needle")
    assert needle.has_audio is False  # исходник только theora


def test_preferred_attribution_field(cache, clock):
    _, found = results(cache, clock)
    assert any(r.author == "Jean-Louis Naudin" for r in found)


def test_request_params(cache, clock):
    src, _ = results(cache, clock)
    params = src.session.calls[0]["params"]
    assert params["gsrsearch"] == "filetype:video laminator"
    assert params["gsrnamespace"] == "6" and params["prop"] == "videoinfo"
    assert "LicenseShortName" in params["viextmetadatafilter"]
    assert src.session.headers["User-Agent"] == USER_AGENT


def test_polite_limit_and_retry_after(cache, clock):
    src = source(cache, clock, FakeResponse(429, "Too many requests", headers={"Retry-After": "120"}))
    with pytest.raises(RateLimitError):
        src.search("a", SearchFilters())
    with pytest.raises(RateLimitError, match="Повторите через 2 мин"):
        src.search("b", SearchFilters())
    assert len(src.session.calls) == 1


def test_api_error_and_maxlag(cache, clock):
    src = source(cache, clock, FakeResponse(text='{"error": {"code": "maxlag"}}'))
    with pytest.raises(SourceError, match="перегружена"):
        src.search("a", SearchFilters())


def test_strip_html():
    assert strip_html('<a href="x">Anna &amp; Co</a>  <b>x</b>') == "Anna & Co x"
