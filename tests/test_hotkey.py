import pytest

from meetingrec.hotkey import parse_hotkey


def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+r") == (0x2 | 0x1, ord("R"))
    assert parse_hotkey(" Shift + Win + F9 ") == (0x4 | 0x8, 0x78)
    assert parse_hotkey("ctrl+space") == (0x2, 0x20)


@pytest.mark.parametrize("spec", ["r", "ctrl+", "hyper+r", "ctrl+pageup", ""])
def test_parse_hotkey_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        parse_hotkey(spec)
