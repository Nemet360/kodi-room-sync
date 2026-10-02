"""The wire. Tested against a recorded HTTP seam, not the network.

The failures that matter: a dead network that takes the television down with
it, a quiet channel that is indistinguishable from an unreachable one, a log
line carrying the household's topic, and a snapshot that is published only in
the form that expires in twelve hours.
"""
import json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "resources", "lib"))

import bus  # noqa: E402


TOPIC = "7K3M9QX2VTJ4HB5NPRSTVW"


class Recorder(object):
    """A fake `_http` that records calls and replays canned answers."""

    def __init__(self, answers=None, raises=None):
        self.calls = []
        self.answers = list(answers or [])
        self.raises = raises

    def __call__(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body,
                           "headers": dict(headers or {})})
        if self.raises:
            raise self.raises
        if self.answers:
            return self.answers.pop(0)
        return 200, ""


def ntfy(answers=None, raises=None, log=None):
    rec = Recorder(answers, raises)
    return bus.NtfyTransport(TOPIC, http=rec, log=log), rec


class TestPublish(unittest.TestCase):
    def test_a_publish_posts_the_body_to_the_topic(self):
        transport, rec = ntfy([(200, "{}")])
        self.assertTrue(transport.publish("hello"))
        self.assertEqual(rec.calls[0]["method"], "POST")
        self.assertTrue(rec.calls[0]["url"].endswith("/" + TOPIC))
        self.assertEqual(rec.calls[0]["body"], "hello")

    def test_a_dead_network_costs_the_sync_and_not_the_television(self):
        transport, _ = ntfy(raises=urllib.error.URLError("no route"))
        self.assertFalse(transport.publish("hello"))
        self.assertIn("publish", transport.last_error)

    def test_a_non_200_is_a_failure_with_the_status_in_the_reason(self):
        transport, _ = ntfy([(429, "rate limited")])
        self.assertFalse(transport.publish("hello"))
        self.assertIn("429", transport.last_error)

    def test_a_successful_publish_clears_the_previous_error(self):
        transport, _ = ntfy([(500, ""), (200, "{}")])
        transport.publish("a")
        self.assertTrue(transport.last_error)
        transport.publish("b")
        self.assertEqual(transport.last_error, "")


class TestSnapshotRetention(unittest.TestCase):
    def test_a_snapshot_is_also_published_as_future_copies(self):
        """12 hours of retention would lose a television switched off for a
        weekend. Measured: ntfy accepts Delay up to 3d and keeps a delivered
        message 12h, so four future copies cover ~3.5 days."""
        transport, rec = ntfy([(200, "{}")] * 5)
        self.assertTrue(transport.publish_snapshot("state"))
        self.assertEqual(len(rec.calls), 1 + len(bus.SNAPSHOT_DELAYS))
        delays = [c["headers"].get("Delay") for c in rec.calls]
        self.assertIsNone(delays[0])
        self.assertEqual(delays[1:], list(bus.SNAPSHOT_DELAYS))

    def test_no_delay_exceeds_what_ntfy_accepts(self):
        """`Delay: 7d` is refused with HTTP 400 — measured, not assumed."""
        units = {"h": 3600, "d": 86400, "m": 60}
        for delay in bus.SNAPSHOT_DELAYS:
            seconds = int(delay[:-1]) * units[delay[-1]]
            self.assertLessEqual(seconds, bus.MAX_DELAY_S)

    def test_the_live_copy_decides_the_result_not_the_future_ones(self):
        """A future copy that fails is a smaller problem than today's state not
        going out at all."""
        transport, _ = ntfy([(200, "{}")] + [(500, "")] * 4)
        self.assertTrue(transport.publish_snapshot("state"))

    def test_a_failed_live_copy_is_reported_as_a_failure(self):
        transport, _ = ntfy([(500, "")] + [(200, "{}")] * 4)
        self.assertFalse(transport.publish_snapshot("state"))

    def test_a_request_carries_no_household_data(self):
        transport, rec = ntfy([(200, "{}")])
        transport.request_snapshot()
        self.assertEqual(rec.calls[0]["body"], bus.REQUEST_MARKER)
        self.assertNotIn(TOPIC, rec.calls[0]["body"])


class TestPoll(unittest.TestCase):
    def line(self, message, event="message"):
        return json.dumps({"id": "x", "event": event, "topic": TOPIC,
                           "message": message})

    def test_poll_returns_message_bodies_oldest_first(self):
        text = "\n".join([self.line("one"), self.line("two")])
        transport, rec = ntfy([(200, text)])
        self.assertEqual([m["body"] for m in transport.poll()], ["one", "two"])
        self.assertIn("poll=1", rec.calls[0]["url"])
        self.assertIn("since=12h", rec.calls[0]["url"])

    def test_keepalive_and_open_frames_are_not_messages(self):
        """ntfy emits open/keepalive/poll_request frames on the same stream.
        Treating one as a message would feed garbage into the parser."""
        text = "\n".join([self.line("", "open"), self.line("real"),
                          self.line("", "keepalive")])
        transport, _ = ntfy([(200, text)])
        self.assertEqual([m["body"] for m in transport.poll()], ["real"])

    def test_a_malformed_line_is_skipped_not_fatal(self):
        text = "\n".join(["not json", self.line("real"), ""])
        transport, _ = ntfy([(200, text)])
        self.assertEqual([m["body"] for m in transport.poll()], ["real"])

    def test_each_message_carries_its_id_so_a_cursor_is_possible(self):
        """ntfy's `since` also accepts a message id. Without the id a restarted
        television can only re-ask for a whole window, and every position it
        already has arrives again as a notice."""
        transport, _ = ntfy([(200, self.line("real"))])
        self.assertEqual(transport.poll()[0]["id"], "x")

    def test_an_unreachable_channel_is_distinguishable_from_a_quiet_one(self):
        """Both return [] — the difference has to be somewhere, or 'nobody
        watched anything' and 'we could not look' collapse into each other."""
        quiet, _ = ntfy([(200, "")])
        self.assertEqual(quiet.poll(), [])
        self.assertEqual(quiet.last_error, "")

        broken, _ = ntfy(raises=urllib.error.URLError("down"))
        self.assertEqual(broken.poll(), [])
        self.assertIn("poll", broken.last_error)

    def test_the_since_window_is_passed_through(self):
        transport, rec = ntfy([(200, "")])
        transport.poll(since="all")
        self.assertIn("since=all", rec.calls[0]["url"])


class TestRedaction(unittest.TestCase):
    def test_redact_never_shows_enough_to_reuse(self):
        """A log attached to a support request is a documented way a capability
        leaks (W3C TAG, Good Practices for Capability URLs)."""
        shown = bus.redact(TOPIC)
        self.assertNotIn(TOPIC, shown)
        self.assertLessEqual(len(shown.split("…")[0]), 3)

    def test_redact_handles_nothing_at_all(self):
        self.assertEqual(bus.redact(""), "(none)")

    def test_no_log_line_contains_the_topic(self):
        lines = []
        transport, _ = ntfy(raises=urllib.error.URLError("down"),
                            log=lines.append)
        transport.publish("x")
        transport.poll()
        self.assertTrue(lines)
        for line in lines:
            self.assertNotIn(TOPIC, line)


class TestKeyValueBackend(unittest.TestCase):
    def test_a_put_sends_the_state_to_the_url(self):
        rec = Recorder([(200, "")])
        backend = bus.KeyValueBackend("https://example.invalid/home", http=rec)
        self.assertTrue(backend.publish("state"))
        self.assertEqual(rec.calls[0]["method"], "PUT")
        self.assertEqual(rec.calls[0]["body"], "state")

    def test_a_get_returns_the_stored_state(self):
        rec = Recorder([(200, "state")])
        backend = bus.KeyValueBackend("https://example.invalid/home", http=rec)
        self.assertEqual([m["body"] for m in backend.poll()], ["state"])

    def test_nothing_stored_yet_is_not_an_error(self):
        rec = Recorder([(404, "")])
        backend = bus.KeyValueBackend("https://example.invalid/home", http=rec)
        self.assertEqual(backend.poll(), [])
        self.assertEqual(backend.last_error, "")

    def test_an_unconfigured_url_says_so_instead_of_crashing(self):
        backend = bus.KeyValueBackend("", http=Recorder())
        self.assertFalse(backend.publish("x"))
        self.assertEqual(backend.poll(), [])
        self.assertIn("URL", backend.last_error)

    def test_a_delay_is_accepted_and_ignored_rather_than_raising(self):
        """A durable store has no expiry to work around, and the two backends
        must answer the same call the same way."""
        rec = Recorder([(200, "")])
        backend = bus.KeyValueBackend("https://example.invalid/home", http=rec)
        self.assertTrue(backend.publish("state", delay="3d"))
        self.assertNotIn("Delay", rec.calls[0]["headers"])

    def test_requesting_a_snapshot_is_a_no_op_that_succeeds(self):
        backend = bus.KeyValueBackend("https://example.invalid/home", http=Recorder())
        self.assertTrue(backend.request_snapshot())


class TestBuild(unittest.TestCase):
    def test_the_default_needs_no_setup(self):
        self.assertIsInstance(bus.build("", TOPIC), bus.NtfyTransport)

    def test_a_url_mode_selects_the_durable_backend(self):
        built = bus.build("url", TOPIC, url="https://example.invalid/home")
        self.assertIsInstance(built, bus.KeyValueBackend)

    def test_an_unknown_mode_falls_back_rather_than_raising(self):
        """The value comes off a settings screen a person typed into."""
        self.assertIsInstance(bus.build("banana", TOPIC), bus.NtfyTransport)
        self.assertIsInstance(bus.build(None, TOPIC), bus.NtfyTransport)

    def test_a_custom_ntfy_server_is_honoured(self):
        built = bus.build("ntfy", TOPIC, ntfy_server="https://ntfy.example.invalid/")
        self.assertEqual(built.server, "https://ntfy.example.invalid")


if __name__ == "__main__":
    unittest.main()
