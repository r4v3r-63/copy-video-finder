import pytest


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Каждый тест работает со своей временной папкой «Данные»."""
    path = tmp_path / "Данные"
    monkeypatch.setenv("COPYVF_DATA_DIR", str(path))
    return path
