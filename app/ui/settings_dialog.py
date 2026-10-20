"""Окно настроек: ключи API и параметры поиска."""

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app import paths
from app.settings import Settings, built_in_keys
from app.sources import vk
from app.ui import theme


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




VK_HELP = "https://github.com/r4v3r-63/copy-video-finder/blob/main/docs/vk.md"


def _link(text: str, url: str) -> QLabel:
    label = QLabel(f'<a href="{url}">{text}</a>')
    label.setOpenExternalLinks(True)
    label.setWordWrap(True)
    return label


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self._settings = settings

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)  # узко — подпись над полем
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
        tavily_row, self.tavily_key = _key_field(settings.tavily_api_key, "tavily_api_key" in built)
        form.addRow("Ключ Tavily:", tavily_row)
        form.addRow("", _link("Поиск по интернету надёжнее с ключом Tavily: бесплатно, без карты, 1000 запросов "
                              "в месяц (программа тратит не больше 900). Без ключа — DuckDuckGo, "
                              "он иногда ограничивает частые запросы",
                              "https://app.tavily.com"))
        brave_row, self.brave_key = _key_field(settings.brave_api_key, "brave_api_key" in built)
        form.addRow("Ключ Brave Search:", brave_row)
        form.addRow("", _note("Brave — платный, необязательно (программа не выходит за бесплатные $5 в месяц)."))
        form.addRow("", _note("Wikimedia Commons, Internet Archive и Videezy работают без ключа."))

        # VK Видео: вход в браузере, пароль программа не видит; в программу — только токен из адреса.
        self.vk_app_id = QLineEdit(settings.vk_app_id)
        self.vk_app_id.setPlaceholderText("ID приложения с id.vk.com")
        self.vk_login = QPushButton("Войти в VK")
        self.vk_login.clicked.connect(self._vk_open_login)
        vk_row = QHBoxLayout()
        vk_row.addWidget(self.vk_app_id, 1)
        vk_row.addWidget(self.vk_login)
        form.addRow("VK Видео:", vk_row)
        self.vk_answer = QLineEdit()
        self.vk_answer.setEchoMode(QLineEdit.EchoMode.Password)
        self.vk_answer.setPlaceholderText("после входа вставьте сюда адрес страницы из браузера")
        self.vk_answer.textChanged.connect(lambda text: self.vk_status.setText(
            "Вход получен — нажмите «Сохранить»." if vk.has_code(text) or vk.token_from_text(text)
            else "В адресе нет кода входа — скопируйте адрес страницы целиком."))
        self.vk_logout = QPushButton("Выйти")
        self.vk_logout.clicked.connect(self._vk_forget)
        answer_row = QHBoxLayout()
        answer_row.addWidget(self.vk_answer, 1)
        answer_row.addWidget(self.vk_logout)
        form.addRow("", answer_row)
        self.vk_token = settings.vk_token
        self.vk_tokens = vk.Tokens(settings.vk_token, settings.vk_refresh_token, settings.vk_device_id,
                                   settings.vk_token_expires)
        self.vk_pending: vk.Login | None = None  # вход начат кнопкой «Войти в VK»
        self.vk_status = _note("")
        form.addRow("", self.vk_status)
        form.addRow("", _link("Как создать приложение VK и войти (инструкция)", VK_HELP))
        self._vk_update()

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
        self.trim = QSpinBox()
        self.trim.setRange(0, 600)
        self.trim.setSuffix(" с")
        self.trim.setSpecialValueText("не обрезать")
        self.trim.setValue(settings.trim_seconds)
        self.trim.setToolTip("Скачанный ролик сохраняется только с первыми N секундами — для экранов в копицентре.")
        self.mute = QCheckBox("Убирать звук из скачанных роликов")
        self.mute.setChecked(settings.mute_audio)
        prepare = QHBoxLayout()
        prepare.addWidget(QLabel("обрезать до"))
        prepare.addWidget(self.trim)
        prepare.addWidget(self.mute)
        prepare.addStretch()
        form.addRow("Подготовка к показу:", prepare)
        self.check_updates = QCheckBox("Проверять обновления при запуске")
        self.check_updates.setChecked(settings.check_updates)
        form.addRow("", self.check_updates)
        self.link_check_days = QSpinBox()
        self.link_check_days.setRange(0, 365)
        self.link_check_days.setSuffix(" дн.")
        self.link_check_days.setSpecialValueText("не проверять")
        self.link_check_days.setValue(settings.link_check_days)
        form.addRow("Проверять ссылки каталога раз в:", self.link_check_days)
        self.theme = QComboBox()
        for value, name in theme.THEME_NAMES.items():
            self.theme.addItem(name, value)
        self.theme.setCurrentIndex(max(self.theme.findData(settings.theme), 0))
        form.addRow("Оформление:", self.theme)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # Форма длинная: на экране 1366×768 она прокручивается, а кнопки всегда видны внизу.
        body = QWidget()
        body.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        # Длинные подписи переносятся; ширина и высота — не больше экрана (на узком — прокрутка).
        screen = self.screen().availableGeometry() if self.screen() else None
        scrollbar = scroll.verticalScrollBar().sizeHint().width()
        if screen and body.minimumSizeHint().width() + scrollbar + 24 > screen.width() - 40:
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)  # узкий экран — подписи над полями
            body.adjustSize()
        width = max(body.sizeHint().width() + scrollbar + 24, 640)
        height = body.heightForWidth(width - 40) if body.hasHeightForWidth() else body.sizeHint().height()
        height += buttons.sizeHint().height() + 48
        if screen:
            width, height = min(width, screen.width() - 40), min(height, screen.height() - 80)
        self.resize(width, height)

    def _vk_open_login(self):
        if not self.vk_app_id.text().strip():
            self.vk_status.setText("Сначала впишите ID приложения VK.")
            return
        self.vk_pending = vk.Login.start(self.vk_app_id.text())
        QDesktopServices.openUrl(QUrl(self.vk_pending.url))
        self.vk_status.setText("Войдите в VK в открывшемся браузере, затем скопируйте адрес страницы "
                               "(начинается с https://oauth.vk.com/blank.html?code=…) и вставьте его выше. "
                               "Окно настроек до этого не закрывайте.")

    def _vk_forget(self):
        self.vk_token = ""
        self.vk_tokens = vk.Tokens("")
        self.vk_answer.clear()
        self._vk_update()

    def _vk_update(self):
        self.vk_logout.setEnabled(bool(self.vk_token))
        self.vk_status.setText("Вход в VK выполнен — VK Видео участвует в поиске." if self.vk_token
                               else "Без входа ролики VK приходят только через Яндекс Видео.")

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
        self._settings.brave_api_key = self.brave_key.text().strip()
        self._settings.tavily_api_key = self.tavily_key.text().strip()
        self._settings.vk_app_id = self.vk_app_id.text().strip()
        pasted = self.vk_answer.text()
        if vk.has_code(pasted):
            if not self.vk_pending:
                self.vk_status.setText("Этот адрес от прошлого входа — нажмите «Войти в VK» ещё раз.")
                return
            try:
                self.vk_tokens = vk.exchange(self.vk_pending, pasted)
            except vk.VkLoginError as exc:
                self.vk_status.setText(f"VK не принял вход: {exc}")
                return  # окно остаётся открытым — можно войти ещё раз
            self.vk_token = self.vk_tokens.access
        elif vk.token_from_text(pasted):
            self.vk_token = vk.token_from_text(pasted)
            self.vk_tokens = vk.Tokens(self.vk_token)
        self._settings.vk_token = self.vk_token
        self._settings.vk_refresh_token = self.vk_tokens.refresh
        self._settings.vk_device_id = self.vk_tokens.device_id
        self._settings.vk_token_expires = self.vk_tokens.expires_at
        self._settings.min_duration = min(low, high)
        self._settings.max_duration = max(low, high)
        self._settings.only_without_attribution = self.no_attribution.isChecked()
        self._settings.download_dir = self.download_dir.text().strip()
        self._settings.allow_4k = self.allow_4k.isChecked()
        self._settings.convert_to_mp4 = self.convert.isChecked()
        self._settings.trim_seconds = self.trim.value()
        self._settings.mute_audio = self.mute.isChecked()
        self._settings.check_updates = self.check_updates.isChecked()
        self._settings.link_check_days = self.link_check_days.value()
        self._settings.theme = self.theme.currentData()
        self._settings.save()
        super().accept()
