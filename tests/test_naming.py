from __future__ import annotations

from studio.naming import safe_local_name


def test_safe_local_name_preserves_non_latin_identity():
    assert safe_local_name("صوت عربي") == "صوت-عربي"
    assert safe_local_name("ナレーター") == "ナレーター"


def test_safe_local_name_avoids_windows_reserved_devices_and_trailing_punctuation():
    assert safe_local_name("CON").casefold() != "con"
    assert safe_local_name("nul.txt") == "nul.txt"  # helper receives stems; extension policy belongs to caller
    assert not safe_local_name("hello. ").endswith((".", " "))


def test_safe_local_name_bounds_long_unicode_names_with_deterministic_suffix():
    raw = "صوت" * 200
    first = safe_local_name(raw)
    second = safe_local_name(raw)
    other = safe_local_name(raw + "آخر")

    assert first == second
    assert first != other
    assert len(first.encode("utf-8")) <= 180
    assert first.startswith("صوت")


def test_safe_local_name_casefolds_canonical_ids_when_requested():
    assert safe_local_name("Narrator MAIN", casefold=True) == "narrator-main"
