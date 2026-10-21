"""Диалог «Шоурил для экрана»: что собрать, сколько секунд от ролика, переходы."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from app.export import showreel
from app.storage import catalog as cat
from app.ui import theme


class ShowreelDialog(QDialog):
    def __init__(self, store: cat.Catalog, categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Шоурил для экрана")
        self.store = store
        ready = [e for e in store.entries() if showreel.suitable(e)]
        count = {}
        for e in ready:
            for pid in e.links:
                count[pid] = count.get(pid, 0) + 1

        self.scope = QComboBox()
        all_ids = [p.id for c in categories for p in c.processes]
        self.scope.addItem(f"Все услуги ({len(ready)})", ("Все услуги", all_ids))
        for c in categories:
            ids = [p.id for p in c.processes]
            n = len({e.id for e in ready if any(pid in ids for pid in e.links)})
            if not n:
                continue
            self.scope.addItem(f"{c.name} ({n})", (c.name, ids))
            for p in c.processes:
                if count.get(p.id):
                    self.scope.addItem(f"    {p.name} ({count[p.id]})", (p.name, [p.id]))
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(2, 60)
        self.seconds.setDecimals(0)
        self.seconds.setValue(showreel.CLIP_SECONDS)
        self.seconds.setSuffix(" с")
        self.fade = QCheckBox("Плавные переходы через затемнение")
        self.fade.setChecked(True)
        self.length = QLabel()
        self.length.setProperty("muted", True)

        form = QFormLayout()
        form.addRow("Что собрать:", self.scope)
        form.addRow("От каждого ролика:", self.seconds)
        form.addRow("", self.fade)
        hint = QLabel("Берутся скачанные ролики со свободной лицензией, подтверждённой в каталоге. "
                      "Звук убирается, кадр — 1920×1080. Рядом с роликом сохраняется список авторов и лицензий.")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Собрать…")
        theme.set_role(self.ok, theme.PRIMARY)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.length)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.scope.currentIndexChanged.connect(self._update)
        self.seconds.valueChanged.connect(self._update)
        self._update()
        self.resize(520, self.sizeHint().height())

    def _update(self):
        entries = self.entries()
        self.ok.setEnabled(bool(entries))
        if not entries:
            self.length.setText("Нет скачанных роликов с подтверждённой свободной лицензией.")
            return
        total = sum(min(self.seconds.value(), e.result.duration or self.seconds.value()) for e in entries)
        self.length.setText(f"Роликов: {len(entries)}, длительность около {int(total) // 60}:{int(total) % 60:02d}.")

    def title(self) -> str:
        data = self.scope.currentData()
        return data[0] if data else ""

    def entries(self) -> list[cat.Entry]:
        data = self.scope.currentData()
        return showreel.showreel_entries(self.store, data[1]) if data else []

    def clip_seconds(self) -> float:
        return self.seconds.value()

    def fade_seconds(self) -> float:
        return showreel.FADE if self.fade.isChecked() else 0.0
