"""The pairing code: one short string that is the household's whole identity.

There is no server and no account. A home is created on one television, which
prints a code; every other television joins by typing that code into the
add-on's settings. So the code is not a convenience — it is the *only* secret
protecting the household's watch history, and every property below follows from
that single fact.

**Why 16 characters and not 6.** OWASP's session-management guidance puts the
floor for a bearer secret at 64 bits of entropy to resist guessing, preferring
128 (https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html:
"Session identifiers must have at least 64 bits of entropy to prevent
brute-force session guessing attacks"). Crockford Base32 carries 5 bits per
character, so 16 characters is 80 bits — above that floor, and still four
groups of four to read off a screen. A 6-character code would be 30 bits, which
is the Zoom-meeting-ID failure: a 9-to-11-digit space was enumerated by
zWarDial at roughly a hundred live meetings an hour.

**Why Crockford Base32 and not base36 or hex.** Somebody reads this code off
one television and types it into another, probably with a remote control.
Crockford's alphabet exists for exactly that: it drops I, L, O and U — "I · Can
be confused with 1 / O · Can be confused with 0 / U · Accidental obscenity"
(http://www.crockford.com/base32.html). `normalize` therefore *accepts* what a
person actually types — lowercase, spaces, dashes, a typed `l` for `1`, a typed
`o` for `0` — instead of rejecting it. A code that is correct but refused is
indistinguishable, to the person holding the remote, from a code that is wrong.

**Why the code is split into three derived values and never sent anywhere.**
The channel is a public topic on somebody else's free service. If the code were
the topic name, the operator of that service — and anyone who guessed a topic —
would read what this household watches. So the code is a *key*, and HKDF-SHA256
derives three independent values from it: a **topic**, the only part that
travels; an **item secret**, which turns a title into an opaque identifier; and
an **auth key**, which signs every message. This is Firefox Send's shape
(https://github.com/mozilla/send/blob/master/docs/encryption.md: "The secret key
is used to derive more keys via HKDF SHA-256").

**Why there is no cipher here.** The first design encrypted the payload, which
needs AES — and the official Kodi add-on repository has no cipher library at
all: a scan of all 2,327 add-ons in the Omega repository turned up
`script.module.pyscrypt` and nothing else. Vendoring an AES implementation into
a TV add-on to protect a list of episode names is the wrong trade. So the
titles simply never leave the house: a message carries the **item secret's
HMAC of the media's identity**, a position and a timestamp, and each television
resolves that opaque key against *its own* library, where the title already is.
There is no plaintext to encrypt because there is no plaintext — which is a
stronger property than encrypting one, and it needs only `hmac` and `hashlib`
from the standard library.

Pure module: no Kodi imports, no network, no files.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

# Crockford Base32. Note what is absent: I, L, O, U.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# What a person might type instead of the right character. U is deliberately NOT
# here: Crockford excludes it to avoid accidental obscenity, so a typed U is a
# typo with no single obvious intent, and guessing one would silently produce a
# different home than the one printed on the other television.
_CONFUSABLE = {"I": "1", "L": "1", "O": "0"}

CODE_CHARS = 16          # 16 x 5 bits = 80 bits
GROUP = 4                # printed as XXXX-XXXX-XXXX-XXXX
_CODE_BITS = CODE_CHARS * 5

_TOPIC_INFO = b"roomwatchsync/v1/document-id"
_ITEM_SECRET_INFO = b"roomwatchsync/v1/content-key"
_AUTH_KEY_INFO = b"roomwatchsync/v1/auth-key"
_SALT = b"roomwatchsync/v1"

TOPIC_CHARS = 26         # 130 bits of topic space, so two homes never collide
SECRET_BYTES = 32


class InvalidCode(ValueError):
    """The text the user typed is not a pairing code. Always carries a reason —
    'invalid code' alone leaves a person re-typing the same thing forever."""


def _hkdf(secret: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256 (RFC 5869), extract-then-expand, stdlib only."""
    prk = hmac.new(_SALT, secret, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _b32_encode(value: int, chars: int) -> str:
    digits = []
    for _ in range(chars):
        digits.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(digits))


def generate_code() -> str:
    """A new home's code, from the OS CSPRNG. 80 bits, grouped for reading."""
    value = int.from_bytes(os.urandom((_CODE_BITS + 7) // 8), "big")
    value &= (1 << _CODE_BITS) - 1
    return format_code(_b32_encode(value, CODE_CHARS))


def format_code(code: str) -> str:
    """Group a canonical code for display. Display only — never for comparison."""
    bare = re.sub(r"[^0-9A-Z]", "", code.upper())
    return "-".join(bare[i:i + GROUP] for i in range(0, len(bare), GROUP))


def normalize(text: str) -> str:
    """What the user typed -> the canonical code, or raise with a reason.

    Tolerant on purpose: case, spaces, dashes and the three confusable letters
    are all accepted, because the person doing this is holding a remote control
    and reading off another television.
    """
    if text is None:
        raise InvalidCode("no code was entered")
    raw = re.sub(r"[\s\-_]", "", str(text)).upper()
    if not raw:
        raise InvalidCode("no code was entered")
    out = []
    for char in raw:
        char = _CONFUSABLE.get(char, char)
        if char not in ALPHABET:
            raise InvalidCode("the character %r is not part of a pairing code" % char)
        out.append(char)
    if len(out) != CODE_CHARS:
        raise InvalidCode("a pairing code has %d characters, this one has %d"
                          % (CODE_CHARS, len(out)))
    return "".join(out)


def is_valid(text: str) -> bool:
    try:
        normalize(text)
    except InvalidCode:
        return False
    return True


def _secret(text: str) -> bytes:
    canonical = normalize(text)
    value = 0
    for char in canonical:
        value = (value << 5) | ALPHABET.index(char)
    return value.to_bytes((_CODE_BITS + 7) // 8, "big")


def topic(text: str) -> str:
    """The only part of the code that ever leaves the device.

    Derived, not the code itself: ntfy's own documentation says "there is no
    sign-up, the topic is essentially a password, so pick something that's not
    easily guessable" (https://docs.ntfy.sh/publish/). 130 bits is that. And
    because it is derived rather than equal to the code, the one value the
    service operator sees is not the value that unlocks anything.
    """
    secret = _secret(text)
    raw = _hkdf(secret, _TOPIC_INFO, 17)
    value = int.from_bytes(raw, "big") & ((1 << (TOPIC_CHARS * 5)) - 1)
    return _b32_encode(value, TOPIC_CHARS)


def item_secret(text: str) -> bytes:
    """Turns a media identity into an opaque key. Never transmitted, never
    logged — it is what keeps titles inside the house."""
    return _hkdf(_secret(text), _ITEM_SECRET_INFO, SECRET_BYTES)


def auth_key(text: str) -> bytes:
    """Signs every message. A topic is 130 bits and will not be guessed, but
    "will not be guessed" is not "cannot be written to": anyone who learns the
    topic — from a screenshot, a support request, a shoulder — could otherwise
    push a false position into the household's library."""
    return _hkdf(_secret(text), _AUTH_KEY_INFO, SECRET_BYTES)


def derive(text: str) -> tuple[str, bytes, bytes]:
    """All three at once: (topic, item secret, auth key)."""
    return topic(text), item_secret(text), auth_key(text)


# Kept so an older caller does not silently get `None` back; the new name says
# what the value is actually for.
document_id = topic
