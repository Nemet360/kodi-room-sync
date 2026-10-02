"""The background service. Thin on purpose — every decision is in a pure module.

What is left here is only what needs a television: Kodi's player, Kodi's
library, the settings screen and the monitor that tells us to stop. Timing is
`pacing`, identity is `media`/`envelope`, the channel is `bus`, the state is
`state`. If something in this file looks like a rule, it is in the wrong place.

Two things are deliberate and easy to get wrong:

* **The item currently playing is never written back.** An incoming position for
  the film on this very screen would move the playhead under the viewer, or
  fight Kodi's own resume bookkeeping. It is kept in the replica and applied the
  next time that item is not playing.
* **Every outward call is fail-soft.** A household with no internet must keep a
  working television: the service logs, waits and tries again. It never raises
  out of the loop, because a Kodi service that dies stays dead until a restart.
"""

import os
import sys
import time

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import xbmc
import xbmcaddon
import xbmcvfs

import bus
import envelope
import kodi_rpc
import media
import pacing
import pairing
import state

ADDON = xbmcaddon.Addon()
LOG_PREFIX = "[Room Watch Sync] "


def log(message, level=xbmc.LOGINFO):
    xbmc.log(LOG_PREFIX + str(message), level)


def setting(name, default=""):
    try:
        return ADDON.getSetting(name) or default
    except Exception:  # a setting added in a newer version than the one installed
        return default


def setting_int(name, default):
    try:
        return int(setting(name, "") or default)
    except (TypeError, ValueError):
        return default


def setting_bool(name, default=True):
    value = setting(name, "").strip().lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    return default


def profile_dir():
    return xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))


def notify(message, seconds=2500):
    if setting_bool("notify_sync", True):
        try:
            kodi_rpc.notify(ADDON.getAddonInfo("name"), message, seconds)
        except Exception:
            pass  # a notification is never worth a broken sweep


class Service(object):
    def __init__(self):
        self.store = state.Store(profile_dir(), log=log).load()
        self.code = ""
        self.topic = ""
        self.secret = b""
        self.auth = b""
        self.transport = None
        self.pacer = pacing.Pacer(
            poll_idle=setting_int("poll_idle_seconds", pacing.POLL_IDLE_S),
            poll_playing=setting_int("poll_playing_seconds", pacing.POLL_PLAYING_S))
        self.dirty = False
        self.device = setting("device_id", "") or "kodi"
        self.playing_key = ""
        self.pending = []
        self.warned_unpaired = False

    # --- configuration ---------------------------------------------------

    def configure(self):
        """Re-read the settings. Returns False while the add-on is unpaired —
        which is a waiting state, not an error, and is said exactly once."""
        code = setting("home_code", "")
        if not pairing.is_valid(code):
            if not self.warned_unpaired:
                log("not paired yet: open the add-on and create or join a home")
                self.warned_unpaired = True
            self.transport = None
            return False
        self.warned_unpaired = False
        canonical = pairing.normalize(code)
        if canonical != self.code:
            self.code = canonical
            self.topic, self.secret, self.auth = pairing.derive(canonical)
            self.transport = None
            # A new home is a new household: the cursor belongs to the old
            # channel and would ask ntfy for a message id it has never seen.
            self.store.cursor = ""
        mode = setting("sync_mode", "ntfy")
        url = setting("sync_url", "")
        server = setting("ntfy_server", bus.DEFAULT_NTFY)
        if self.transport is None:
            self.transport = bus.build(mode, self.topic, server, url, log=log)
            log("channel ready (%s, topic %s)" % (mode, bus.redact(self.topic)))
        self.device = setting("device_id", "") or self.store.device_id or "kodi"
        if self.store.device_id != self.device:
            self.store.device_id = self.device
            self.store.save()
        return True

    # --- the wire --------------------------------------------------------

    def publish_progress(self, now):
        entries = [e for e in self.store.entries() if int(e.get("at") or 0) >= now - 3600]
        if not entries:
            self.dirty = False
            return True
        body = envelope.build(self.auth, self.device, entries, now)
        if self.transport.publish(body):
            self.dirty = False
            return True
        return False

    def publish_snapshot(self, now):
        body = envelope.build(self.auth, self.device, self.store.entries(), now,
                              snapshot=True)
        return self.transport.publish_snapshot(body)

    def poll(self, now):
        messages = self.transport.poll(since=self.store.since())
        if not messages and self.transport.last_error:
            return False
        answered = False
        for message in messages:
            body, message_id = message.get("body", ""), message.get("id", "")
            if message_id:
                self.store.seen(message_id)
            if body.strip() == bus.REQUEST_MARKER:
                # Another television has just started and is asking. Answering
                # with a live publish rather than a snapshot keeps this cheap:
                # the asker only needs what is current.
                if not answered:
                    self.transport.publish(
                        envelope.build(self.auth, self.device,
                                       self.store.entries(), now, snapshot=True))
                    answered = True
                continue
            try:
                parsed = envelope.parse(self.auth, body, now, own_device=self.device)
            except envelope.Rejected as exc:
                log("ignored a message: %s" % exc)
                continue
            changed = self.store.apply(parsed)
            if changed:
                self.pending.extend(changed)
        if messages:
            self.store.save()
        return True

    # --- Kodi ------------------------------------------------------------

    def watch_player(self, now):
        """Read this television's own progress into the replica."""
        try:
            active = kodi_rpc.active_video()
        except Exception as exc:
            log("could not read the player: %s" % exc, xbmc.LOGWARNING)
            return False
        if not active:
            self.playing_key = ""
            return False
        item, properties = active
        position = media.seconds(properties.get("time"))
        duration = media.seconds(properties.get("totaltime"))
        key = media.key(self.secret, item)
        self.playing_key = key
        if media.is_finished(position, duration):
            if self.store.record(key, int(duration), int(duration)):
                self.dirty = True
                self.store.save()
            return True
        if self.store.record(key, int(position), int(duration), at=int(now)):
            self.dirty = True
        return True

    def apply_pending(self):
        """Write what arrived into Kodi — except for whatever is on screen."""
        if not self.pending:
            return
        writable = [e for e in self.pending if e.get("k") != self.playing_key]
        if not writable:
            return
        try:
            written = kodi_rpc.apply_entries(self.secret, writable)
        except Exception as exc:
            log("could not update the library: %s" % exc, xbmc.LOGWARNING)
            return
        self.pending = [e for e in self.pending if e.get("k") == self.playing_key]
        if written:
            first = written[0]
            label = first.get("title") or first.get("label") or ""
            notify(ADDON.getLocalizedString(30101) + ((" — " + label) if label else ""))

    # --- the loop --------------------------------------------------------

    def tick(self, now):
        playing = self.watch_player(now)
        for action in self.pacer.plan(now, playing, self.dirty):
            if action == pacing.REQUEST:
                if self.transport.request_snapshot():
                    self.pacer.requested(now)
            elif action == pacing.POLL:
                if self.poll(now):
                    self.pacer.polled(now)
            elif action == pacing.PUBLISH:
                if self.publish_progress(now):
                    self.pacer.published(now)
                    self.store.save()
            elif action == pacing.SNAPSHOT:
                if self.publish_snapshot(now):
                    self.pacer.snapshotted(now)
        if not playing:
            self.apply_pending()
        return playing


def main():
    monitor = xbmc.Monitor()
    service = Service()
    log("service started")
    while not monitor.abortRequested():
        playing = False
        try:
            if service.configure():
                playing = service.tick(time.time())
        except Exception as exc:
            # A Kodi service that dies stays dead until the next restart, so
            # nothing is allowed out of this loop — not a malformed library row,
            # not a setting somebody typed a word into.
            log("unexpected error: %s" % exc, xbmc.LOGERROR)
        if monitor.waitForAbort(service.pacer.wait(playing)):
            break
    try:
        service.store.save()
    except Exception:
        pass
    log("service stopped")


if __name__ == "__main__":
    main()
