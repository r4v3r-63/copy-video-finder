import pytest

from app import licenses
from app.cache import Cache
from app.sources.archive import NO_FILES_NOTE, ArchiveSource, parse_runtime
from app.sources.base import AuthError, SearchFilters
from app.sources.europeana import EuropeanaSource
from tests.fakes import Clock, FakeResponse, FakeSession, fixture_text


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path, clock):
    c = Cache(tmp_path / "cache.sqlite", now=clock)
    yield c
    c.close()


@pytest.mark.parametrize("url, name, status", [
    ("http://creativecommons.org/licenses/by-sa/4.0/", "CC BY-SA 4.0", licenses.ATTRIBUTION),
    ("https://creativecommons.org/licenses/by/3.0/", "CC BY 3.0", licenses.ATTRIBUTION),
    ("http://creativecommons.org/publicdomain/zero/1.0/", "CC0", licenses.ALLOWED),
    ("http://creativecommons.org/publicdomain/mark/1.0/", "Public Domain", licenses.ALLOWED),
    ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "CC BY-NC-SA 4.0", licenses.REJECTED),
    ("http://rightsstatements.org/vocab/InC/1.0/", "", licenses.REVIEW),
    ("//creativecommons.org/licenses/by/4.0/deed.ru", "CC BY 4.0", licenses.ATTRIBUTION),
    ("https://not-creativecommons.org/licenses/by/4.0/", "", licenses.REVIEW),
    ("https://evil.example/?u=https://creativecommons.org/licenses/by/4.0/", "", licenses.REVIEW),
    ("https://creativecommons.org.evil.example/licenses/by/4.0/", "", licenses.REVIEW),
    ("https://creativecommons.org/licenses/whatever/4.0/", "", licenses.REVIEW),
])
def test_license_from_url(url, name, status):
    assert licenses.name_from_url(url) == name
    assert licenses.evaluate(name)[0] == status


# --- Europeana (настоящий ответ API) ---

def europeana(cache, clock, *responses, key="ekey"):
    return EuropeanaSource(key, cache, session=FakeSession(*responses), now=clock)


def test_europeana_real_response(cache, clock):
    src = europeana(cache, clock, FakeResponse(text=fixture_text("europeana_search.json")))
    found = src.search("printing press", SearchFilters())
    first = found[0]
    assert first.title == "500 years of book printing in the Netherlands modern and old printing techniques"
    assert first.description.startswith("In the Centraal Museum in Utrecht")
    assert first.license.name == "CC BY-SA 4.0" and first.license.status == licenses.ATTRIBUTION
    assert first.files and first.files[0].url.startswith("https://www.openbeelden.nl/files/")
    assert first.files[0].url.endswith(".mp4")
    assert first.page_url.startswith("https://www.europeana.eu/item/")
    assert first.preview_url and first.author
    assert any(r.license.name == "Public Domain" and r.license.status == licenses.ALLOWED for r in found)


def test_europeana_request_and_key(cache, clock):
    src = europeana(cache, clock, FakeResponse(text='{"items": []}'))
    src.search("bookbinding", SearchFilters(), page=2)
    params = src.session.calls[0]["params"]
    assert params["wskey"] == "ekey" and params["qf"] == "TYPE:VIDEO" and params["reusability"] == "open"
    assert params["start"] == 21
    keys = [row[0] for row in cache._db.execute("SELECT key FROM responses")]
    assert keys and all("ekey" not in k for k in keys)
    assert EuropeanaSource.languages == {"en", "de", "fr"}  # без русского
    with pytest.raises(AuthError):
        europeana(cache, clock, key="").search("x", SearchFilters())


# --- Internet Archive (настоящий ответ API) ---

def archive(cache, clock, *responses):
    session = FakeSession(*responses)
    session.headers = {}
    return ArchiveSource(cache, session=session, now=clock)


@pytest.mark.parametrize("value, seconds", [
    ("00:00:53", 53), ("1:20:18", 4818), ("2:15", 135), ("53 seconds", 53), ("3 min", 180),
    (["00:01:00"], 60), (None, None), ("", None), ("неизвестно", None),
])
def test_parse_runtime(value, seconds):
    assert parse_runtime(value) == seconds


def test_archive_real_response(cache, clock):
    src = archive(cache, clock, FakeResponse(text=fixture_text("archive_search.json")))
    found = {r.source_id: r for r in src.search("printing press", SearchFilters())}
    assert not any(k.startswith("youtube-") for k in found)  # копии роликов с YouTube не показываются
    film = found["it-happens-every-thursday-1953-classic-comedy-loretta-young"]
    assert film.duration == 4818 and film.license.name == "Public Domain"
    assert film.page_url == "https://archive.org/details/it-happens-every-thursday-1953-classic-comedy-loretta-young"
    assert film.files == [] and NO_FILES_NOTE in film.license.note
    nc = found["Appjudgment_Snapp_23"]
    assert nc.license.status == licenses.REJECTED and nc.duration == 53
    assert ArchiveSource.topic_in_description is False


def test_archive_query(cache, clock):
    src = archive(cache, clock, FakeResponse(text='{"response": {"docs": []}}'))
    src.search("laminator", SearchFilters())
    params = src.session.calls[0]["params"]
    assert params["q"].startswith("(laminator) AND mediatype:movies")
    assert "NOT identifier:youtube-*" in params["q"]
    assert "licenseurl" in params["fl[]"]
