import hashlib
import json
import zipfile

import pytest

from app import paths
from app.services import updater
from tests.fakes import FakeResponse, FakeSession, StreamResponse, fixture_text


def release_data(tag="v0.5.0", sha=True, **extra):
    data = json.loads(fixture_text("github_release.json"))
    data["tag_name"] = tag
    name = f"CopyVideoFinder-{tag}-windows.zip"
    data["assets"] = [{"name": name, "browser_download_url": f"https://github.com/x/{name}"}]
    if sha:
        data["assets"].append({"name": name + ".sha256", "browser_download_url": f"https://github.com/x/{name}.sha256"})
    data.update(extra)
    return data


def test_versions():
    assert updater.parse_version("v0.4.1") == (0, 4, 1)
    assert updater.is_newer("0.4.10", "0.4.9")
    assert not updater.is_newer("v0.4.0", "0.4.0")
    assert not updater.is_newer("v0.3.9", "0.4.0")
    assert not updater.is_newer("latest", "0.4.0")


def test_parse_real_release_fixture():
    r = updater.parse_release(json.loads(fixture_text("github_release.json")))
    assert r.version == "0.4.0"
    assert r.zip_name == "CopyVideoFinder-v0.4.0-windows.zip"
    assert r.zip_url.startswith("https://github.com/r4v3r-63/copy-video-finder/releases/download/v0.4.0/")
    assert r.sha256_url is None  # в 0.4.0 контрольной суммы ещё не было


def test_parse_skips_drafts_prereleases_and_missing_zip():
    assert updater.parse_release(release_data(draft=True)) is None
    assert updater.parse_release(release_data(prerelease=True)) is None
    assert updater.parse_release({**release_data(), "assets": []}) is None
    assert updater.parse_release(release_data()).sha256_url.endswith(".sha256")


def test_check_latest():
    session = FakeSession(FakeResponse(text=json.dumps(release_data("v9.0.0"))))
    assert updater.check_latest(session, current="0.4.1").version == "9.0.0"
    assert session.calls[0]["url"] == updater.LATEST_URL
    assert updater.check_latest(FakeSession(FakeResponse(text=json.dumps(release_data("v0.4.1")))), "0.4.1") is None
    assert updater.check_latest(FakeSession(FakeResponse(404)), "0.4.1") is None


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


class Session:
    """Скачивание ZIP (поток) и текст контрольной суммы."""

    def __init__(self, payload: bytes, sha_text: str):
        self.payload, self.sha_text = payload, sha_text

    def get(self, url, headers=None, stream=False, timeout=None):
        if url.endswith(".sha256"):
            return FakeResponse(text=self.sha_text)
        return StreamResponse(self.payload)


def test_download_verifies_checksum(data_dir, tmp_path):
    payload = make_zip(tmp_path / "a.zip", {"CopyVideoFinder/CopyVideoFinder.exe": "exe"}).read_bytes()
    release = updater.parse_release(release_data())
    good = hashlib.sha256(payload).hexdigest()
    path = updater.download(release, Session(payload, f"{good}  file.zip\n"))
    assert path.read_bytes() == payload and path.parent == paths.data_dir() / "update"
    with pytest.raises(updater.UpdateError, match="не совпала"):
        updater.download(release, Session(payload, "0" * 64))
    assert not path.exists()
    with pytest.raises(updater.UpdateError, match="контрольную сумму"):
        updater.download(release, Session(payload, "<html>"))


def test_extract_finds_program_folder(tmp_path):
    z = make_zip(tmp_path / "u.zip", {"CopyVideoFinder/CopyVideoFinder.exe": "exe",
                                      "CopyVideoFinder/_internal/base.dll": "dll"})
    folder = updater.extract(z, tmp_path / "new")
    assert folder == tmp_path / "new" / "CopyVideoFinder"
    assert (folder / "_internal" / "base.dll").read_text() == "dll"


@pytest.mark.parametrize("files, message", [
    ({"../evil.exe": "x", "CopyVideoFinder/CopyVideoFinder.exe": "exe"}, "недопустимые пути"),
    ({"CopyVideoFinder/readme.txt": "x"}, "нет CopyVideoFinder.exe"),
    ({"CopyVideoFinder/CopyVideoFinder.exe": "exe", "CopyVideoFinder/Данные/settings.json": "{}"}, "Данные"),
])
def test_extract_rejects_bad_archives(tmp_path, files, message):
    with pytest.raises(updater.UpdateError, match=message):
        updater.extract(make_zip(tmp_path / "u.zip", files), tmp_path / "new")


def test_extract_rejects_broken_zip(tmp_path):
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    with pytest.raises(updater.UpdateError, match="повреждён"):
        updater.extract(tmp_path / "bad.zip", tmp_path / "new")


def test_update_script(data_dir, tmp_path):
    script = updater.write_script(tmp_path / "new 100%", tmp_path / "Программа", 4242, tmp_path / "update.log")
    raw = script.read_bytes()
    assert b"\r\n" in raw and not raw.startswith(b"\xef\xbb\xbf")  # CRLF, UTF-8 без BOM
    text = raw.decode("utf-8")
    assert 'set "PID=4242"' in text
    assert "new 100%%" in text  # % в пути экранирован
    assert str(tmp_path / "Программа") in text
    assert 'robocopy "%NEW%" "%APP%" /E' in text
    assert "/MIR" not in text and "/PURGE" not in text  # ничего не удаляется в папке программы
    assert 'start "" "%APP%\\CopyVideoFinder.exe"' in text


def test_no_self_update_outside_windows_build():
    assert not updater.can_self_update()
    with pytest.raises(updater.UpdateError, match="только в собранной программе"):
        updater.prepare_and_launch(paths.data_dir() / "x.zip")


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="командный файл работает только в Windows")
def test_update_script_on_windows(data_dir, tmp_path):
    import shutil
    import subprocess
    import time
    tiny_exe = r"C:\Windows\System32\hostname.exe"  # запускается и сразу завершается
    app = tmp_path / "Программа"
    (app / "_internal").mkdir(parents=True)
    shutil.copy(tiny_exe, app / "CopyVideoFinder.exe")
    (app / "_internal" / "lib.dll").write_text("old")
    (app / "Данные").mkdir()
    (app / "Данные" / "settings.json").write_text("keep")
    new = tmp_path / "update" / "CopyVideoFinder"
    (new / "_internal").mkdir(parents=True)
    shutil.copy(tiny_exe, new / "CopyVideoFinder.exe")
    (new / "_internal" / "lib.dll").write_text("new")
    (new / "_internal" / "added.txt").write_text("added")

    # Программа «ещё работает» 3 секунды — файл должен дождаться её выхода.
    running = subprocess.Popen(["ping", "-n", "4", "127.0.0.1"], stdout=subprocess.DEVNULL)
    log = tmp_path / "update.log"
    script = updater.write_script(new, app, running.pid, log)
    started = time.monotonic()
    subprocess.run(["cmd.exe", "/c", str(script)], check=True, timeout=120)
    running.wait()
    assert time.monotonic() - started >= 2

    assert (app / "_internal" / "lib.dll").read_text() == "new"
    assert (app / "_internal" / "added.txt").read_text() == "added"
    assert (app / "Данные" / "settings.json").read_text() == "keep"
    assert "Готово" in log.read_text(encoding="utf-8", errors="replace")
