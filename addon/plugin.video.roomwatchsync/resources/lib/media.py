"""Pure functions shared by the Kodi UI and background service."""

import hashlib
import os
import re


def _text(value):
    return str(value or "").strip()


def _slug(value):
    return re.sub(r"\s+", " ", _text(value).lower())


def media_key(item):
    """Return a stable key, preferring provider IDs over local paths."""
    media_type = item.get("type") or item.get("media_type") or "video"
    unique = item.get("uniqueid") or {}
    if isinstance(unique, dict):
        for provider in ("imdb", "tmdb", "tvdb"):
            if unique.get(provider):
                return "%s:%s:%s" % (media_type, provider, unique[provider])
    if media_type == "episode":
        show = _slug(item.get("showtitle") or item.get("show_title"))
        season = int(item.get("season") or 0)
        episode = int(item.get("episode") or 0)
        if show and (season or episode):
            return "episode:show:%s:s%02de%02d" % (show, season, episode)
    if media_type == "movie":
        identity = "%s|%s" % (_slug(item.get("title")), int(item.get("year") or 0))
        return "movie:title:%s" % hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    filename = os.path.basename(_text(item.get("file") or item.get("source_path")))
    identity = "%s|%s|%s" % (media_type, _slug(item.get("title")), _slug(filename))
    return "%s:fallback:%s" % (media_type, hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24])


def seconds(kodi_time):
    if not isinstance(kodi_time, dict):
        return 0.0
    return float(
        int(kodi_time.get("hours", 0)) * 3600
        + int(kodi_time.get("minutes", 0)) * 60
        + int(kodi_time.get("seconds", 0))
        + int(kodi_time.get("milliseconds", 0)) / 1000.0
    )


def progress_payload(item, properties, device_id):
    position = seconds(properties.get("time"))
    duration = seconds(properties.get("totaltime"))
    completed = bool(duration and (position / duration >= 0.92 or duration - position <= 60))
    art = item.get("art") or {}
    thumbnail = art.get("thumb", "") if isinstance(art, dict) else ""
    media_type = item.get("type") or "video"
    if media_type not in ("movie", "episode", "video", "unknown"):
        media_type = "video"
    return {
        "media_key": media_key(item),
        "title": _text(item.get("title") or item.get("label")),
        "media_type": media_type,
        "show_title": _text(item.get("showtitle")),
        "season": int(item.get("season") or 0),
        "episode": int(item.get("episode") or 0),
        "year": int(item.get("year") or 0) or None,
        "source_path": _text(item.get("file")),
        "thumbnail": _text(thumbnail),
        "position_seconds": round(position, 3),
        "duration_seconds": round(duration, 3) if duration > 0 else None,
        "device_id": _text(device_id),
        "completed": completed,
    }


def resume_seconds(item, rewind=20):
    return max(0, int(float(item.get("position_seconds") or 0)) - max(0, int(rewind)))
