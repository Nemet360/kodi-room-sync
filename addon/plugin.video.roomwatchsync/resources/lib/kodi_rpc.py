"""Kodi JSON-RPC. The only module that talks to Kodi, and it imports `xbmc`
lazily so every other module stays testable off a television.

`library_index` is keyed by the household's *opaque* key rather than by Kodi's
own ids, because that is the name an incoming message carries. The index is
built once per sweep: `VideoLibrary.GetMovies` over a real library is not free,
and calling it per incoming item would make a snapshot of fifty positions fifty
scans.
"""

import json

import media


def call(method, params=None):
    import xbmc

    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if "error" in response:
        raise RuntimeError("Kodi JSON-RPC error: %s" % response["error"])
    return response.get("result", {})


MOVIE_PROPERTIES = ["title", "year", "file", "uniqueid", "resume", "art", "playcount"]
EPISODE_PROPERTIES = [
    "title", "showtitle", "season", "episode", "year", "file", "uniqueid",
    "resume", "art", "playcount",
]
PLAYER_PROPERTIES = [
    "title", "showtitle", "season", "episode", "year", "file", "uniqueid", "art",
]


def library_items():
    movies = call("VideoLibrary.GetMovies", {
        "properties": MOVIE_PROPERTIES,
        "limits": {"start": 0, "end": 10000},
    }).get("movies", [])
    episodes = call("VideoLibrary.GetEpisodes", {
        "properties": EPISODE_PROPERTIES,
        "limits": {"start": 0, "end": 10000},
    }).get("episodes", [])
    for movie in movies:
        movie["type"] = "movie"
    for episode in episodes:
        episode["type"] = "episode"
    return movies + episodes


def library_index(secret):
    """Opaque key -> the local library row. Keyed the way the wire is."""
    return {media.key(secret, item): item for item in library_items()}


def active_video():
    players = call("Player.GetActivePlayers")
    player = next((p for p in players if p.get("type") == "video"), None)
    if not player:
        return None
    player_id = player["playerid"]
    item = call("Player.GetItem", {
        "playerid": player_id, "properties": PLAYER_PROPERTIES,
    }).get("item", {})
    properties = call("Player.GetProperties", {
        "playerid": player_id,
        "properties": ["time", "totaltime", "percentage", "speed"],
    })
    return item, properties


def set_resume(local_item, position, total):
    resume = {"position": float(position), "total": float(total)}
    if local_item.get("movieid"):
        return call("VideoLibrary.SetMovieDetails",
                    {"movieid": local_item["movieid"], "resume": resume})
    if local_item.get("episodeid"):
        return call("VideoLibrary.SetEpisodeDetails",
                    {"episodeid": local_item["episodeid"], "resume": resume})
    return None


def apply_entries(secret, entries, index=None):
    """Write incoming positions into this television's own library.

    Returns the rows that were actually written, so the caller can name what
    changed instead of announcing a sync that moved nothing.

    Two guards, both from the same principle — never make the library worse
    than it was:

    * An item this television does not have is skipped silently. You cannot
      resume a film you do not own, and there is nothing for the owner to do
      about it.
    * A difference under two seconds is not written. Kodi's own resume point
      drifts by a fraction of a second on every stop, and writing that back
      would mark the library modified on every single sweep.
    """
    index = library_index(secret) if index is None else index
    written = []
    for entry in entries or []:
        item = index.get(entry.get("k"))
        if not item:
            continue
        position = float(entry.get("pos") or 0)
        total = float(entry.get("dur") or 0)
        if total <= 0:
            total = float((item.get("resume") or {}).get("total") or 0)
        if total <= 0:
            continue  # a resume point with no total is not one Kodi can store
        local = float((item.get("resume") or {}).get("position") or 0)
        if abs(position - local) <= 2:
            continue
        set_resume(item, position, total)
        written.append(item)
    return written


def notify(heading, message, seconds=2500, icon=None):
    import xbmcgui

    dialog = xbmcgui.Dialog()
    if icon:
        dialog.notification(heading, message, icon, seconds)
    else:
        dialog.notification(heading, message, time=seconds)
