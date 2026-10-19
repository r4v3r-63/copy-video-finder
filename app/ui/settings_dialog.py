"""Окно настроек: ключи API и параметры поиска."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app import paths
from app.settings import Settings, built_in_keys


def _key_field(value: str, built_in: bool = False) -> tuple[QWidget, QLineEdit]:
    edit = QLineEdit(value)
    if built_in:
        edit.setPlaceholderText("встроенный ключ — можно не заполнять")
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    toggle = QPushButton("Показать")
    toggle.setCheckable(True)
    toggle.toggled.connect(
        lambda on: (
            edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
            toggle.setText("Скрыть" if on else "Показать"),
        )
    )
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(edit)
    layout.addWidget(toggle)
    return row, edit


def _link(text: str, url: str) -> QLabel:
    label = QLabel(f'<a href="{url}">{text}</a>')
    label.setOpenExternalLinks(True)
    return label


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(480)
        self._settings = settings

        form = QFormLayout()
        built = built_in_keys()
        pexels_row, self.pexels_key = _key_field(settings.pexels_api_key, "pexels_api_key" in built)
        form.addRow("Ключ API Pexels:", pexels_row)
        form.addRow("", _link("Получить ключ Pexels", "https://www.pexels.com/api/"))
        pixabay_row, self.pixabay_key = _key_field(settings.pixabay_api_key, "pixabay_api_key" in built)
        form.addRow("Ключ API Pixabay:", pixabay_row)
        form.addRow("", _link("Получить ключ Pixabay", "https://pixabay.com/api/docs/"))
        coverr_row, self.coverr_key = _key_field(settings.coverr_api_key, "coverr_api_key" in built)
        form.addRow("Ключ API Coverr:", coverr_row)
        form.addRow("", _link("Получить ключ Coverr (бесплатно — 50 запросов в час)", "https://coverr.co/developers"))
        vimeo_row, self.vimeo_token = _key_field(settings.vimeo_token, "vimeo_token" in built)
        form.addRow("Токен Vimeo:", vimeo_row)
        form.addRow("", _link("Создать приложение и личный токен Vimeo (права public)",
                              "https://developer.vimeo.com/apps"))
        europeana_row, self.europeana_key = _key_field(settings.europeana_api_key, "europeana_api_key" in built)
        form.addRow("Ключ API Europeana:", europeana_row)
        form.addRow("", _link("Получить ключ Europeana (бесплатно)", "https://pro.europeana.eu/page/get-api"))
        form.addRow("", QLabel("Wikimedia Commons и Internet Archive работают без ключа."))

        self.min_duration = QSpinBox()
        self.min_duration.setRange(1, 600)
        self.min_duration.setSuffix(" с")
        self.min_duration.setValue(settings.min_duration)
        self.max_duration = QSpinBox()
        self.max_duration.setRange(1, 600)
        self.max_duration.setSuffix(" с")
        self.max_duration.setValue(settings.max_duration)
        duration = QWidget()
        duration_layout = QHBoxLayout(duration)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.addWidget(QLabel("от"))
        duration_layout.addWidget(self.min_duration)
        duration_layout.addWidget(QLabel("до"))
        duration_layout.addWidget(self.max_duration)
        duration_layout.addStretch()
        form.addRow("Длительность ролика:", duration)

        self.no_attribution = QCheckBox("Только ролики без атрибуции (без титра с автором)")
        self.no_attribution.setChecked(settings.only_without_attribution)
        form.addRow("", self.no_attribution)

        self.download_dir = QLineEdit(settings.download_dir)
        self.download_dir.setPlaceholderText(str(paths.default_download_dir()))
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._browse)
        folder = QWidget()
        folder_layout = QHBoxLayout(folder)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.download_dir, 1)
        folder_layout.addWidget(browse)
        form.addRow("Папка для роликов:", folder)
        self.allow_4k = QCheckBox("Скачивать 4K, если есть (по умолчанию — максимум Full HD)")
        self.allow_4k.setChecked(settings.allow_4k)
        form.addRow("", self.allow_4k)
        self.convert = QCheckBox("Конвертировать WebM/OGV в MP4 (нужен FFmpeg в папке программы)")
        self.convert.setChecked(settings.convert_to_mp4)
        form.addRow("", self.convert)
        self.check_updates = QCheckBox("Проверять обновления при запуске")
        self.check_updates.setChecked(settings.check_updates)
        form.addRow("", self.check_updates)
        self.link_check_days = QSpinBox()
        self.link_check_days.setRange(0, 365)
        self.link_check_days.setSuffix(" дн.")
        self.link_check_days.setSpecialValueText("не проверять")
        self.link_check_days.setValue(settings.link_check_days)
        form.addRow("Проверять ссылки каталога раз в:", self.link_check_days)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self):
        start = self.download_dir.text() or str(paths.default_download_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Папка для роликов", start)
        if chosen:
            self.download_dir.setText(chosen)

    def accept(self):
        low, high = self.min_duration.value(), self.max_duration.value()
        self._settings.pexels_api_key = self.pexels_key.text().strip()
        self._settings.pixabay_api_key = self.pixabay_key.text().strip()
        self._settings.coverr_api_key = self.coverr_key.text().strip()
        self._settings.vimeo_token = self.vimeo_token.text().strip()
        self._settings.europeana_api_key = self.europeana_key.text().strip()
        self._settings.min_duration = min(low, high)
        self._settings.max_duration = max(low, high)
        self._settings.only_without_attribution = self.no_attribution.isChecked()
        self._settings.download_dir = self.download_dir.text().strip()
        self._settings.allow_4k = self.allow_4k.isChecked()
        self._settings.convert_to_mp4 = self.convert.isChecked()
        self._settings.check_updates = self.check_updates.isChecked()
        self._settings.link_check_days = self.link_check_days.value()
        self._settings.save()
        super().accept()
