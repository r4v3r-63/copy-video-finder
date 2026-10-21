"""Оформление программы по дизайн-системе Copy.ru (токены — в `app/design.py`).

Виджеты не задают цвета сами: кнопкам назначается роль (`set_role`), панелям — роль
карточки (`make_card`), всё остальное оформляется общим стилем `stylesheet()`.
Состояния hover, focus, pressed и disabled описаны в стиле для каждого элемента.
"""

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QWidget

from app import paths
from app.core import design, licenses

# Токены, которые виджеты берут как theme.PRIMARY, theme.SPACE_L и т. п., — тоже отсюда.
from app.core.design import (  # noqa: F401 — часть импортов только для виджетов
    CONTROL_HEIGHT,
    CONTROL_HEIGHT_LARGE,
    DARK,
    FONT_BODY,
    FONT_CAPTION,
    FONT_FALLBACK,
    FONT_FAMILY,
    FONT_FILES,
    FONT_SUBTITLE,
    FONT_TITLE,
    LIGHT,
    PALETTES,
    PRIMARY,
    RADIUS_BUTTON,
    RADIUS_CARD,
    RADIUS_INPUT,
    SECONDARY,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XS,
    SYSTEM,
    TERTIARY,
    THEME_NAMES,
    Palette,
)

_current = LIGHT


def resolve(choice: str) -> str:
    """Тема для выбора пользователя: «как в системе» — по настройке Windows."""
    if choice in (LIGHT, DARK):
        return choice
    hints = QGuiApplication.styleHints() if QGuiApplication.instance() else None
    if hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark:
        return DARK
    return LIGHT


def palette(name: str | None = None) -> Palette:
    return PALETTES[name or _current]


def coverage_color(level: int) -> QColor:
    return QColor(palette().coverage[level])


def load_fonts() -> str:
    """Подключить Inter из папки программы; вернуть имя семейства (или системный шрифт)."""
    family = None
    for name in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(paths.resources_dir() / "fonts" / name))
        if font_id >= 0 and not family:
            families = QFontDatabase.applicationFontFamilies(font_id)
            family = families[0] if families else None
    return family or ""


def compact_combo(combo: QComboBox, chars: int = 16) -> QComboBox:
    """Список, ширина которого не зависит от самого длинного пункта (названия услуг, качества).

    Иначе длинный пункт растягивает панель, а через неё — минимальную ширину всего окна.
    """
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    return combo


def set_role(button: QWidget, role: str) -> QWidget:
    """Роль кнопки: primary — главное действие, secondary — второстепенное, tertiary — прочие."""
    button.setProperty("role", role)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def make_card(widget: QWidget) -> QWidget:
    """Оформить панель как карточку: фон, тонкая рамка, скругление."""
    widget.setObjectName("card")
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


def license_label(license_info) -> tuple[str, int]:
    """Подпись статуса для компактной метки; оценка лицензии остаётся прежней."""
    status = license_info.status if license_info else licenses.REVIEW
    if status == licenses.ALLOWED:
        return "Разрешено", 0
    if status == licenses.ATTRIBUTION:
        return "С атрибуцией", 1
    if status == licenses.REVIEW:
        return "Проверить", 1
    return "Нет свободной", 2


class LicenseBadge(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("licenseBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_license(self, license_info):
        text, tone = license_label(license_info)
        self.setText(text)
        self.setProperty("tone", str(tone))
        self.setToolTip(license_info.status if license_info else licenses.REVIEW)
        self.style().unpolish(self)
        self.style().polish(self)


_ICON_PATHS = {
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    "filters": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/>'
               '<circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    "folder": '<path d="M3 6h7l2 3h9v12H3z"/>',
    "external": '<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',
    "play": '<path d="m8 4 12 8-12 8z"/>',
    "pause": '<path d="M8 4v16M16 4v16"/>',
    "link": '<path d="m9 15 6-6m-8 3-2 2a4 4 0 0 0 6 6l2-2m-2-12 2-2a4 4 0 0 1 6 6l-2 2"/>',
}


def icon(name: str, inverse: bool = False) -> QIcon:
    """Единый набор контурных значков, цвет берётся из текущей темы."""
    color = palette().text_inverse if inverse else palette().text_secondary
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="{design.ICON_STROKE}" stroke-linecap="round" '
           f'stroke-linejoin="round">{_ICON_PATHS[name]}</svg>')
    pixmap = QPixmap(design.ICON_SIZE * 2, design.ICON_SIZE * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def set_icon(button, name: str):
    button.setProperty("icon_name", name)
    button.setIcon(icon(name, inverse=button.property("role") == PRIMARY or bool(button.property("icon_inverse"))))
    button.setIconSize(QSize(design.ICON_SIZE, design.ICON_SIZE))


def stylesheet(p: Palette) -> str:
    """Стиль Qt из токенов. Состояния: hover, focus, pressed, disabled."""
    return f"""
* {{ font-family: "{FONT_FAMILY}", {FONT_FALLBACK}; font-size: {FONT_BODY}px; }}
QMainWindow, QDialog {{ background: {p.bg_secondary}; }}
QWidget {{ color: {p.text_primary}; }}
QWidget#card {{ background: {p.bg_surface}; border: 1px solid {p.border}; border-radius: {RADIUS_CARD}px; }}
QWidget#card QLabel, QWidget#card QCheckBox {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {p.text_secondary}; font-size: {FONT_CAPTION}px; }}
QLabel[heading="true"] {{ font-size: {FONT_SUBTITLE}px; font-weight: 600; }}
QLabel[title="true"] {{ font-size: {FONT_TITLE}px; font-weight: 600; }}
QLabel#licenseBadge {{ border-radius: {SPACE_XS}px; padding: {SPACE_XS}px {SPACE_S}px;
                      font-size: {FONT_CAPTION}px; font-weight: 600; }}
QLabel#licenseBadge[tone="0"] {{ background: {p.badge_bg[0]}; color: {p.badge_text[0]}; }}
QLabel#licenseBadge[tone="1"] {{ background: {p.badge_bg[1]}; color: {p.badge_text[1]}; }}
QLabel#licenseBadge[tone="2"] {{ background: {p.badge_bg[2]}; color: {p.badge_text[2]}; }}
QWidget#playerControls {{ background: {p.video_bg}; border-bottom-left-radius: {RADIUS_INPUT}px;
                         border-bottom-right-radius: {RADIUS_INPUT}px; }}
QWidget#playerControls QLabel {{ color: {design.WHITE}; font-size: {FONT_CAPTION}px; }}
QWidget#playerControls QPushButton {{ color: {design.WHITE}; border: none; padding: 0 {SPACE_S}px; }}
QToolTip {{ background: {p.bg_surface}; color: {p.text_primary}; border: 1px solid {p.border};
            border-radius: 6px; padding: 6px 8px; }}

/* --- кнопки: по умолчанию tertiary --- */
QPushButton, QToolButton {{
    background: transparent; color: {p.text_primary}; border: 1px solid {p.border};
    border-radius: {RADIUS_BUTTON}px; padding: 0 {SPACE_L}px; min-height: {CONTROL_HEIGHT}px; font-weight: 500;
}}
QPushButton:hover {{ background: {p.bg_secondary}; border-color: {p.border_strong}; }}
QToolButton:hover {{ background: {p.selection_hover}; border-color: {p.border_strong}; }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton:checked {{ background: {p.selection_bg}; border-color: {p.accent_primary}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:focus {{ border: 2px solid {p.accent_primary}; }}
QPushButton:disabled {{ color: {p.disabled_text}; border-color: {p.border}; background: transparent; }}
QPushButton[flat="true"] {{ border: none; color: {p.accent_text}; padding: 0 {SPACE_S}px; }}

QPushButton[role="primary"] {{
    background: {p.accent_primary}; color: {p.text_inverse}; border: 1px solid {p.accent_primary};
}}
QPushButton[role="primary"]:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[role="primary"]:pressed {{ background: {p.accent_pressed}; border-color: {p.accent_pressed}; }}
QPushButton[role="primary"]:focus {{ border: 2px solid {p.accent_secondary}; }}
QPushButton[role="primary"]:disabled {{ background: {p.disabled_bg}; color: {p.disabled_text};
                                        border-color: {p.disabled_bg}; }}

QPushButton[role="secondary"] {{
    background: {p.bg_surface}; color: {p.accent_text}; border: 1px solid {p.accent_primary};
}}
QPushButton[role="secondary"]:hover {{ background: {p.bg_secondary}; }}
QPushButton[role="secondary"]:pressed {{ background: {p.border}; }}
QPushButton[role="secondary"]:focus {{ border: 2px solid {p.accent_primary}; }}
QPushButton[role="secondary"]:disabled {{ color: {p.disabled_text}; border-color: {p.border}; }}

QPushButton:default {{ background: {p.accent_primary}; color: {p.text_inverse}; border: 1px solid {p.accent_primary}; }}
QPushButton:default:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton:default:pressed {{ background: {p.accent_pressed}; }}
QPushButton:default:focus {{ border: 2px solid {p.accent_secondary}; }}

QPushButton[large="true"] {{ min-height: {CONTROL_HEIGHT_LARGE}px; padding: 0 {SPACE_L + 8}px;
                             font-size: {FONT_SUBTITLE}px; font-weight: 600; }}

/* --- поля ввода --- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDateEdit {{
    background: {p.bg_surface}; color: {p.text_primary}; border: 1px solid {p.border};
    border-radius: {RADIUS_INPUT}px; padding: 4px {SPACE_S + 2}px; min-height: {CONTROL_HEIGHT - 10}px;
    selection-background-color: {p.accent_primary}; selection-color: {p.selection_text};
}}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {p.border_strong}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 2px solid {p.accent_primary};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {p.disabled_bg}; color: {p.disabled_text};
}}
QLineEdit#query {{ font-size: {FONT_TITLE}px; min-height: {CONTROL_HEIGHT_LARGE - 10}px;
                   padding: 4px {SPACE_M}px; border-radius: {RADIUS_BUTTON + 2}px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {p.bg_surface}; border: 1px solid {p.border}; border-radius: {RADIUS_INPUT}px;
    selection-background-color: {p.accent_primary}; selection-color: {p.selection_text}; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 18px; }}

QCheckBox {{ spacing: {SPACE_S}px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {p.border_strong};
                        border-radius: 5px; background: {p.bg_surface}; }}
QCheckBox::indicator:hover {{ border-color: {p.accent_primary}; }}
QCheckBox::indicator:focus {{ border: 2px solid {p.accent_primary}; }}
QCheckBox::indicator:checked {{ background: {p.accent_primary}; border-color: {p.accent_primary};
                                image: url({_check_icon()}); }}
QCheckBox:disabled {{ color: {p.disabled_text}; }}
QCheckBox::indicator:disabled {{ background: {p.disabled_bg}; border-color: {p.border}; }}

/* --- вкладки и меню --- */
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {p.text_secondary}; padding: {SPACE_S}px {SPACE_L}px;
    border: none; border-bottom: 2px solid transparent; font-weight: 500; margin-right: {SPACE_XS}px;
}}
QTabBar::tab:hover {{ color: {p.text_primary}; }}
QTabBar::tab:selected {{ color: {p.accent_text}; background: {p.selection_bg};
                        border-top-left-radius: {RADIUS_INPUT}px; border-top-right-radius: {RADIUS_INPUT}px;
                        border-bottom: 2px solid {p.accent_primary}; }}
QTabBar::tab:focus {{ color: {p.accent_text}; }}

QMenuBar {{ background: {p.bg_primary}; border-bottom: 1px solid {p.border}; padding: 2px {SPACE_XS}px; }}
QMenuBar::item {{ background: transparent; padding: 6px {SPACE_M}px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {p.bg_secondary}; }}
QMenuBar::item:pressed {{ background: {p.accent_primary}; color: {p.text_inverse}; }}
QMenu {{ background: {p.bg_surface}; border: 1px solid {p.border}; border-radius: {RADIUS_INPUT}px; padding: 4px; }}
QMenu::item {{ padding: 6px {SPACE_L + 8}px 6px {SPACE_M}px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.accent_primary}; color: {p.selection_text}; }}
QMenu::item:disabled {{ color: {p.disabled_text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px {SPACE_S}px; }}

QStatusBar {{ background: {p.bg_primary}; border-top: 1px solid {p.border}; color: {p.text_secondary}; }}
QStatusBar QLabel {{ color: {p.text_secondary}; }}

/* --- списки, таблицы, деревья --- */
QAbstractItemView {{ background: {p.bg_surface}; color: {p.text_primary};
                     selection-background-color: {p.selection_bg}; selection-color: {p.text_primary}; }}
QListWidget, QTableWidget, QTreeWidget {{
    background: {p.bg_surface}; border: 1px solid {p.border}; border-radius: {RADIUS_CARD}px;
    alternate-background-color: {p.bg_secondary}; outline: none; padding: 4px;
    selection-background-color: {p.selection_bg}; selection-color: {p.text_primary};
}}
QListWidget#results {{ background: transparent; border: none; padding: 0; }}
QListWidget::item {{ border-radius: {RADIUS_BUTTON}px; padding: 4px; color: {p.text_primary}; }}
QListWidget::item:hover {{ background: {p.bg_secondary}; }}
QListWidget::item:selected {{ background: {p.selection_bg}; color: {p.text_primary}; }}
QTableWidget {{ gridline-color: {p.border}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 4px; }}
QTableWidget::item:selected, QTreeWidget::item:selected {{ background: {p.selection_bg};
                                                          color: {p.text_primary}; }}
QTreeWidget::item {{ min-height: {design.TREE_ROW_HEIGHT}px; }}
QTreeWidget::item:hover {{ background: {p.bg_secondary}; }}
QHeaderView::section {{
    background: {p.bg_surface}; color: {p.text_secondary}; border: none; border-bottom: 1px solid {p.border};
    padding: 6px {SPACE_S}px; font-weight: 600; font-size: {FONT_CAPTION}px;
}}
QTableCornerButton::section {{ background: {p.bg_surface}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {p.border_strong}; border-radius: 4px; min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {p.text_secondary}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; width: {SPACE_S}px; height: {SPACE_S}px; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent_primary}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {p.bg_surface}; border: 2px solid {p.accent_primary};
                              width: 12px; height: 12px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {p.accent_primary}; }}
QSlider:disabled::sub-page:horizontal {{ background: {p.disabled_text}; }}

QProgressBar {{ background: {p.border}; border: none; border-radius: 4px; text-align: center;
                color: {p.text_primary}; min-height: 8px; }}
QProgressBar::chunk {{ background: {p.accent_primary}; border-radius: 4px; }}

QDialogButtonBox QPushButton {{ min-width: 96px; }}
QMessageBox QLabel {{ font-size: {FONT_BODY}px; }}
QWidget#playerDetails, QWidget#detailsViewport {{ background: {p.bg_surface}; }}
"""


def _check_icon() -> str:
    """Галочка для флажков (белая, PNG из папки ресурсов)."""
    return (paths.resources_dir() / "check.png").as_posix()


def qt_palette(p: Palette) -> QPalette:
    """Палитра Qt — для элементов, которых стиль не касается (системные диалоги, полосы)."""
    pal = QPalette()
    roles = QPalette.ColorRole
    for role, color in (
        (roles.Window, p.bg_secondary), (roles.WindowText, p.text_primary), (roles.Base, p.bg_surface),
        (roles.AlternateBase, p.bg_secondary), (roles.Text, p.text_primary), (roles.Button, p.bg_surface),
        (roles.ButtonText, p.text_primary), (roles.Highlight, p.selection_bg),
        (roles.HighlightedText, p.text_primary), (roles.Link, p.accent_text), (roles.LinkVisited, p.accent_secondary),
        (roles.ToolTipBase, p.bg_surface), (roles.ToolTipText, p.text_primary),
        (roles.PlaceholderText, p.text_secondary),
        (roles.BrightText, p.text_inverse),
    ):
        pal.setColor(role, QColor(color))
    for role in (roles.Text, roles.WindowText, roles.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.disabled_text))
    return pal


def apply(app: QApplication, choice: str = SYSTEM) -> str:
    """Применить тему к программе; вернуть применённую («light» или «dark»)."""
    global _current
    _current = resolve(choice)
    p = palette()
    app.setStyle("Fusion")
    app.setPalette(qt_palette(p))
    font = QFont(FONT_FAMILY)
    font.setPixelSize(FONT_BODY)
    app.setFont(font)
    app.setStyleSheet(stylesheet(p))
    for widget in app.allWidgets():
        if name := widget.property("icon_name"):
            set_icon(widget, name)
    return _current
