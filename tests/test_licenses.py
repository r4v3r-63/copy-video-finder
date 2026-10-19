import pytest

from app.licenses import ALLOWED, ATTRIBUTION, REJECTED, REVIEW, SHARE_ALIKE_NOTE, attribution_text, evaluate


@pytest.mark.parametrize("name, status", [
    ("CC0", ALLOWED),
    ("CC0 1.0", ALLOWED),
    ("Public domain", ALLOWED),
    ("PD-USGov", ALLOWED),
    ("Pexels License", ALLOWED),
    ("Pixabay Content License", ALLOWED),
    ("CC BY 4.0", ATTRIBUTION),
    ("CC-BY-2.0", ATTRIBUTION),
    ("CC BY-SA 3.0", ATTRIBUTION),
    ("CC BY-NC 4.0", REJECTED),
    ("CC BY-NC-SA 4.0", REJECTED),
    ("CC BY-ND 4.0", REJECTED),
    ("Creative Commons Attribution-NonCommercial", REJECTED),
    ("Editorial Use Only", REJECTED),
    ("Personal Use", REJECTED),
    ("Premium", REJECTED),
    ("Royalty Free", REVIEW),
    ("GFDL", REVIEW),
    ("", REVIEW),
    (None, REVIEW),
])
def test_license_table(name, status):
    assert evaluate(name)[0] == status


def test_share_alike_note():
    assert evaluate("CC BY-SA 4.0")[1] == SHARE_ALIKE_NOTE
    assert evaluate("CC BY 4.0")[1] == ""


def test_attribution_text():
    assert attribution_text("Анна", "Ламинатор", "CC BY 4.0", "https://x") == "Анна — Ламинатор — CC BY 4.0 — https://x"
    assert attribution_text("Анна", "", "CC BY 4.0", "https://x") == "Анна — CC BY 4.0 — https://x"
