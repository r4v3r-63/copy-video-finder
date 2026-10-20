"""Оформление программы по дизайн-системе Copy.ru (токены — в `app/design.py`).

Виджеты не задают цвета сами: кнопкам назначается роль (`set_role`), панелям — роль
карточки (`make_card`), всё остальное оформляется общим стилем `stylesheet()`.
Состояния hover, focus, pressed и disabled описаны в стиле для каждого элемента.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from app import paths

# Токены, которые виджеты берут как theme.PRIMARY, theme.SPACE_L и т. п., — тоже отсюда.
from app.design import (  # noqa: F401 — часть импортов только для виджетов
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
QToolTip {{ background: {p.bg_surface}; color: {p.text_primary}; border: 1px solid {p.border};
            border-radius: 6px; padding: 6px 8px; }}

/* --- кнопки: по умолчанию tertiary --- */
QPushButton {{
    background: transparent; color: {p.text_primary}; border: 1px solid {p.border};
    border-radius: {RADIUS_BUTTON}px; padding: 0 {SPACE_L}px; min-height: {CONTROL_HEIGHT}px; font-weight: 500;
}}
QPushButton:hover {{ background: {p.bg_secondary}; border-color: {p.border_strong}; }}
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

QPushButton[size="large"] {{ min-height: {CONTROL_HEIGHT_LARGE}px; padding: 0 {SPACE_L + 8}px;
                             font-size: {FONT_SUBTITLE}px; font-weight: 600; }}

/* --- поля ввода --- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
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
QTabBar::tab:selected {{ color: {p.accent_text}; border-bottom: 2px solid {p.accent_primary}; }}
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
                     selection-background-color: {p.accent_primary}; selection-color: {p.selection_text}; }}
QListWidget, QTableWidget, QTreeWidget {{
    background: {p.bg_surface}; border: 1px solid {p.border}; border-radius: {RADIUS_CARD}px;
    alternate-background-color: {p.bg_secondary}; outline: none; padding: 4px;
    selection-background-color: {p.accent_primary}; selection-color: {p.selection_text};
}}
QListWidget::item {{ border-radius: {RADIUS_BUTTON}px; padding: 4px; color: {p.text_primary}; }}
QListWidget::item:hover {{ background: {p.bg_secondary}; }}
QListWidget::item:selected {{ background: {p.accent_primary}; color: {p.selection_text}; }}
QTableWidget {{ gridline-color: {p.border}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 4px; }}
QTableWidget::item:selected, QTreeWidget::item:selected {{ background: {p.accent_primary};
                                                          color: {p.selection_text}; }}
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
        (roles.ButtonText, p.text_primary), (roles.Highlight, p.accent_primary),
        (roles.HighlightedText, p.selection_text), (roles.Link, p.accent_text), (roles.LinkVisited, p.accent_secondary),
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
    return _current
