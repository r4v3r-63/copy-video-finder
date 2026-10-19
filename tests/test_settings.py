from app import paths
from app.settings import Settings


def test_defaults_when_no_file(data_dir):
    s = Settings.load()
    assert s.pexels_api_key == ""
    assert (s.min_duration, s.max_duration) == (5, 45)


def test_save_and_load_in_data_dir(data_dir):
    Settings(pexels_api_key="abc", max_duration=30).save()
    assert paths.settings_file().parent == data_dir
    s = Settings.load()
    assert s.pexels_api_key == "abc"
    assert s.max_duration == 30


def test_broken_file_gives_defaults(data_dir):
    paths.settings_file().write_text("{не json", encoding="utf-8")
    assert Settings.load() == Settings()


def test_unknown_keys_ignored(data_dir):
    paths.settings_file().write_text('{"pixabay_api_key": "k", "old": 1}', encoding="utf-8")
    assert Settings.load().pixabay_api_key == "k"
