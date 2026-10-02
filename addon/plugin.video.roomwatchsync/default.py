"""The add-on's own screens: the first-run choice, and Continue Watching.

The first screen a person ever sees asks one question, because there are only
two answers: this is the first television (create a home, here is the code) or
it is not (type the code from the other one). Anything more — a server address,
a token, a port — is a question a person standing in front of a television with
a remote control cannot answer, and the whole design exists so that nobody has
to.

Three details that are the difference between working and almost working:

* **The code is shown in full, once, and can be shown again.** It is not a
  secret from the owner; it is a secret from everyone else. A code that can only
  be read at creation time is a code that gets written on a scrap of paper.
* **A typed code is normalised, not validated.** `pairing.normalize` already
  accepts lowercase, spaces, dashes, and the `O`/`l` a person types for `0`/`1`.
  A correct code that is refused looks exactly like a wrong one.
* **Joining does not touch the local library.** The replica fills from the
  household's own snapshot within seconds; writing anything before that would
  mean guessing.
"""

import os
import sys

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

import bus
import kodi_rpc
import media
import pairing
import state

ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else -1


def t(string_id):
    return ADDON.getLocalizedString(string_id)


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[Room Watch Sync] " + str(message), level)


def profile_dir():
    return xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))


def dialog():
    return xbmcgui.Dialog()


# --- pairing ------------------------------------------------------------

def show_code(code):
    """The code, the topic, and what not to do with either."""
    topic = pairing.topic(code)
    dialog().textviewer(
        t(30200),
        "%s\n\n%s\n\n%s\n\n%s\n%s" % (
            t(30201),
            pairing.format_code(code),
            t(30202),
            t(30203),
            topic,
        ))


def create_home():
    code = pairing.generate_code()
    ADDON.setSetting("home_code", code)
    show_code(code)
    return code


def join_home():
    while True:
        typed = dialog().input(t(30210), type=xbmcgui.INPUT_ALPHANUM)
        if not typed:
            return ""
        try:
            canonical = pairing.normalize(typed)
        except pairing.InvalidCode as exc:
            # The reason, not "invalid code": a person who is not told what is
            # wrong retypes the same thing.
            if not dialog().yesno(t(30211), "%s\n\n%s" % (str(exc), t(30212))):
                return ""
            continue
        ADDON.setSetting("home_code", pairing.format_code(canonical))
        dialog().ok(t(30213), t(30214))
        return canonical


def first_run():
    """Asked once, and asked again any time the add-on is opened unpaired."""
    choice = dialog().contextmenu([t(30220), t(30221)])
    if choice == 0:
        return create_home()
    if choice == 1:
        return join_home()
    return ""


def ensure_paired():
    code = ADDON.getSetting("home_code")
    if pairing.is_valid(code):
        return pairing.normalize(code)
    return first_run()


# --- continue watching --------------------------------------------------

def continue_watching(code):
    """The household's open items that THIS television can actually play.

    An item the replica knows about but the library does not is left out rather
    than listed: a row that cannot be played is a row that reports a bug.
    """
    store = state.Store(profile_dir(), log=log).load()
    secret = pairing.item_secret(code)
    try:
        index = kodi_rpc.library_index(secret)
    except Exception as exc:
        log("could not read the library: %s" % exc, xbmc.LOGWARNING)
        index = {}
    rewind = 20
    try:
        rewind = int(ADDON.getSetting("rewind_seconds") or 20)
    except (TypeError, ValueError):
        pass

    listed = 0
    for entry in store.entries():
        item = index.get(entry.get("k"))
        if not item:
            continue
        label = item.get("title") or item.get("label") or t(30230)
        if item.get("showtitle"):
            label = "%s — S%02dE%02d %s" % (
                item.get("showtitle"), int(item.get("season") or 0),
                int(item.get("episode") or 0), item.get("title") or "")
        minutes = max(0, int(entry.get("pos") or 0)) // 60
        listing = xbmcgui.ListItem(label="%s  (%d%s)" % (label, minutes, t(30231)))
        listing.setProperty("IsPlayable", "true")
        art = item.get("art") if isinstance(item.get("art"), dict) else {}
        if art:
            listing.setArt({"thumb": art.get("thumb", ""),
                            "poster": art.get("poster", "")})
        path = item.get("file") or ""
        resume = media.resume_seconds(entry.get("pos"), rewind)
        listing.setProperty("StartOffset", str(resume))
        info = listing.getVideoInfoTag()
        info.setResumePoint(float(resume), float(entry.get("dur") or 0))
        xbmcplugin.addDirectoryItem(HANDLE, path, listing, isFolder=False)
        listed += 1

    if not listed:
        empty = xbmcgui.ListItem(label=t(30232))
        xbmcplugin.addDirectoryItem(HANDLE, "", empty, isFolder=False)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def main():
    code = ensure_paired()
    if not code:
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    if "showcode" in sys.argv[2:] or (len(sys.argv) > 2 and "showcode" in sys.argv[2]):
        show_code(code)
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    if HANDLE >= 0:
        continue_watching(code)


if __name__ == "__main__":
    main()
