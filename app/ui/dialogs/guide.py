"""Встроенное руководство, доступное без интернета в исходниках и Windows-поставке."""

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

from app import paths
from app.core import design


class GuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Руководство пользователя")
        self.resize(design.GUIDE_WIDTH, design.GUIDE_HEIGHT)
        guide = paths.resources_dir().parent / "docs" / "USER_GUIDE.md"
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setSearchPaths([str(guide.parent)])
        try:
            self.browser.setMarkdown(guide.read_text(encoding="utf-8"))
        except OSError:
            self.browser.setPlainText("Не удалось открыть руководство. Повторно распакуйте полную папку программы.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.browser)
        layout.addWidget(buttons)
