"""
How the engine writes names, emails and phone numbers so TTS reads them clearly.

The rules match Relay Hawk's production text normalizer and come from our TTS
readback evaluation (~4,500 synthesized clips, each scored by STT). The short
version:

- Spell letters as UPPER-CASE names joined by " - ". A plain space runs letters
  together; a period makes ElevenLabs Flash rush them like initials; a comma is
  an optional pause Flash skips when it is speaking fast.
- Say "@" as " - at - " so there is a pause on both sides of it.
- Digits become words ("six"), never bare digits, which Flash may read as one big
  number ("four billion seven hundred...").
- Phone numbers are grouped 3-3-4 and the groups are joined with a PERIOD. Over
  10 runs per separator, a period kept both group pauses 10/10 times and a comma
  only 3/10.
- Well-known email domains are spoken as words ("gmail dot com"); unknown ones
  are spelled so the caller can catch "relayhawk" vs "reallyhawk".

Spelled-out words work on every provider ConversationRelay supports (ElevenLabs,
Google, Amazon), so there is no per-provider branching here. ConversationRelay's
`elevenlabsTextNormalization` has nothing left to normalize in these strings.

Because the engine knows each value's type, it formats it here before sending it
to Twilio. Nothing has to guess from a regex whether "4045550123" is a phone number.
"""

import os

import phonenumbers

# Region assumed for numbers said without a country code, and read back without one.
DEFAULT_REGION = os.getenv("DEFAULT_PHONE_REGION", "US")

DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

GROUP_SEPARATOR = ". "  # between phone digit groups
LETTER_SEPARATOR = " - "  # between spelled characters
AT_SEPARATOR = " - at - "

SYMBOL_WORDS = {".": "dot", "_": "underscore", "-": "dash", "+": "plus", "'": "apostrophe"}

# Domains spoken as words instead of spelled. Anything else (including
# initialisms like AOL) is spelled letter by letter.
KNOWN_EMAIL_DOMAINS: dict[str, str] = {
    "gmail.com": "gmail dot com",
    "googlemail.com": "googlemail dot com",
    "yahoo.com": "yahoo dot com",
    "yahoo.co.uk": "yahoo dot co dot uk",
    "outlook.com": "outlook dot com",
    "hotmail.com": "hotmail dot com",
    "live.com": "live dot com",
    "icloud.com": "icloud dot com",
    "me.com": "me dot com",
    "mac.com": "mac dot com",
    "protonmail.com": "protonmail dot com",
    "proton.me": "proton dot me",
    "comcast.net": "comcast dot net",
    "verizon.net": "verizon dot net",
    "att.net": "att dot net",
}


# ---------------------------------------------------------------------------
# Spelling
# ---------------------------------------------------------------------------

def spell_chars(text: str) -> str:
    """'Hopper' -> 'H - O - P - P - E - R'; digits and symbols become words. Spaces are dropped."""
    out: list[str] = []
    for ch in text:
        if ch.isspace():
            continue
        if ch.isalpha():
            out.append(ch.upper())
        elif ch in DIGIT_WORDS:
            out.append(DIGIT_WORDS[ch])
        else:
            out.append(SYMBOL_WORDS.get(ch, ch))
    return LETTER_SEPARATOR.join(out)


def format_email_for_speech(email: str) -> str:
    """
    >>> format_email_for_speech("grace@relayhawk.com")
    'G - R - A - C - E - at - R - E - L - A - Y - H - A - W - K dot com'
    >>> format_email_for_speech("brandondoyle650@gmail.com")
    'B - R - A - N - D - O - N - D - O - Y - L - E - six - five - zero - at - gmail dot com'
    """
    local, sep, domain = email.partition("@")
    if not sep:
        return spell_chars(email)
    known = KNOWN_EMAIL_DOMAINS.get(domain.lower())
    if known is not None:
        spoken_domain = known
    else:
        *labels, tld = domain.split(".")
        # Spell every label except the TLD, which is spoken as a word.
        spoken_domain = " dot ".join([spell_chars(label) for label in labels] + [tld.lower()])
    return f"{spell_chars(local)}{AT_SEPARATOR}{spoken_domain}"


def say_email(email: str) -> str:
    """Natural form for a summary, after the spelling was already confirmed: 'grace at relayhawk dot com'."""
    local, _, domain = email.partition("@")

    def words(part: str) -> str:
        # Symbols and digits become words; bare "650" can be read as "six hundred fifty".
        spoken = (f" {SYMBOL_WORDS.get(ch) or DIGIT_WORDS[ch]} " if ch in SYMBOL_WORDS or ch in DIGIT_WORDS else ch
                  for ch in part)
        return " ".join("".join(spoken).split())

    return f"{words(local)} at {KNOWN_EMAIL_DOMAINS.get(domain) or domain.replace('.', ' dot ')}"


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

def _group_sizes(num_digits: int) -> tuple[int, ...]:
    if num_digits == 10:  # NANP: 404 555 0123
        return (3, 3, 4)
    if num_digits == 11:  # 1 404 555 0123
        return (1, 3, 3, 4)
    if num_digits == 7:  # 555 0123
        return (3, 4)
    return (num_digits,)  # international or unusual: spell every digit


def format_digits_for_speech(digits: str) -> str:
    """
    >>> format_digits_for_speech("3125550148")
    'three one two. five five five. zero one four eight'
    """
    groups: list[str] = []
    i = 0
    for size in _group_sizes(len(digits)):
        group = digits[i:i + size]
        i += size
        if group:
            groups.append(" ".join(DIGIT_WORDS[d] for d in group))
    if i < len(digits):
        groups.append(" ".join(DIGIT_WORDS[d] for d in digits[i:]))
    return GROUP_SEPARATOR.join(groups)


def normalize_phone(raw: str, default_region: str = DEFAULT_REGION) -> str:
    """
    Validate a phone number and return it in E.164 ('+14045550123').
    Parse with
    `phonenumbers`, then require `is_valid_number` (real area code, right length).
    """
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"'{raw}' is not a phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        if len(phonenumbers.national_significant_number(parsed)) == 7:
            raise ValueError(f"'{raw}' is missing its area code")
        raise ValueError(f"'{raw}' is not a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def format_phone_for_speech(e164: str, default_region: str = DEFAULT_REGION) -> str:
    """
    Numbers in the default region's calling code are read in national form; others with their country code.

    >>> format_phone_for_speech("+14045550123")
    'four zero four. five five five. zero one two three'
    """
    parsed = phonenumbers.parse(e164, default_region)
    national = phonenumbers.national_significant_number(parsed)
    # Same calling code (e.g. all of NANP for US, Canada included): no "plus one" prefix.
    if parsed.country_code == phonenumbers.country_code_for_region(default_region):
        return format_digits_for_speech(national)
    country = " ".join(DIGIT_WORDS[d] for d in str(parsed.country_code))
    return f"plus {country}{GROUP_SEPARATOR}{format_digits_for_speech(national)}"


def last_four_for_speech(e164: str) -> str:
    """'+14045550123' -> 'zero one two three'."""
    return " ".join(DIGIT_WORDS[d] for d in e164[-4:])
