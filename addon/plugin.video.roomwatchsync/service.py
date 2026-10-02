"""Background progress uploader and Kodi library resume synchroniser."""

import os
import sys
import time

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import xbmc
import xbmcaddon
import xbmcgui

from api import ApiClient, ApiError
from media import progress_payload
import kodi_rpc


ADDON = xbmcaddon.Addon()
LOG_PREFIX = "[Room Watch Sync] "


def setting_int(name, default):
    try:
        return int(ADDON.getSetting(name) or default)
    except ValueError:
        return default


def notify(message_id):
    if ADDON.getSettingBool("notify_sync"):
        xbmcgui.Dialog().notification(ADDON.getAddonInfo("name"), ADDON.getLocalizedString(message_id), time=2500)


def log(message, level=xbmc.LOGINFO):
    xbmc.log(LOG_PREFIX + str(message), level)


def main():
    monitor = xbmc.Monitor()
    client = ApiClient(ADDON.getSetting("server_url"), ADDON.getSetting("api_token"))
    device_id = ADDON.getSetting("device_id") or "kodi"
    upload_interval = max(5, setting_int("sync_interval", 15))
    library_interval = max(1, setting_int("library_sync_minutes", 5)) * 60
    last_library_sync = 0
    previous_playing = False
    previous_payload = None
    first_library_sync = True
    warned_unconfigured = False

    while not monitor.abortRequested():
        server_url = ADDON.getSetting("server_url")
        api_token = ADDON.getSetting("api_token")
        if server_url.rstrip("/") != client.base_url or api_token.strip() != client.token:
            client = ApiClient(server_url, api_token)
        if not client.base_url or not client.token:
            if not warned_unconfigured:
                log("server URL or API token is not configured", xbmc.LOGWARNING)
                warned_unconfigured = True
            if monitor.waitForAbort(10):
                break
            continue
        warned_unconfigured = False
        now = time.monotonic()
        try:
            active = kodi_rpc.active_video()
            if active:
                item, properties = active
                payload = progress_payload(item, properties, device_id)
                client.save_progress(payload)
                previous_payload = payload
                previous_playing = True
            elif previous_playing and previous_payload:
                client.save_progress(previous_payload)
                previous_playing = False

            if now - last_library_sync >= library_interval:
                changed = kodi_rpc.sync_library(client.list_progress(limit=50, unfinished=True))
                last_library_sync = now
                if first_library_sync or changed:
                    notify(30101)
                first_library_sync = False
        except ApiError as exc:
            log("server error: %s" % exc, xbmc.LOGWARNING)
        except Exception as exc:  # Kodi services must survive malformed library entries.
            log("unexpected error: %s" % exc, xbmc.LOGERROR)

        if monitor.waitForAbort(upload_interval):
            break


if __name__ == "__main__":
    main()
