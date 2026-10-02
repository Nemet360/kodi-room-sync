"""What one television actually puts on the wire, and what the next one trusts.

Three properties, each the answer to a specific way this could leak or be
abused:

**No title ever leaves the house.** A message carries `item_key` — an HMAC of
the media's identity under the household's item secret — not "The Sopranos
S03E08". The ntfy operator, and anyone who ever sees the topic, gets opaque
32-hex-character keys and integers. Each television resolves a key against its
*own* library, where the title already is. A set that cannot be read is better
than one that is encrypted, and it costs no cipher: this module needs only
`hmac`, `hashlib` and `json`.

**Identity is canonicalised before it is hashed, or the two televisions derive
different keys for the same episode.** A key built from a file path would
differ between a NAS mount and a local disk; one built from a title would
differ on capitalisation and on `The Office (US)` versus `The Office US`. So
`item_key` takes structured identity — an external id where Kodi has one, else
show/season/episode or title/year — lowercases, collapses whitespace and
punctuation, and sorts nothing: the field order is fixed in code.

**Every message is signed.** The topic is 130 bits and will not be guessed, but
that is not the same as "cannot be written to" — a topic leaks through a
screenshot or a support request, and an unsigned protocol would then let a
stranger rewind somebody's film. `verify` rejects anything whose tag does not
match, using `hmac.compare_digest`.

Pure module: no Kodi imports, no network, no clock of its own (`now` is passed
in). All decisions here, no I/O.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

VERSION = 1

# Nothing is trusted from more than this far in the future: a television whose
# clock is wrong by a month would otherwise win last-write-wins forever and
# freeze the household's progress at whatever it last saw.
MAX_CLOCK_SKEW_S = 3600

# A position this close to the end means "finished", not "resume here" — and a
# finished item must never drag another television back to 99%.
MIN_POSITION_S = 10

_PUNCT = re.compile(r"[^0-9a-z]+")


class Rejected(ValueError):
    """A message that will not be acted on, with the reason. Always a reason:
    "bad message" in a log is a support request nobody can answer."""


def _canonical(value) -> str:
    text = "" if value is None else str(value)
    return _PUNCT.sub(" ", text.strip().lower()).strip()


def item_identity(kind: str, *, uniqueids=None, title=None, year=None,
                  showtitle=None, season=None, episode=None) -> str:
    """The stable, human-independent name of one piece of media.

    An external id wins whenever Kodi has one — that is the only identity that
    survives a renamed file, a re-scraped library and a different language's
    title. The textual fallback exists because a hand-added file has no ids at
    all, and no sync is worse than a slightly fragile one.
    """
    kind = _canonical(kind) or "video"
    for source in ("imdb", "tmdb", "tvdb"):
        value = (uniqueids or {}).get(source) if isinstance(uniqueids, dict) else None
        if value:
            # Deliberately WITHOUT the kind. An imdb/tmdb/tvdb id is already
            # globally unique, while the kind is a guess: `Player.GetItem` on a
            # playing movie often returns no `type` at all, so including it made
            # the television that was playing a file and the television that held
            # it in its library derive two different keys for the same movie —
            # with no error anywhere, which reads as "sync does not work".
            return "%s:%s" % (source, _canonical(value))
    if kind == "episode" or showtitle:
        return "episode|%s|s%s|e%s" % (_canonical(showtitle),
                                       int(season or 0), int(episode or 0))
    return "%s|%s|%s" % (kind, _canonical(title), int(year or 0))


def item_key(secret: bytes, identity: str) -> str:
    """The opaque name that travels. Deterministic, so two televisions agree."""
    return hmac.new(secret, identity.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _payload_bytes(payload: dict) -> bytes:
    """Canonical JSON for signing: sorted keys, no spaces. Two devices must
    produce byte-identical input or every signature fails."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign(auth: bytes, payload: dict) -> str:
    return hmac.new(auth, _payload_bytes(payload), hashlib.sha256).hexdigest()


def build(auth: bytes, device: str, entries: list[dict], now: float,
          snapshot: bool = False) -> str:
    """One message, ready to publish. `entries` are already opaque."""
    payload = {
        "v": VERSION,
        "dev": _canonical(device) or "kodi",
        "at": int(now),
        "snap": bool(snapshot),
        "items": [
            {"k": str(e["k"]), "pos": int(e["pos"]), "dur": int(e.get("dur") or 0),
             "at": int(e.get("at") or now)}
            for e in entries
        ],
    }
    return json.dumps({"p": payload, "mac": sign(auth, payload)},
                      separators=(",", ":"), ensure_ascii=False)


def parse(auth: bytes, raw, now: float, own_device: str = "") -> dict:
    """Someone else's message -> a payload this device may act on, or Rejected.

    Order matters: the signature is checked before any field is read, so a
    malformed message from a stranger cannot reach the parsing code at all.
    """
    if not raw:
        raise Rejected("empty message")
    try:
        doc = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (TypeError, ValueError):
        raise Rejected("message is not JSON")
    if not isinstance(doc, dict):
        raise Rejected("message is not an object")
    payload, mac = doc.get("p"), doc.get("mac")
    if not isinstance(payload, dict) or not isinstance(mac, str):
        raise Rejected("message has no signed payload")
    if not hmac.compare_digest(sign(auth, payload), mac):
        raise Rejected("signature does not match this home's pairing code")
    if payload.get("v") != VERSION:
        raise Rejected("message version %r is not supported" % (payload.get("v"),))
    try:
        stamp = int(payload.get("at"))
    except (TypeError, ValueError):
        raise Rejected("message has no timestamp")
    if stamp > now + MAX_CLOCK_SKEW_S:
        raise Rejected("message is timestamped in the future")
    device = str(payload.get("dev") or "")
    if own_device and device == _canonical(own_device):
        raise Rejected("own echo")
    items = payload.get("items")
    if not isinstance(items, list):
        raise Rejected("message carries no items")
    clean = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("k")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            continue
        try:
            position = int(item.get("pos"))
            duration = int(item.get("dur") or 0)
            at = int(item.get("at") or stamp)
        except (TypeError, ValueError):
            continue
        if position < MIN_POSITION_S or at > now + MAX_CLOCK_SKEW_S:
            continue
        if duration and position >= duration:
            continue
        clean.append({"k": key, "pos": position, "dur": duration, "at": at})
    return {"device": device, "at": stamp, "snapshot": bool(payload.get("snap")),
            "items": clean}


def merge(local: dict, incoming: dict) -> tuple[dict, list[dict]]:
    """Last write wins, per item, by the item's own timestamp.

    Returns the new state and only the entries that actually changed, because
    the caller writes each change into Kodi's library and shows a notice — and
    a notice for a position the television already had is noise the owner
    learns to ignore.
    """
    state = dict(local or {})
    changed = []
    for item in incoming.get("items", []):
        key = item["k"]
        current = state.get(key)
        if current and int(current.get("at", 0)) >= item["at"]:
            continue
        if current and int(current.get("pos", -1)) == item["pos"]:
            # Same position, newer stamp: remember the stamp, say nothing.
            state[key] = dict(item)
            continue
        state[key] = dict(item)
        changed.append(dict(item))
    return state, changed
