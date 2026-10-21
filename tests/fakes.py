"""Подставные объекты вместо сети."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # корень репозитория
FIXTURES = ROOT / "tests" / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, status_code=200, text="{}", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Записывает запросы и отдаёт заранее заданные ответы по очереди (последний повторяется)."""

    def __init__(self, *responses):
        self.responses = list(responses) or [FakeResponse()]
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class StreamResponse:
    """Ответ со стримингом для скачивания; cut_after — оборвать после N байт."""

    def __init__(self, data: bytes, status_code=200, total=None, cut_after=None, headers=None):
        self.data = data
        self.status_code = status_code
        self.headers = {"Content-Length": str(total if total is not None else len(data)), **(headers or {})}
        self.cut_after = cut_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        import requests
        sent = 0
        for i in range(0, len(self.data), 4):
            if self.cut_after is not None and sent >= self.cut_after:
                raise requests.ConnectionError("обрыв")
            chunk = self.data[i:i + 4]
            sent += len(chunk)
            yield chunk


class StreamSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, stream=False, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {})})
        return self.responses.pop(0)
