"""План очистки и защита выпусков без обращений к GitHub."""

from copy import deepcopy

import pytest

from scripts.maintain_releases import maintain, release_body, release_plan
from tests.fakes import ROOT


def release(number, **changes):
    tag = f"v0.26.{number}"
    archive = f"CopyVideoFinder-{tag}-windows.zip"
    return {"id": number, "tag_name": tag, "draft": False, "prerelease": False,
            "assets": [{"name": archive}, {"name": archive + ".sha256"}], **changes}


def test_keep_three_and_preserve_unknown_and_unpublished_releases():
    items = [release(i) for i in (1, 10, 3, 2)]
    items += [release(20, draft=True), release(21, prerelease=True),
              release(22, tag_name="preview"), release(0, assets=[{"name": "other.zip"}])]
    retained, old = release_plan(items, "v0.26.10", 3)
    assert [item["id"] for item in retained] == [10, 3, 2]
    assert [item["id"] for item in old] == [1]


@pytest.mark.parametrize("keep", [0, -1, True, 1.5])
def test_invalid_keep_cannot_delete(keep):
    with pytest.raises(ValueError):
        release_plan([release(1)], "v0.26.1", keep)


def test_latest_is_preserved_even_when_it_is_an_older_rollback():
    retained, old = release_plan([release(i) for i in range(1, 6)], "v0.26.1", 3)
    assert [item["id"] for item in retained] == [5, 4, 1]
    assert [item["id"] for item in old] == [3, 2]


def test_no_deletion_when_latest_unknown_or_archive_incomplete():
    with pytest.raises(ValueError, match="не распознан"):
        release_plan([release(1)], "v0.26.2", 3)
    with pytest.raises(ValueError, match="нет ZIP"):
        release_plan([release(1), release(2, assets=[])], "v0.26.2", 3)


class FakeGitHub:
    def __init__(self, items, latest="v0.26.3", switch_latest=False):
        self.items = items
        self.latest = latest
        self.switch_latest = switch_latest
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/releases/latest":
            previous = sum(call[1] == path for call in self.calls)
            return {"tag_name": "v0.26.4" if self.switch_latest and previous > 1 else self.latest}
        if method == "GET":
            page = int(path.rsplit("=", 1)[1])
            return deepcopy(self.items[(page - 1) * 100:page * 100])


def test_dry_run_has_no_writes():
    api = FakeGitHub([release(i) for i in range(4)])
    maintain(api, ROOT, 3, dry_run=True)
    assert all(method == "GET" for method, *_ in api.calls)


def test_cleanup_updates_retained_notes_deletes_only_old_release_and_never_tags():
    api = FakeGitHub([release(i) for i in range(4)])
    maintain(api, ROOT, 3, dry_run=False)
    assert [(method, path) for method, path, _ in api.calls if method == "DELETE"] == [("DELETE", "/releases/0")]
    patches = [body for method, _, body in api.calls if method == "PATCH"]
    assert len(patches) == 3 and all("Скачать для Windows" in body["body"] for body in patches)
    assert not any("refs" in path or "tags" in path for _, path, _ in api.calls)


def test_pagination_does_not_miss_more_than_100_releases():
    api = FakeGitHub([release(i) for i in range(102)], latest="v0.26.101")
    maintain(api, ROOT, 3, dry_run=True)
    assert [path for _, path, _ in api.calls if "per_page" in path] == [
        "/releases?per_page=100&page=1", "/releases?per_page=100&page=2"]


def test_change_of_latest_aborts_before_delete():
    api = FakeGitHub([release(i) for i in range(4)], switch_latest=True)
    with pytest.raises(RuntimeError, match="изменился"):
        maintain(api, ROOT, 3, dry_run=False)
    assert not any(method == "DELETE" for method, *_ in api.calls)


def test_release_body_uses_real_notes_and_unchanged_asset_names():
    body = release_body("v0.26.3", ROOT)
    assert "Аниме" in body and "CopyVideoFinder-v0.26.3-windows.zip.sha256" in body
    assert "Данные" in body and "USER_GUIDE.md" in body
    with pytest.raises(ValueError):
        release_body("../../private", ROOT)
