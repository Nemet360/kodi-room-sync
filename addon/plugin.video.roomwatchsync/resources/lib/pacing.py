"""What the service should do on this tick. Every rule, pure, `now` injected.

The service loop itself then has no judgement of its own, which is the only way
this part is testable at all — a Kodi service cannot be unit-tested, and timing
bugs here are the kind nobody notices until the free channel starts answering
429.

The budget is real: ntfy's anonymous limit is 60 requests per minute, shared by
every television in the house. So:

* **Idle televisions poll slowly** (`POLL_IDLE_S`, 60s). Three televisions at
  one poll a minute is 4,300 requests a day between them, which is nothing to
  ntfy and everything to responsiveness — a position written in the salon shows
  up in the bedroom within a minute.
* **A playing television polls almost never** (`POLL_PLAYING_S`, 600s). Nobody
  needs another room's progress while they are watching, and the one thing a
  poll could do mid-film — write a resume point — must not happen anyway.
* **Progress is published on a change, not on a clock.** `envelope`/`state`
  already refuse an unchanged position, so a film paused for an hour publishes
  once, not 240 times.
* **A snapshot is expensive** (five publishes, because of the delayed copies),
  so it happens on startup and then every `SNAPSHOT_EVERY_S` (6h) — never per
  item.
"""
from __future__ import annotations

POLL_IDLE_S = 60
POLL_PLAYING_S = 600
PUBLISH_PLAYING_S = 30
SNAPSHOT_EVERY_S = 6 * 3600

# Actions a tick can ask for. A tick can ask for several.
POLL = "poll"
PUBLISH = "publish"
SNAPSHOT = "snapshot"
REQUEST = "request"


class Pacer(object):
    def __init__(self, poll_idle=POLL_IDLE_S, poll_playing=POLL_PLAYING_S,
                 publish_playing=PUBLISH_PLAYING_S, snapshot_every=SNAPSHOT_EVERY_S):
        self.poll_idle = max(15, int(poll_idle))
        self.poll_playing = max(self.poll_idle, int(poll_playing))
        self.publish_playing = max(10, int(publish_playing))
        self.snapshot_every = max(600, int(snapshot_every))
        self.last_poll = None
        self.last_publish = None
        self.last_snapshot = None
        self.asked_for_state = False

    def plan(self, now, playing, dirty):
        """`dirty` means this television has progress nobody else has heard.

        On the very first tick the order matters: ask for the household's state
        *and* publish our own, because a television that only asks and is the
        only one switched on would wait forever for an answer that no one is
        there to give.
        """
        actions = []
        if not self.asked_for_state:
            actions.append(REQUEST)
            actions.append(SNAPSHOT)
            actions.append(POLL)
            return actions

        poll_after = self.poll_playing if playing else self.poll_idle
        if self.last_poll is None or now - self.last_poll >= poll_after:
            actions.append(POLL)

        if dirty:
            # While something is playing, hold back to one publish per window:
            # a seek-heavy viewer would otherwise publish on every scrub. When
            # playback has just stopped, publish at once — that is the moment
            # the other room is about to want.
            if not playing:
                actions.append(PUBLISH)
            elif (self.last_publish is None
                  or now - self.last_publish >= self.publish_playing):
                actions.append(PUBLISH)

        if self.last_snapshot is None or now - self.last_snapshot >= self.snapshot_every:
            if SNAPSHOT not in actions:
                actions.append(SNAPSHOT)
        return actions

    # --- what actually happened ------------------------------------------
    #
    # Recorded separately from `plan` so that an action which FAILED does not
    # reset its own clock. A publish that could not go out must be retried on
    # the next tick, not forgotten for thirty seconds.

    def polled(self, now):
        self.last_poll = now
        self.asked_for_state = True

    def published(self, now):
        self.last_publish = now

    def snapshotted(self, now):
        self.last_snapshot = now
        self.asked_for_state = True

    def requested(self, now):
        self.asked_for_state = True

    def wait(self, playing):
        """How long to sleep. Short while playing, because the moment playback
        stops is the moment the other room wants the position."""
        return 5 if playing else 15
