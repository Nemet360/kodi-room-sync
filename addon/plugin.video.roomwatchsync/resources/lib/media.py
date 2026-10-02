"""Kodi's own dictionaries, turned into the two things this project needs.

Kodi hands back a different shape depending on which call produced it — a
playing item from `Player.GetItem`, a library row from `VideoLibrary.GetMovies`,
a list entry with `label` but no `title`. All of them have to reduce to the
*same* identity, or the television that is playing a file and the television
that holds it in its library derive two different keys for one episode. That
looks exactly like "sync does not work", and it produces no error anywhere.

So there is one function that reads a Kodi item, and it is the only place that
knows Kodi's field names. Everything downstream sees `envelope.item_identity`'s
structured arguments.

Pure: no Kodi imports, no network, no clock.
"""
from __future__ import annotations

import envelope


def _text(value):
    return str(value or "").strip()


def item_type(item):
    """Kodi says `type`, `media_type` or nothing at all, and an episode is
    sometimes only recognisable by carrying a `showtitle`."""
    kind = _text(item.get("type") or item.get("media_type")).lower()
    if kind in ("movie", "episode"):
        return kind
    if item.get("showtitle") or item.get("show_title"):
        return "episode"
    if item.get("movieid"):
        return "movie"
    if item.get("episodeid"):
        return "episode"
    return "video"


def identity(item):
    """The household-independent name of this piece of media."""
    unique = item.get("uniqueid")
    return envelope.item_identity(
        item_type(item),
        uniqueids=unique if isinstance(unique, dict) else {},
        title=item.get("title") or item.get("label"),
        year=item.get("year"),
        showtitle=item.get("showtitle") or item.get("show_title"),
        season=item.get("season"),
        episode=item.get("episode"),
    )


def key(secret, item):
    """The opaque key that travels, for one Kodi item."""
    return envelope.item_key(secret, identity(item))


def seconds(kodi_time):
    """Kodi reports time as {hours, minutes, seconds, milliseconds}."""
    if not isinstance(kodi_time, dict):
        return 0.0
    return float(
        int(kodi_time.get("hours", 0) or 0) * 3600
        + int(kodi_time.get("minutes", 0) or 0) * 60
        + int(kodi_time.get("seconds", 0) or 0)
        + int(kodi_time.get("milliseconds", 0) or 0) / 1000.0
    )


def resume_seconds(position, rewind=20):
    """Where to actually start playing.

    The rewind exists because a person who stopped a film in another room has
    lost the thread of the scene; starting exactly where the other television
    stopped feels like the film skipped. Never below zero, and never *past* the
    stored position.
    """
    return max(0, int(float(position or 0)) - max(0, int(rewind or 0)))


def is_finished(position, duration):
    """Kodi's own resume logic treats the last stretch as watched, and so must
    this: a position two seconds from the end is not a resume point, and
    publishing it would drag another television to the closing credits."""
    position, duration = float(position or 0), float(duration or 0)
    if duration <= 0:
        return False
    return position / duration >= 0.92 or duration - position <= 60
