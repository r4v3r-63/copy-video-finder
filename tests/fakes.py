"""Подставные объекты вместо сети."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


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
