"""What goes on the wire, and what a television is allowed to believe.

The tests that matter here are the negative ones: a title that leaked into a
message, an unsigned message that was acted on, a wrong clock that froze the
household's progress, and two televisions deriving different keys for the same
episode — which looks exactly like "sync does not work" and has no error.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "resources", "lib"))

import envelope  # noqa: E402
import pairing  # noqa: E402


CODE = pairing.generate_code()
SECRET = pairing.item_secret(CODE)
AUTH = pairing.auth_key(CODE)
NOW = 1_790_000_000


class TestIdentity(unittest.TestCase):
    def test_an_external_id_wins_over_the_title(self):
        """The only identity that survives a renamed file, a re-scrape and a
        different language's title."""
        with_id = envelope.item_identity("movie", uniqueids={"imdb": "tt0133093"},
                                         title="The Matrix", year=1999)
        renamed = envelope.item_identity("movie", uniqueids={"imdb": "tt0133093"},
                                         title="matrix, the", year=1998)
        self.assertEqual(with_id, renamed)

    def test_an_external_id_identifies_without_the_kind(self):
        """The kind is a guess — `Player.GetItem` on a playing movie often
        returns no `type` at all — while an imdb id is already globally unique.
        Including the kind made a playing item and the same item in the library
        derive two different keys, with no error anywhere."""
        self.assertEqual(
            envelope.item_identity("video", uniqueids={"imdb": "tt0133093"}),
            envelope.item_identity("movie", uniqueids={"imdb": "tt0133093"}))

    def test_titles_are_canonicalised_so_two_libraries_agree(self):
        a = envelope.item_identity("movie", title="The Office (US)", year=2005)
        b = envelope.item_identity("movie", title="the office  us", year=2005)
        self.assertEqual(a, b)

    def test_an_episode_is_identified_by_show_season_and_episode(self):
        a = envelope.item_identity("episode", showtitle="The Sopranos",
                                   season=3, episode=8)
        b = envelope.item_identity("episode", showtitle="the sopranos",
                                   season="3", episode="8")
        self.assertEqual(a, b)
        self.assertNotEqual(a, envelope.item_identity(
            "episode", showtitle="The Sopranos", season=3, episode=9))

    def test_a_movie_and_an_episode_never_collide(self):
        self.assertNotEqual(
            envelope.item_identity("movie", title="Fargo", year=1996),
            envelope.item_identity("episode", showtitle="Fargo", season=1, episode=1))

    def test_missing_everything_still_produces_a_key_rather_than_crashing(self):
        """A hand-added file has no ids at all. No sync is worse than fragile
        sync."""
        self.assertTrue(envelope.item_identity("movie"))


class TestItemKey(unittest.TestCase):
    def test_the_key_is_opaque_and_carries_no_title(self):
        identity = envelope.item_identity("episode", showtitle="The Sopranos",
                                          season=3, episode=8)
        key = envelope.item_key(SECRET, identity)
        self.assertRegex(key, r"^[0-9a-f]{32}$")
        for word in ("sopranos", "s03", "episode"):
            self.assertNotIn(word, key)

    def test_two_homes_derive_different_keys_for_the_same_episode(self):
        """Otherwise one household's opaque key is a lookup table for another's."""
        other = pairing.item_secret(pairing.generate_code())
        identity = envelope.item_identity("movie", uniqueids={"imdb": "tt0133093"})
        self.assertNotEqual(envelope.item_key(SECRET, identity),
                            envelope.item_key(other, identity))

    def test_the_same_home_derives_the_same_key_every_time(self):
        identity = envelope.item_identity("movie", uniqueids={"imdb": "tt0133093"})
        self.assertEqual(envelope.item_key(SECRET, identity),
                         envelope.item_key(pairing.item_secret(CODE), identity))


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.entries = [{"k": "a" * 32, "pos": 1200, "dur": 3600, "at": NOW}]

    def test_no_title_path_or_pairing_code_appears_on_the_wire(self):
        raw = envelope.build(AUTH, "salon", self.entries, NOW)
        for forbidden in (pairing.normalize(CODE), pairing.format_code(CODE),
                          "Sopranos", "/mnt", AUTH.hex(), SECRET.hex()):
            self.assertNotIn(forbidden.lower(), raw.lower())

    def test_a_message_carries_a_signature(self):
        doc = json.loads(envelope.build(AUTH, "salon", self.entries, NOW))
        self.assertIn("mac", doc)
        self.assertEqual(len(doc["mac"]), 64)

    def test_a_built_message_round_trips(self):
        raw = envelope.build(AUTH, "salon", self.entries, NOW)
        parsed = envelope.parse(AUTH, raw, NOW)
        self.assertEqual(parsed["items"], self.entries)
        self.assertEqual(parsed["device"], "salon")

    def test_the_snapshot_flag_survives(self):
        raw = envelope.build(AUTH, "salon", self.entries, NOW, snapshot=True)
        self.assertTrue(envelope.parse(AUTH, raw, NOW)["snapshot"])


class TestParse(unittest.TestCase):
    def setUp(self):
        self.entries = [{"k": "b" * 32, "pos": 600, "dur": 3600, "at": NOW}]
        self.raw = envelope.build(AUTH, "salon", self.entries, NOW)

    def test_a_message_from_another_home_is_refused(self):
        other = pairing.auth_key(pairing.generate_code())
        with self.assertRaises(envelope.Rejected) as caught:
            envelope.parse(other, self.raw, NOW)
        self.assertIn("signature", str(caught.exception))

    def test_a_tampered_position_is_refused(self):
        doc = json.loads(self.raw)
        doc["p"]["items"][0]["pos"] = 7
        with self.assertRaises(envelope.Rejected):
            envelope.parse(AUTH, json.dumps(doc), NOW)

    def test_an_unsigned_message_is_refused(self):
        payload = json.loads(self.raw)["p"]
        with self.assertRaises(envelope.Rejected):
            envelope.parse(AUTH, json.dumps({"p": payload}), NOW)

    def test_the_signature_is_checked_before_any_field_is_read(self):
        """A stranger's malformed message must not reach the parsing code."""
        bad = json.dumps({"p": {"v": "not-a-number", "items": "not-a-list"},
                          "mac": "0" * 64})
        with self.assertRaises(envelope.Rejected) as caught:
            envelope.parse(AUTH, bad, NOW)
        self.assertIn("signature", str(caught.exception))

    def test_a_message_from_the_future_is_refused(self):
        """A television whose clock is a month ahead would otherwise win
        last-write-wins forever and freeze the household at what it last saw."""
        future = envelope.build(AUTH, "salon", self.entries,
                                NOW + envelope.MAX_CLOCK_SKEW_S + 60)
        with self.assertRaises(envelope.Rejected) as caught:
            envelope.parse(AUTH, future, NOW)
        self.assertIn("future", str(caught.exception))

    def test_a_small_skew_is_tolerated(self):
        near = envelope.build(AUTH, "salon", self.entries, NOW + 60)
        self.assertEqual(len(envelope.parse(AUTH, near, NOW)["items"]), 1)

    def test_our_own_echo_is_dropped(self):
        with self.assertRaises(envelope.Rejected) as caught:
            envelope.parse(AUTH, self.raw, NOW, own_device="Salon")
        self.assertIn("echo", str(caught.exception))

    def test_an_unknown_version_is_refused_rather_than_guessed(self):
        payload = json.loads(self.raw)["p"]
        payload["v"] = 99
        raw = json.dumps({"p": payload, "mac": envelope.sign(AUTH, payload)})
        with self.assertRaises(envelope.Rejected) as caught:
            envelope.parse(AUTH, raw, NOW)
        self.assertIn("version", str(caught.exception))

    def test_every_rejection_names_a_reason(self):
        for raw in ("", "not json", "[]", json.dumps({"p": 1, "mac": "x"})):
            with self.assertRaises(envelope.Rejected) as caught:
                envelope.parse(AUTH, raw, NOW)
            self.assertGreater(len(str(caught.exception)), 5)

    def test_a_malformed_item_is_skipped_not_fatal(self):
        payload = json.loads(self.raw)["p"]
        payload["items"] = [{"k": "nope", "pos": 100},
                            {"k": "c" * 32, "pos": 100, "dur": 3600, "at": NOW},
                            "garbage"]
        raw = json.dumps({"p": payload, "mac": envelope.sign(AUTH, payload)})
        parsed = envelope.parse(AUTH, raw, NOW)
        self.assertEqual([i["k"] for i in parsed["items"]], ["c" * 32])

    def test_a_position_at_the_very_start_is_not_a_resume_point(self):
        payload = json.loads(self.raw)["p"]
        payload["items"] = [{"k": "d" * 32, "pos": 2, "dur": 3600, "at": NOW}]
        raw = json.dumps({"p": payload, "mac": envelope.sign(AUTH, payload)})
        self.assertEqual(envelope.parse(AUTH, raw, NOW)["items"], [])

    def test_a_finished_item_never_drags_another_television_back(self):
        payload = json.loads(self.raw)["p"]
        payload["items"] = [{"k": "e" * 32, "pos": 3600, "dur": 3600, "at": NOW}]
        raw = json.dumps({"p": payload, "mac": envelope.sign(AUTH, payload)})
        self.assertEqual(envelope.parse(AUTH, raw, NOW)["items"], [])


class TestMerge(unittest.TestCase):
    def incoming(self, key, pos, at):
        return {"items": [{"k": key, "pos": pos, "dur": 3600, "at": at}]}

    def test_a_newer_position_wins(self):
        state, changed = envelope.merge(
            {"k1": {"k": "k1", "pos": 100, "at": NOW}},
            self.incoming("k1", 900, NOW + 10))
        self.assertEqual(state["k1"]["pos"], 900)
        self.assertEqual(len(changed), 1)

    def test_an_older_message_never_rewinds_the_library(self):
        """Messages arrive out of order; the oldest must not be the last word."""
        state, changed = envelope.merge(
            {"k1": {"k": "k1", "pos": 900, "at": NOW + 10}},
            self.incoming("k1", 100, NOW))
        self.assertEqual(state["k1"]["pos"], 900)
        self.assertEqual(changed, [])

    def test_an_unchanged_position_is_recorded_but_not_announced(self):
        """A notice for a position the television already had is noise the owner
        learns to ignore — and then misses a real one."""
        state, changed = envelope.merge(
            {"k1": {"k": "k1", "pos": 900, "at": NOW}},
            self.incoming("k1", 900, NOW + 10))
        self.assertEqual(state["k1"]["at"], NOW + 10)
        self.assertEqual(changed, [])

    def test_a_new_item_is_added_and_announced(self):
        state, changed = envelope.merge({}, self.incoming("k9", 300, NOW))
        self.assertIn("k9", state)
        self.assertEqual(len(changed), 1)

    def test_merging_does_not_mutate_the_caller_state(self):
        local = {"k1": {"k": "k1", "pos": 100, "at": NOW}}
        envelope.merge(local, self.incoming("k1", 900, NOW + 10))
        self.assertEqual(local["k1"]["pos"], 100)


if __name__ == "__main__":
    unittest.main()
