"""Дизайн-токены Copy.ru: палитра, размеры, скругления, типографика.

Единственное место, где они заданы. Из них собираются оформление программы
(`app/ui/theme.py`) и CSS-переменные HTML-экспорта (`css_variables`). Чтобы поменять
цвет, размер или шрифт во всей программе, достаточно поправить значение здесь.

Палитра бренда ориентировочная (по маркетинг-киту Copy.ru), не официальные коды.
Контраст текста и активных элементов обеих тем — не ниже WCAG AA (тест test_design).
"""

from dataclasses import dataclass

# --- палитра бренда ---
WHITE = "#FFFFFF"
BLACK = "#111111"
BLUE = "#246BEE"
PURPLE = "#7B35B5"

SYSTEM, LIGHT, DARK = "system", "light", "dark"
THEME_NAMES = {SYSTEM: "Как в системе", LIGHT: "Светлая", DARK: "Тёмная"}

# --- размеры ---
RADIUS_BUTTON = 10  # 8–12
RADIUS_INPUT = 8
RADIUS_CARD = 14  # 12–16
SPACE_XS, SPACE_S, SPACE_M, SPACE_L = 4, 8, 12, 16
CONTROL_HEIGHT = 34
CONTROL_HEIGHT_LARGE = 44  # главное поле поиска и кнопка «Найти»

# --- типографика: Inter, единая шкала (px) ---
FONT_FAMILY = "Inter"
FONT_FALLBACK = "Segoe UI, Arial, sans-serif"
FONT_CAPTION = 12
FONT_BODY = 14
FONT_SUBTITLE = 16
FONT_TITLE = 18  # главное поле поиска, заголовки вкладок
FONT_DISPLAY = 24
FONT_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf")

# Роли кнопок
PRIMARY, SECONDARY, TERTIARY = "primary", "secondary", "tertiary"


@dataclass(frozen=True)
class Palette:
    """Семантические цвета одной темы."""
    bg_primary: str  # основной фон окон, карточек
    bg_secondary: str  # фон за карточками, наведение на строки
    bg_surface: str  # карточки и поля ввода
    text_primary: str
    text_secondary: str
    text_inverse: str
    accent_primary: str  # основные кнопки, активные вкладки, выделение
    accent_hover: str
    accent_pressed: str
    accent_text: str  # синий для ссылок и текста secondary-кнопок (на тёмном фоне — светлее)
    accent_secondary: str  # фиолетовый: второстепенные акценты
    border: str
    border_strong: str  # рамка полей при наведении
    disabled_bg: str
    disabled_text: str
    selection_text: str
    # покрытие услуг: нет роликов / 1 / 2 / цель выполнена
    coverage: tuple[str, str, str, str]


PALETTES = {
    LIGHT: Palette(
        bg_primary=WHITE, bg_secondary="#F5F6F8", bg_surface=WHITE,
        text_primary=BLACK, text_secondary="#666666", text_inverse=WHITE,
        accent_primary=BLUE, accent_hover="#1F5FD6", accent_pressed="#1A51B8",
        accent_text="#1F63E0",  # фирменный синий текстом на сером фоне чуть ниже AA — на полтона темнее
        accent_secondary=PURPLE, border="#E5E7EB", border_strong="#C9CDD4",
        disabled_bg="#EEF0F3", disabled_text="#9CA3AF", selection_text=WHITE,
        coverage=("#FBE3E3", "#FCEBD7", "#FBF3CF", "#DDF2E2"),
    ),
    DARK: Palette(
        bg_primary=BLACK, bg_secondary="#0B0B0C", bg_surface="#1B1C1F",
        text_primary=WHITE, text_secondary="#A1A1AA", text_inverse=WHITE,
        accent_primary=BLUE, accent_hover="#1F5FD6", accent_pressed="#1A51B8", accent_text="#6FA0F5",
        accent_secondary="#A774D6", border="#2C2D31", border_strong="#45464C",
        disabled_bg="#232427", disabled_text="#6B6B73", selection_text=WHITE,
        coverage=("#4A2224", "#4A3320", "#46401F", "#1F3F2A"),
    ),
}


def css_variables() -> str:
    """CSS-переменные из тех же токенов: светлая тема и тёмная по настройке системы."""
    def block(p: Palette) -> str:
        return "; ".join(f"{name}: {value}" for name, value in (
            ("--color-white", WHITE), ("--color-black", BLACK), ("--color-blue", BLUE), ("--color-purple", PURPLE),
            ("--bg-primary", p.bg_primary), ("--bg-dark", BLACK), ("--bg-secondary", p.bg_secondary), ("--bg-surface", p.bg_surface),
            ("--text-primary", p.text_primary), ("--text-secondary", p.text_secondary),
            ("--text-inverse", p.text_inverse), ("--accent-primary", p.accent_primary),
            ("--accent-hover", p.accent_hover), ("--accent-text", p.accent_text),
            ("--accent-secondary", p.accent_secondary), ("--border-color", p.border),
            ("--radius-button", f"{RADIUS_BUTTON}px"), ("--radius-input", f"{RADIUS_INPUT}px"),
            ("--radius-card", f"{RADIUS_CARD}px"),
            ("--font-family", f'"{FONT_FAMILY}", system-ui, {FONT_FALLBACK}'),
            ("--font-caption", f"{FONT_CAPTION}px"), ("--font-body", f"{FONT_BODY}px"),
            ("--font-subtitle", f"{FONT_SUBTITLE}px"), ("--font-title", f"{FONT_TITLE}px"),
            ("--font-display", f"{FONT_DISPLAY}px"),
            ("--ok", "#1E7B3A" if p is PALETTES[LIGHT] else "#7BD08F"),
            ("--warn", "#9A5B00" if p is PALETTES[LIGHT] else "#F0B45A"),
            ("--bad", "#B42318" if p is PALETTES[LIGHT] else "#F39A90"),
        ))
    return (f":root {{ {block(PALETTES[LIGHT])}; }}\n"
            f"@media (prefers-color-scheme: dark) {{ :root {{ {block(PALETTES[DARK])}; }} }}")
