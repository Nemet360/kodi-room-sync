"""The local replica. The only thing in this project that is a source of truth.

The failures tested here are the ones that look like "the add-on just stopped
working": a half-written file after a power cut, a replica that grows until the
snapshot is too big for the free channel, and a restart that replays a day of
positions as a stream of notices.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "resources", "lib"))

import envelope  # noqa: E402
import state  # noqa: E402


NOW = 1_790_000_000


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rws-")
        self.logs = []
        self.clock = [NOW]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def store(self):
        return state.Store(self.dir, log=self.logs.append,
                           now=lambda: self.clock[0])


class TestFirstRun(StoreCase):
    def test_a_missing_file_is_an_empty_replica_and_not_a_complaint(self):
        store = self.store().load()
        self.assertEqual(store.items, {})
        self.assertTrue(store.loaded_cleanly)
        self.assertEqual(self.logs, [])

    def test_saving_creates_the_directory_if_kodi_has_not_yet(self):
        nested = os.path.join(self.dir, "profile", "addon_data")
        store = state.Store(nested, log=self.logs.append, now=lambda: NOW)
        store.record("a" * 32, 600, 3600)
        self.assertTrue(store.save())
        self.assertTrue(os.path.isfile(os.path.join(nested, state.FILENAME)))


class TestDurability(StoreCase):
    def test_state_survives_a_restart(self):
        store = self.store().load()
        store.device_id = "salon"
        store.record("a" * 32, 1200, 3600)
        store.seen("msg-7")
        store.save()

        again = self.store().load()
        self.assertEqual(again.items["a" * 32]["pos"], 1200)
        self.assertEqual(again.cursor, "msg-7")
        self.assertEqual(again.device_id, "salon")

    def test_the_write_is_atomic_so_a_power_cut_cannot_truncate_it(self):
        """A television loses power mid-film more often than any other computer
        in the house, and a half-written file is an add-on that never starts."""
        store = self.store().load()
        store.record("a" * 32, 600, 3600)
        store.save()
        self.assertFalse(os.path.exists(store.path + ".tmp"))
        with open(store.path, "r", encoding="utf-8") as handle:
            json.load(handle)  # parses, or this raises

    def test_a_corrupt_file_starts_over_and_says_so_once(self):
        """The alternative is a service that crashes on every boot. The state
        is positions the household will re-create by watching."""
        with open(os.path.join(self.dir, state.FILENAME), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        store = self.store().load()
        self.assertEqual(store.items, {})
        self.assertFalse(store.loaded_cleanly)
        self.assertEqual(len(self.logs), 1)

    def test_a_future_version_starts_over_rather_than_guessing(self):
        with open(os.path.join(self.dir, state.FILENAME), "w",
                  encoding="utf-8") as handle:
            json.dump({"v": 99, "items": {"a" * 32: {"pos": 1}}}, handle)
        store = self.store().load()
        self.assertEqual(store.items, {})
        self.assertFalse(store.loaded_cleanly)

    def test_a_garbled_item_is_dropped_and_the_rest_survives(self):
        with open(os.path.join(self.dir, state.FILENAME), "w",
                  encoding="utf-8") as handle:
            json.dump({"v": state.VERSION, "items": {
                "a" * 32: {"pos": 500, "dur": 3600, "at": NOW},
                "b" * 32: "nonsense",
                "c" * 32: {"no-pos": True}}}, handle)
        store = self.store().load()
        self.assertEqual(list(store.items), ["a" * 32])

    def test_an_unwritable_directory_is_reported_not_raised(self):
        store = state.Store(os.path.join(self.dir, "x\0bad"),
                            log=self.logs.append, now=lambda: NOW)
        store.record("a" * 32, 600, 3600)
        self.assertFalse(store.save())
        self.assertTrue(self.logs)


class TestRecord(StoreCase):
    def test_an_unchanged_position_is_not_news(self):
        """An unchanged position must not become a message, or the household's
        channel fills with its own echo."""
        store = self.store().load()
        self.assertTrue(store.record("a" * 32, 600, 3600))
        self.assertFalse(store.record("a" * 32, 600, 3600))

    def test_the_first_few_seconds_are_not_a_resume_point(self):
        store = self.store().load()
        self.assertFalse(store.record("a" * 32, 3, 3600))
        self.assertEqual(store.items, {})

    def test_finishing_removes_the_resume_point_instead_of_storing_ninety_nine_percent(self):
        """Stored, it would be published, and another television would be
        dragged back to the last minute of something already finished."""
        store = self.store().load()
        store.record("a" * 32, 1200, 3600)
        self.assertTrue(store.record("a" * 32, 3600, 3600))
        self.assertNotIn("a" * 32, store.items)

    def test_finishing_something_never_started_is_not_news(self):
        store = self.store().load()
        self.assertFalse(store.record("a" * 32, 3600, 3600))

    def test_an_unknown_duration_still_records(self):
        """Kodi does not always know the length of a stream."""
        store = self.store().load()
        self.assertTrue(store.record("a" * 32, 600, 0))
        self.assertEqual(store.items["a" * 32]["pos"], 600)


class TestApply(StoreCase):
    def message(self, key, pos, at):
        return {"items": [{"k": key, "pos": pos, "dur": 3600, "at": at}]}

    def test_an_incoming_position_is_applied_and_returned_as_a_change(self):
        store = self.store().load()
        changed = store.apply(self.message("a" * 32, 900, NOW))
        self.assertEqual(len(changed), 1)
        self.assertEqual(store.items["a" * 32]["pos"], 900)

    def test_an_older_message_cannot_rewind_this_television(self):
        store = self.store().load()
        store.record("a" * 32, 1800, 3600, at=NOW + 100)
        self.assertEqual(store.apply(self.message("a" * 32, 60, NOW)), [])
        self.assertEqual(store.items["a" * 32]["pos"], 1800)

    def test_applying_an_identical_position_announces_nothing(self):
        store = self.store().load()
        store.record("a" * 32, 900, 3600, at=NOW)
        self.assertEqual(store.apply(self.message("a" * 32, 900, NOW + 50)), [])


class TestSnapshot(StoreCase):
    def test_the_snapshot_is_the_whole_replica_newest_first(self):
        store = self.store().load()
        store.record("a" * 32, 100, 3600, at=NOW)
        store.record("b" * 32, 200, 3600, at=NOW + 10)
        entries = store.entries()
        self.assertEqual([e["k"] for e in entries], ["b" * 32, "a" * 32])

    def test_the_snapshot_is_a_copy_so_a_caller_cannot_corrupt_the_replica(self):
        store = self.store().load()
        store.record("a" * 32, 100, 3600)
        store.entries()[0]["pos"] = 1
        self.assertEqual(store.items["a" * 32]["pos"], 100)

    def test_a_snapshot_entry_is_shaped_like_a_wire_entry(self):
        store = self.store().load()
        store.record("a" * 32, 100, 3600)
        built = envelope.build(b"k" * 32, "salon", store.entries(), NOW)
        parsed = envelope.parse(b"k" * 32, built, NOW)
        self.assertEqual(parsed["items"][0]["pos"], 100)


class TestPrune(StoreCase):
    def test_items_nobody_touched_for_months_are_dropped(self):
        """Unbounded, this file grows for the life of the installation — and the
        snapshot on the wire IS this file."""
        store = self.store().load()
        store.record("old", 100, 3600, at=NOW - (state.PRUNE_AFTER_DAYS + 1) * 86400)
        store.record("new", 100, 3600, at=NOW)
        store.prune()
        self.assertEqual(list(store.items), ["new"])

    def test_the_count_is_capped_keeping_the_most_recent(self):
        store = self.store().load()
        for index in range(state.MAX_ITEMS + 50):
            store.record("k%04d" % index, 100, 3600, at=NOW + index)
        store.prune()
        self.assertEqual(len(store.items), state.MAX_ITEMS)
        self.assertIn("k%04d" % (state.MAX_ITEMS + 49), store.items)
        self.assertNotIn("k0000", store.items)

    def test_saving_prunes_so_the_bound_cannot_be_forgotten(self):
        store = self.store().load()
        store.record("old", 100, 3600, at=NOW - 400 * 86400)
        store.save()
        self.assertEqual(self.store().load().items, {})


class TestCursor(StoreCase):
    def test_a_first_run_asks_for_the_default_window(self):
        self.assertEqual(self.store().load().since(), "12h")

    def test_after_a_restart_it_asks_only_for_what_came_later(self):
        """Otherwise every position the television already has arrives again as
        a notice, and the owner learns to ignore all of them."""
        store = self.store().load()
        store.seen("msg-12")
        store.save()
        self.assertEqual(self.store().load().since(), "msg-12")

    def test_an_empty_id_does_not_wipe_the_cursor(self):
        store = self.store().load()
        store.seen("msg-12")
        store.seen("")
        self.assertEqual(store.cursor, "msg-12")


if __name__ == "__main__":
    unittest.main()
