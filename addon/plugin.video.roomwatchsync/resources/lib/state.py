"""The local replica — the only place this project calls a source of truth.

Everything on the wire expires. ntfy keeps a message 12 hours, and even with
the delayed copies that is about three and a half days. So the network is a
transport, never a database: each television holds the household's whole state
on its own disk, and a message it never saw is a delay rather than a loss.

Four decisions, each from a way this file could ruin a library:

**The write is atomic.** A television loses power mid-film more often than any
other computer in the house. A half-written JSON file read on the next boot is
not "no state" — it is an exception in a service that then never starts, and
the owner sees an add-on that simply stopped working. Temp file plus
`os.replace`, which is atomic on Windows and POSIX alike.

**An unreadable file is treated as empty, and said so once.** The alternative
is a service that crashes on boot forever. The state is a cache of positions,
all of which the household will re-create by watching; losing it is an
annoyance, while a dead add-on is a bug report.

**Finished items are pruned, by age and by count.** Without a bound, this file
grows for the lifetime of the installation and so does every snapshot
published from it — the wire payload is the state. `PRUNE_AFTER_DAYS` is what
keeps a snapshot small enough that the free channel stays free.

**The last-seen cursor lives here too.** A service restarted every few hours by
a reboot must not re-read and re-apply a day of messages — that is a stream of
"synced" notices for positions the television already has, which is how an
owner learns to ignore the one notice that matters.

Pure but for the two file operations; `now` is injected everywhere.
"""
from __future__ import annotations

import json
import os
import time

import envelope

VERSION = 1
FILENAME = "watchsync-state.json"

# A resume point nobody touched in three months is not a resume point. This also
# bounds the snapshot that goes on the wire, because the snapshot *is* the state.
PRUNE_AFTER_DAYS = 90
# A hard ceiling as well as an age one: a library scan can add hundreds of items
# in a day, and the free channel has a payload size to respect.
MAX_ITEMS = 400


class Store(object):
    def __init__(self, directory, log=None, now=None):
        self.directory = directory
        self._log = log or (lambda message: None)
        self._now = now or time.time
        self.path = os.path.join(directory, FILENAME)
        self.items = {}
        self.cursor = ""
        self.device_id = ""
        self.loaded_cleanly = True

    # --- disk ------------------------------------------------------------

    def load(self):
        """Read the replica. A missing or corrupt file is an empty replica —
        never an exception out of here, because this runs at service start."""
        self.items, self.cursor, self.device_id = {}, "", ""
        self.loaded_cleanly = True
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                doc = json.load(handle)
        except (IOError, OSError):
            return self          # first run; not worth a word in the log
        except ValueError:
            self.loaded_cleanly = False
            self._log("room-watch-sync: local state was unreadable, starting over")
            return self
        if not isinstance(doc, dict) or doc.get("v") != VERSION:
            self.loaded_cleanly = False
            self._log("room-watch-sync: local state version is not supported, "
                      "starting over")
            return self
        items = doc.get("items")
        if isinstance(items, dict):
            for key, value in items.items():
                if isinstance(value, dict) and "pos" in value:
                    self.items[str(key)] = {
                        "k": str(key),
                        "pos": int(value.get("pos") or 0),
                        "dur": int(value.get("dur") or 0),
                        "at": int(value.get("at") or 0),
                    }
        self.cursor = str(doc.get("cursor") or "")
        self.device_id = str(doc.get("device_id") or "")
        return self

    def save(self):
        """Atomic: a television loses power mid-film, and a half-written file
        is an add-on that never starts again."""
        self.prune()
        doc = {"v": VERSION, "items": self.items, "cursor": self.cursor,
               "device_id": self.device_id, "saved_at": int(self._now())}
        temp = self.path + ".tmp"
        try:
            if not os.path.isdir(self.directory):
                os.makedirs(self.directory)
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(doc, handle, ensure_ascii=False)
            os.replace(temp, self.path)
        except (IOError, OSError, ValueError) as exc:
            # ValueError and not only OSError: a profile path with a stray null
            # or surrogate in it — which a hand-edited /homedir can produce —
            # raises ValueError out of os.makedirs, and an uncaught one here is
            # the add-on dying on a save rather than losing one.
            self._log("room-watch-sync: could not save local state (%s)"
                      % type(exc).__name__)
            try:
                os.remove(temp)
            except (OSError, ValueError):
                pass
            return False
        return True

    # --- content ---------------------------------------------------------

    def record(self, key, position, duration=0, at=None):
        """This television's own progress. Returns True when it changed
        something worth publishing — an unchanged position must not become a
        message, or the household's channel fills with its own echo."""
        at = int(at if at is not None else self._now())
        position, duration = int(position), int(duration or 0)
        if position < envelope.MIN_POSITION_S:
            return False
        if duration and position >= duration:
            # Finished. Drop the resume point rather than storing 99%, which
            # would otherwise be published and drag another television back.
            return self.items.pop(key, None) is not None
        current = self.items.get(key)
        if current and current["pos"] == position:
            return False
        self.items[key] = {"k": key, "pos": position, "dur": duration, "at": at}
        return True

    def apply(self, incoming):
        """Someone else's parsed message. Returns only what actually changed, so
        the caller writes to Kodi and notifies for real news only."""
        self.items, changed = envelope.merge(self.items, incoming)
        return changed

    def entries(self):
        """The whole replica as wire entries — this is what a snapshot is."""
        return [dict(item) for item in
                sorted(self.items.values(), key=lambda i: -int(i.get("at") or 0))]

    def prune(self):
        """By age first, then by count. Both bounds exist because the snapshot
        on the wire is this dictionary."""
        cutoff = int(self._now()) - PRUNE_AFTER_DAYS * 86400
        for key in [k for k, v in self.items.items()
                    if int(v.get("at") or 0) < cutoff]:
            del self.items[key]
        if len(self.items) > MAX_ITEMS:
            keep = sorted(self.items.values(),
                          key=lambda i: -int(i.get("at") or 0))[:MAX_ITEMS]
            self.items = {item["k"]: item for item in keep}
        return self

    # --- cursor ----------------------------------------------------------

    def seen(self, message_id):
        """Remember the last message acted on, so a reboot does not replay a
        day of positions as a stream of 'synced' notices."""
        if message_id:
            self.cursor = str(message_id)

    def since(self, default="12h"):
        """What to ask the channel for: everything after the last message this
        television acted on, or the default window on a first run."""
        return self.cursor or default
