"""Kodi JSON-RPC helpers. Imports Kodi modules only at runtime."""

import json

from media import media_key


def call(method, params=None):
    import xbmc

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if "error" in response:
        raise RuntimeError("Kodi JSON-RPC error: %s" % response["error"])
    return response.get("result", {})


MOVIE_PROPERTIES = ["title", "year", "file", "uniqueid", "resume", "art"]
EPISODE_PROPERTIES = [
    "title", "showtitle", "season", "episode", "year", "file", "uniqueid",
    "resume", "art",
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


def library_index():
    return {media_key(item): item for item in library_items()}


def active_video():
    players = call("Player.GetActivePlayers")
    player = next((p for p in players if p.get("type") == "video"), None)
    if not player:
        return None
    player_id = player["playerid"]
    item = call("Player.GetItem", {
        "playerid": player_id,
        "properties": [
            "title", "showtitle", "season", "episode", "year", "file",
            "uniqueid", "art",
        ],
    }).get("item", {})
    properties = call("Player.GetProperties", {
        "playerid": player_id,
        "properties": ["time", "totaltime", "percentage", "speed"],
    })
    return item, properties


def set_resume(local_item, position, total):
    resume = {"position": float(position), "total": float(total)}
    if local_item.get("type") == "movie":
        return call("VideoLibrary.SetMovieDetails", {
            "movieid": local_item["movieid"], "resume": resume,
        })
    return call("VideoLibrary.SetEpisodeDetails", {
        "episodeid": local_item["episodeid"], "resume": resume,
    })


def sync_library(remote_items):
    local = library_index()
    changed = 0
    for remote in remote_items:
        item = local.get(remote.get("media_key"))
        if not item:
            continue
        remote_position = float(remote.get("position_seconds") or 0)
        remote_total = float(remote.get("duration_seconds") or 0)
        local_position = float((item.get("resume") or {}).get("position") or 0)
        if remote_total > 0 and abs(remote_position - local_position) > 2:
            set_resume(item, remote_position, remote_total)
            changed += 1
    return changed
