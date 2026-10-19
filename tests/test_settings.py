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


def test_built_in_keys_used_when_user_key_empty(data_dir, tmp_path, monkeypatch):
    import json
    from app import settings as settings_mod
    defaults = tmp_path / "defaults.json"
    defaults.write_text(json.dumps({"pixabay_api_key": "built-px", "vimeo_token": " built-vm ", "other": "x"}))
    monkeypatch.setattr(paths, "defaults_file", lambda: defaults)
    s = Settings(vimeo_token="own")
    assert s.key("pixabay_api_key") == "built-px"
    assert s.key("vimeo_token") == "own"  # свой ключ главнее встроенного
    assert s.key("coverr_api_key") == ""
    assert settings_mod.built_in_keys() == {"pixabay_api_key": "built-px", "vimeo_token": "built-vm"}
    s.save()
    assert "built-px" not in paths.settings_file().read_text()  # встроенные ключи не копируются в «Данные»


def test_built_in_keys_missing_or_broken(tmp_path, monkeypatch):
    from app.settings import built_in_keys
    assert built_in_keys(tmp_path / "nope.json") == {}
    (tmp_path / "bad.json").write_text("{")
    assert built_in_keys(tmp_path / "bad.json") == {}


def test_no_api_keys_in_repository():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"\b\d{6,}-[0-9a-f]{20,}\b|\b[0-9a-f]{32}\b")  # вид ключей Pixabay и токенов Vimeo
    for path in list(root.glob("app/**/*.py")) + list(root.glob("resources/*.json")) + [root / ".github/workflows/build.yml"]:
        assert not pattern.search(path.read_text(encoding="utf-8")), path
