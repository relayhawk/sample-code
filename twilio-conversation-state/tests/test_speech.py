"""Pins the exact strings the engine sends to TTS."""

import pytest

from src.speech import (
    format_digits_for_speech,
    format_email_for_speech,
    format_phone_for_speech,
    normalize_phone,
    say_email,
    spell_chars,
)


def test_email_readback_is_spelled():
    assert format_email_for_speech("grace@relayhawk.com") == (
        "G - R - A - C - E - at - R - E - L - A - Y - H - A - W - K dot com"
    )
    assert format_email_for_speech("brandondoyle650@gmail.com") == (
        "B - R - A - N - D - O - N - D - O - Y - L - E - six - five - zero - at - gmail dot com"
    )


def test_email_symbols_and_unknown_subdomains_are_spelled():
    assert format_email_for_speech("g.hopper@mail.relayhawk.io") == (
        "G - dot - H - O - P - P - E - R - at - M - A - I - L dot R - E - L - A - Y - H - A - W - K dot io"
    )


def test_summary_email_is_natural_with_digits_as_words():
    assert say_email("grace@relayhawk.com") == "grace at relayhawk dot com"
    assert say_email("brandon650@gmail.com") == "brandon six five zero at gmail dot com"


def test_phone_readback_is_grouped():
    assert format_digits_for_speech("3125550148") == "three one two. five five five. zero one four eight"
    assert format_phone_for_speech("+19025550123") == "nine zero two. five five five. zero one two three"
    assert format_phone_for_speech("+442071838750").startswith("plus four four. ")


def test_phone_validation_normalizes_to_e164():
    assert normalize_phone("(312) 555-0148") == "+13125550148"
    assert normalize_phone("1 312 555 0148") == "+13125550148"
    with pytest.raises(ValueError, match="area code"):
        normalize_phone("555-1234")
    for bad in [ "+15551230000", "12345", "call me maybe"]:
        with pytest.raises(ValueError):
            normalize_phone(bad)


def test_name_spelling():
    assert spell_chars("Hopper") == "H - O - P - P - E - R"
    assert spell_chars("O'Brien") == "O - apostrophe - B - R - I - E - N"
