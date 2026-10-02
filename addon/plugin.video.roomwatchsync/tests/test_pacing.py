"""What the service does on each tick.

The bugs this guards against are the ones that only show up as a rate limit or
as a sync that is mysteriously slow: a publish on every scrub, a poll every
five seconds, a failed action that resets its own clock and is never retried.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "resources", "lib"))

import pacing  # noqa: E402


class TestFirstTick(unittest.TestCase):
    def setUp(self):
        self.pacer = pacing.Pacer()

    def test_the_first_tick_asks_publishes_and_polls(self):
        """A television that only asked, and happened to be the only one on,
        would wait forever for an answer nobody is there to give."""
        actions = self.pacer.plan(1000, playing=False, dirty=False)
        self.assertEqual(actions, [pacing.REQUEST, pacing.SNAPSHOT, pacing.POLL])

    def test_the_first_tick_happens_even_while_something_is_playing(self):
        actions = self.pacer.plan(1000, playing=True, dirty=False)
        self.assertIn(pacing.REQUEST, actions)

    def test_the_handshake_happens_once(self):
        self.pacer.plan(1000, playing=False, dirty=False)
        self.pacer.polled(1000)
        self.pacer.snapshotted(1000)
        self.assertNotIn(pacing.REQUEST,
                         self.pacer.plan(1100, playing=False, dirty=False))


class PacerCase(unittest.TestCase):
    def setUp(self):
        self.pacer = pacing.Pacer()
        self.pacer.plan(0, playing=False, dirty=False)
        self.pacer.polled(0)
        self.pacer.published(0)
        self.pacer.snapshotted(0)


class TestPolling(PacerCase):
    def test_an_idle_television_polls_about_once_a_minute(self):
        self.assertEqual(self.pacer.plan(30, False, False), [])
        self.assertIn(pacing.POLL, self.pacer.plan(60, False, False))

    def test_a_playing_television_polls_almost_never(self):
        """Nobody needs another room's progress mid-film, and the one thing a
        poll could do then — write a resume point — must not happen anyway."""
        self.assertEqual(self.pacer.plan(300, True, False), [])
        self.assertIn(pacing.POLL, self.pacer.plan(600, True, False))

    def test_a_failed_poll_is_retried_on_the_next_tick(self):
        """Recording the attempt rather than the success would hide a channel
        that is down behind a clock that keeps resetting."""
        self.assertIn(pacing.POLL, self.pacer.plan(60, False, False))
        self.assertIn(pacing.POLL, self.pacer.plan(61, False, False))

    def test_a_successful_poll_resets_the_clock(self):
        self.pacer.polled(60)
        self.assertNotIn(pacing.POLL, self.pacer.plan(61, False, False))


class TestPublishing(PacerCase):
    def test_nothing_is_published_when_nothing_changed(self):
        self.assertNotIn(pacing.PUBLISH, self.pacer.plan(10, False, False))

    def test_a_stop_publishes_at_once(self):
        """That is the exact moment the other room is about to want."""
        self.assertIn(pacing.PUBLISH, self.pacer.plan(1, False, True))

    def test_a_scrubbing_viewer_does_not_publish_on_every_seek(self):
        self.assertNotIn(pacing.PUBLISH, self.pacer.plan(5, True, True))
        self.assertNotIn(pacing.PUBLISH, self.pacer.plan(20, True, True))
        self.assertIn(pacing.PUBLISH, self.pacer.plan(30, True, True))

    def test_a_failed_publish_is_retried(self):
        self.assertIn(pacing.PUBLISH, self.pacer.plan(40, True, True))
        self.assertIn(pacing.PUBLISH, self.pacer.plan(41, True, True))

    def test_a_successful_publish_resets_the_window(self):
        self.pacer.published(40)
        self.assertNotIn(pacing.PUBLISH, self.pacer.plan(41, True, True))


class TestSnapshots(PacerCase):
    def test_a_snapshot_is_rare_because_it_costs_five_publishes(self):
        self.assertNotIn(pacing.SNAPSHOT, self.pacer.plan(3600, False, False))
        self.assertIn(pacing.SNAPSHOT,
                      self.pacer.plan(pacing.SNAPSHOT_EVERY_S, False, False))

    def test_a_snapshot_is_never_requested_twice_in_one_plan(self):
        pacer = pacing.Pacer()
        actions = pacer.plan(0, False, False)
        self.assertEqual(actions.count(pacing.SNAPSHOT), 1)


class TestSettings(unittest.TestCase):
    def test_absurd_settings_are_clamped_rather_than_obeyed(self):
        """These come off a settings screen a person typed into, and a one-second
        poll would get the household rate-limited within the hour."""
        pacer = pacing.Pacer(poll_idle=1, poll_playing=0, publish_playing=0,
                             snapshot_every=1)
        self.assertGreaterEqual(pacer.poll_idle, 15)
        self.assertGreaterEqual(pacer.poll_playing, pacer.poll_idle)
        self.assertGreaterEqual(pacer.publish_playing, 10)
        self.assertGreaterEqual(pacer.snapshot_every, 600)

    def test_the_wait_is_shorter_while_playing(self):
        pacer = pacing.Pacer()
        self.assertLess(pacer.wait(playing=True), pacer.wait(playing=False))

    def test_the_whole_budget_stays_well_inside_ntfys_anonymous_limit(self):
        """ntfy's anonymous limit is 60 requests a minute, shared by the house.
        Three idle televisions at this pace are one request every twenty
        seconds between them."""
        per_minute = 3 * (60.0 / pacing.POLL_IDLE_S)
        self.assertLess(per_minute, 10)


if __name__ == "__main__":
    unittest.main()
