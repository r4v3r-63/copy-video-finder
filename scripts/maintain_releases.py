"""Описания выпусков и хранение трёх последних версий без удаления тегов."""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
REPOSITORY = "r4v3r-63/copy-video-finder"


def version(tag):
    match = VERSION.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def release_body(tag, root):
    if version(tag) is None:
        raise ValueError("Некорректный тег выпуска.")
    notes = (root / "docs" / "release-notes" / f"{tag[1:]}.md").read_text(encoding="utf-8")
    notes = notes.removeprefix(f"# {tag[1:]}").strip()
    base = f"https://github.com/{REPOSITORY}"
    archive = f"CopyVideoFinder-{tag}-windows.zip"
    return (
        f"**[Скачать для Windows]({base}/releases/download/{tag}/{archive})** · "
        f"[SHA-256]({base}/releases/download/{tag}/{archive}.sha256)\n\n"
        f"{notes}\n\n"
        "Распакуйте ZIP и запустите `CopyVideoFinder.exe`. Установка и права администратора не нужны. "
        "Для сохранения настроек и каталога перенесите папку `Данные` из прежней версии "
        "при закрытой программе.\n\n"
        f"[Руководство пользователя]({base}/blob/main/docs/USER_GUIDE.md)\n"
    )


def release_plan(releases, latest, keep):
    if type(keep) is not int or keep < 1:
        raise ValueError("Нужно сохранить хотя бы один выпуск.")
    stable = [item for item in releases if version(item["tag_name"]) is not None
              and not item["draft"] and not item["prerelease"]]
    stable.sort(key=lambda item: version(item["tag_name"]), reverse=True)
    if latest not in {item["tag_name"] for item in stable}:
        raise ValueError("Последний выпуск не распознан: удаление остановлено.")
    # Latest сохраняется и при вручную выбранной старой версии для отката.
    protected = {latest}
    for item in stable:
        if len(protected) >= keep:
            break
        protected.add(item["tag_name"])
    retained = [item for item in stable if item["tag_name"] in protected]
    for item in retained:
        archive = f'CopyVideoFinder-{item["tag_name"]}-windows.zip'
        names = {asset["name"] for asset in item["assets"]}
        if not {archive, archive + ".sha256"} <= names:
            raise ValueError("У сохраняемого выпуска нет ZIP или SHA-256: удаление остановлено.")
    # Неизвестные выпуски и чужие наборы файлов не удаляем.
    old = [item for item in stable if item["tag_name"] not in protected
           and item["assets"] and all(
               asset["name"] in {f'CopyVideoFinder-{item["tag_name"]}-windows.zip',
                                 f'CopyVideoFinder-{item["tag_name"]}-windows.zip.sha256'}
               for asset in item["assets"])]
    return retained, old


class GitHub:
    def __init__(self, repository, token):
        if repository != REPOSITORY or not token:
            raise ValueError("Нужен токен Actions этого репозитория.")
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token

    def request(self, method, path, payload=None):
        request = Request(self.base + path, method=method,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28",
                                   "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as exc:
            raise RuntimeError(f"GitHub API вернул HTTP {exc.code}; операция остановлена.") from None


def maintain(api, root, keep=3, dry_run=True):
    releases = []
    page = 1
    while True:
        batch = api.request("GET", f"/releases?per_page=100&page={page}")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    latest = api.request("GET", "/releases/latest")["tag_name"]
    retained, old = release_plan(releases, latest, keep)
    print("Остаются:", ", ".join(item["tag_name"] for item in retained))
    print("Старые выпуски:", ", ".join(item["tag_name"] for item in old) or "нет")
    if dry_run:
        print("Проверка: изменения не выполняются.")
        return
    for item in retained:
        notes = root / "docs" / "release-notes" / f'{item["tag_name"][1:]}.md'
        if notes.is_file():
            body = release_body(item["tag_name"], root)
            if item.get("body") != body:
                api.request("PATCH", f'/releases/{item["id"]}', {"body": body})
                print("Обновлено описание:", item["tag_name"])
    for item in old:
        # Повторная проверка защищает от параллельного переключения Latest.
        if api.request("GET", "/releases/latest")["tag_name"] != latest:
            raise RuntimeError("Последний выпуск изменился; удаление остановлено.")
        api.request("DELETE", f'/releases/{item["id"]}')
        print("Удалён старый выпуск, тег сохранён:", item["tag_name"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--notes-for")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.notes_for:
        if args.output is None:
            parser.error("Для примечаний нужен --output.")
        args.output.write_text(release_body(args.notes_for, root), encoding="utf-8")
    else:
        api = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
        maintain(api, root, args.keep, args.dry_run)


if __name__ == "__main__":
    main()
