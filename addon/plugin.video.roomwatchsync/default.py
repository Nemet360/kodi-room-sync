"""Kodi 'Continue watching' directory and playback resolver."""

import os
import sys
from urllib.parse import parse_qs, urlencode

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import xbmcaddon
import xbmcgui
import xbmcplugin

from api import ApiClient, ApiError
from media import resume_seconds
import kodi_rpc


ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]


def notify(message_id):
    xbmcgui.Dialog().notification(ADDON.getAddonInfo("name"), ADDON.getLocalizedString(message_id), time=3000)


def client():
    return ApiClient(ADDON.getSetting("server_url"), ADDON.getSetting("api_token"))


def configured():
    return bool(ADDON.getSetting("server_url").strip() and ADDON.getSetting("api_token").strip())


def item_label(remote):
    title = remote.get("title") or ""
    show = remote.get("show_title") or ""
    if remote.get("media_type") == "episode" and show:
        return "%s — S%02dE%02d — %s" % (
            show, int(remote.get("season") or 0), int(remote.get("episode") or 0), title,
        )
    return title


def show_directory():
    xbmcplugin.setPluginCategory(HANDLE, ADDON.getLocalizedString(30100))
    xbmcplugin.setContent(HANDLE, "videos")
    if not configured():
        notify(30102)
        xbmcplugin.endOfDirectory(HANDLE, succeeded=True)
        return
    try:
        remote_items = client().list_progress(limit=50, unfinished=True)
        local = kodi_rpc.library_index()
    except (ApiError, RuntimeError, ValueError):
        notify(30103)
        remote_items, local = [], {}

    for remote in remote_items:
        local_item = local.get(remote.get("media_key"))
        if not local_item and not remote.get("source_path"):
            continue
        list_item = xbmcgui.ListItem(label=item_label(remote))
        list_item.setProperty("IsPlayable", "true")
        list_item.setInfo("video", {
            "title": remote.get("title") or "",
            "tvshowtitle": remote.get("show_title") or "",
            "season": int(remote.get("season") or 0),
            "episode": int(remote.get("episode") or 0),
            "year": int(remote.get("year") or 0),
        })
        thumb = remote.get("thumbnail") or ((local_item or {}).get("art") or {}).get("thumb")
        if thumb:
            list_item.setArt({"thumb": thumb, "icon": thumb})
        url = BASE_URL + "?" + urlencode({"action": "play", "key": remote["media_key"]})
        xbmcplugin.addDirectoryItem(HANDLE, url, list_item, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE, succeeded=True)


def play(media_key):
    if not configured():
        notify(30102)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        remote = client().get_progress(media_key)
        local_item = kodi_rpc.library_index().get(media_key)
    except (ApiError, RuntimeError, ValueError):
        notify(30103)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    if not local_item and not remote.get("source_path"):
        notify(30104)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    list_item = xbmcgui.ListItem(path=(local_item or {}).get("file") or remote.get("source_path") or "")
    rewind = int(ADDON.getSetting("rewind_seconds") or 20)
    list_item.setProperty("StartOffset", str(resume_seconds(remote, rewind)))
    list_item.setProperty("IsPlayable", "true")
    xbmcplugin.setResolvedUrl(HANDLE, True, list_item)


def main():
    query = parse_qs(sys.argv[2][1:] if len(sys.argv) > 2 else "")
    if query.get("action", [""])[0] == "play":
        play(query.get("key", [""])[0])
    else:
        show_directory()


if __name__ == "__main__":
    main()
