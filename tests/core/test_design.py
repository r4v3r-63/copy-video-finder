"""Дизайн-система: контраст по WCAG AA, состояния элементов, единые токены."""

import re

import pytest

from app.core import design
from tests.fakes import ROOT

AA_TEXT = 4.5  # обычный текст
AA_UI = 3.0  # крупный текст, рамки и значки активных элементов


def luminance(hex_color: str) -> float:
    channels = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("name", [design.LIGHT, design.DARK])
def test_contrast_wcag_aa(name):
    p = design.PALETTES[name]
    for bg in (p.bg_primary, p.bg_secondary, p.bg_surface):
        assert contrast(p.text_primary, bg) >= AA_TEXT
        assert contrast(p.text_secondary, bg) >= AA_TEXT
        assert contrast(p.accent_text, bg) >= AA_TEXT  # ссылки и текст secondary-кнопок
    assert contrast(p.text_inverse, p.accent_primary) >= AA_TEXT  # текст на primary-кнопке
    assert contrast(p.text_inverse, p.accent_hover) >= AA_TEXT
    assert contrast(p.selection_text, p.accent_primary) >= AA_TEXT
    for level in p.coverage:  # строки покрытия: текст поверх цвета
        assert contrast(p.text_primary, level) >= AA_TEXT
    assert contrast(p.accent_primary, p.bg_surface) >= AA_UI  # рамка фокуса и secondary-кнопки


def test_brand_palette():
    assert (design.WHITE, design.BLACK, design.BLUE, design.PURPLE) == ("#FFFFFF", "#111111", "#246BEE", "#7B35B5")
    light, dark = design.PALETTES[design.LIGHT], design.PALETTES[design.DARK]
    assert light.bg_primary == design.WHITE and light.text_primary == design.BLACK
    assert dark.bg_primary == design.BLACK and dark.text_primary == design.WHITE
    assert light.accent_primary == dark.accent_primary == design.BLUE
    assert light.accent_secondary == design.PURPLE


def test_css_variables_from_spec():
    css = design.css_variables()
    for var in ("--color-white", "--color-black", "--color-blue", "--color-purple", "--bg-primary",
                "--bg-secondary", "--bg-dark", "--text-primary", "--text-secondary", "--text-inverse",
                "--accent-primary", "--accent-secondary", "--border-color"):
        assert f"{var}:" in css
    assert "prefers-color-scheme: dark" in css


def test_no_colors_outside_tokens():
    """Цвета задаются только токенами: ни одного HEX-кода в коде, кроме app/design.py."""
    root = ROOT / "app"
    offenders = [str(path.relative_to(root)) for path in root.rglob("*.py")
                 if path.name != "design.py" and re.search(r"#[0-9A-Fa-f]{6}\b", path.read_text(encoding="utf-8"))]
    assert offenders == []


def test_stylesheet_states():
    from app.ui import theme
    for name in (design.LIGHT, design.DARK):
        qss = theme.stylesheet(design.PALETTES[name])
        for role in (design.PRIMARY, design.SECONDARY):
            for state in ("hover", "pressed", "focus", "disabled"):
                assert f'QPushButton[role="{role}"]:{state}' in qss
        for state in ("hover", "pressed", "focus", "disabled"):
            assert f"QPushButton:{state}" in qss
        assert "QLineEdit:focus" in qss and "QLineEdit:disabled" in qss


def test_theme_choice():
    from app.ui import theme
    assert theme.resolve(design.LIGHT) == design.LIGHT
    assert theme.resolve(design.DARK) == design.DARK
    assert theme.resolve(design.SYSTEM) in (design.LIGHT, design.DARK)


def test_fonts_bundled():
    from app import paths
    for name in design.FONT_FILES:
        assert (paths.resources_dir() / "fonts" / name).is_file()
    assert (paths.resources_dir() / "fonts" / "Inter-OFL.txt").is_file()
