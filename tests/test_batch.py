from PyQt6.QtCore import QCoreApplication

from app import catalog as cat
from app import licenses, paths, processes
from app.batch import BatchWorker
from app.query import Dictionary
from app.search import print_stems
from app.sources.base import LicenseInfo, SearchFilters, Source, VideoResult


def video(sid, tags):
    return VideoResult(source="Fake", source_id=sid, title=tags, description=tags, preview_url=None, duration=10,
                       width=1920, height=1080, has_audio=None, page_url="",
                       license=LicenseInfo("x", status=licenses.ALLOWED))


class Fake(Source):
    name = "Fake"

    def __init__(self):
        self.queries = []

    def search(self, query, filters, page=1, lang="en"):
        self.queries.append(query)
        if "laminat" in query:
            return [video("1", "laminator, office"), video("2", "laminating, document")]
        return []

    def get_details(self, source_id):
        raise NotImplementedError

    def get_license(self, result):
        return result.license


def test_batch_searches_services_in_priority_order(tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    procs = processes.all_processes(processes.load_catalog(paths.resources_dir() / "processes.json"))
    store = cat.Catalog(tmp_path / "c.sqlite")
    by_id = {p.id: p for p in procs}
    store.select(video("9", "x"), "lam_roll")  # у рулонной ламинации уже один ролик
    store.select(video("1", "laminator, office"), "lam_pouch")
    order = cat.batch_order([by_id["lam_roll"], by_id["lam_pouch"], by_id["lam_wide"]], store.coverage())
    assert [p.id for p in order] == ["lam_wide", "lam_roll", "lam_pouch"]  # сначала без роликов
    src = Fake()
    worker = BatchWorker(procs, order, [src], SearchFilters(), Dictionary.load(), store, print_stems(procs))
    started, done = [], []
    worker.started_service.connect(lambda pid, i, n: started.append((pid, i, n)))
    worker.done_service.connect(lambda pid, found, fresh: done.append((pid, found, fresh)))
    worker.run()
    assert [s[0] for s in started] == ["lam_wide", "lam_roll", "lam_pouch"]
    assert done[0] == ("lam_wide", 2, 1)  # ролик «1» уже в каталоге
    assert done[2] == ("lam_pouch", 2, 1)  # один из двух уже в каталоге
    assert store.has_history()
    store.close()
