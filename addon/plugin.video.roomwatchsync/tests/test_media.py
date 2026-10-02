"""Kodi's dictionaries -> one identity.

The failure this file exists to prevent: the television that is *playing* a
file and the television that holds it in its *library* derive two different
keys for the same episode. Nothing errors, nothing logs, and the owner reports
that sync does not work.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "resources", "lib"))

import media  # noqa: E402

SECRET = b"s" * 32


class TestItemType(unittest.TestCase):
    def test_an_explicit_type_is_used(self):
        self.assertEqual(media.item_type({"type": "movie"}), "movie")
        self.assertEqual(media.item_type({"media_type": "episode"}), "episode")

    def test_a_showtitle_identifies_an_episode_with_no_type_field(self):
        """`Player.GetItem` on a playing episode does not always say `type`."""
        self.assertEqual(media.item_type({"showtitle": "Fargo"}), "episode")

    def test_a_library_id_identifies_the_type(self):
        self.assertEqual(media.item_type({"movieid": 7}), "movie")
        self.assertEqual(media.item_type({"episodeid": 7}), "episode")

    def test_anything_else_is_a_plain_video_rather_than_an_error(self):
        self.assertEqual(media.item_type({}), "video")


class TestIdentityAcrossKodiShapes(unittest.TestCase):
    def test_the_player_shape_and_the_library_shape_agree_on_a_movie(self):
        playing = {"title": "The Matrix", "year": 1999,
                   "uniqueid": {"imdb": "tt0133093"}}
        in_library = {"type": "movie", "movieid": 12, "label": "The Matrix",
                      "year": 1999, "uniqueid": {"imdb": "tt0133093"},
                      "resume": {"position": 0, "total": 0}}
        self.assertEqual(media.identity(playing), media.identity(in_library))

    def test_the_player_shape_and_the_library_shape_agree_on_an_episode(self):
        playing = {"showtitle": "The Sopranos", "season": 3, "episode": 8,
                   "title": "He Is Risen"}
        in_library = {"type": "episode", "episodeid": 40,
                      "showtitle": "the sopranos", "season": "3",
                      "episode": "8", "label": "He Is Risen"}
        self.assertEqual(media.identity(playing), media.identity(in_library))

    def test_a_label_stands_in_for_a_missing_title(self):
        self.assertEqual(media.identity({"type": "movie", "label": "Fargo",
                                         "year": 1996}),
                         media.identity({"type": "movie", "title": "Fargo",
                                         "year": 1996}))

    def test_a_non_dict_uniqueid_does_not_crash_the_service(self):
        """Kodi has been seen returning an empty list here."""
        self.assertTrue(media.identity({"type": "movie", "uniqueid": [],
                                        "title": "Fargo", "year": 1996}))

    def test_different_episodes_of_one_show_differ(self):
        base = {"type": "episode", "showtitle": "Fargo", "season": 1}
        self.assertNotEqual(media.identity(dict(base, episode=1)),
                            media.identity(dict(base, episode=2)))


class TestKey(unittest.TestCase):
    def test_the_key_is_the_opaque_wire_name(self):
        key = media.key(SECRET, {"type": "movie", "title": "Fargo", "year": 1996})
        self.assertRegex(key, r"^[0-9a-f]{32}$")
        self.assertNotIn("fargo", key)

    def test_two_shapes_of_the_same_item_produce_one_key(self):
        a = media.key(SECRET, {"title": "The Matrix",
                               "uniqueid": {"imdb": "tt0133093"}})
        b = media.key(SECRET, {"type": "movie", "movieid": 3, "label": "matrix",
                               "uniqueid": {"imdb": "tt0133093"}})
        self.assertEqual(a, b)


class TestSeconds(unittest.TestCase):
    def test_kodis_time_dictionary_becomes_seconds(self):
        self.assertEqual(media.seconds({"hours": 1, "minutes": 2, "seconds": 3,
                                        "milliseconds": 500}), 3723.5)

    def test_a_missing_or_wrong_shape_is_zero_rather_than_an_exception(self):
        self.assertEqual(media.seconds(None), 0.0)
        self.assertEqual(media.seconds("01:02:03"), 0.0)
        self.assertEqual(media.seconds({}), 0.0)

    def test_null_fields_are_tolerated(self):
        self.assertEqual(media.seconds({"hours": None, "minutes": 2,
                                        "seconds": None}), 120.0)


class TestResume(unittest.TestCase):
    def test_the_rewind_is_subtracted(self):
        """Somebody who stopped a film in another room lost the thread of the
        scene; resuming exactly where it stopped feels like a skip."""
        self.assertEqual(media.resume_seconds(600, 20), 580)

    def test_it_never_goes_below_zero(self):
        self.assertEqual(media.resume_seconds(5, 20), 0)

    def test_a_negative_rewind_is_treated_as_none(self):
        """The setting is a number a person typed."""
        self.assertEqual(media.resume_seconds(600, -30), 600)

    def test_no_rewind_resumes_exactly(self):
        self.assertEqual(media.resume_seconds(600, 0), 600)


class TestFinished(unittest.TestCase):
    def test_the_last_stretch_counts_as_watched(self):
        self.assertTrue(media.is_finished(3540, 3600))

    def test_the_last_minute_counts_as_watched_even_in_a_long_film(self):
        self.assertTrue(media.is_finished(10740, 10800))

    def test_the_middle_is_a_resume_point(self):
        self.assertFalse(media.is_finished(1800, 3600))

    def test_an_unknown_duration_is_never_finished(self):
        """Kodi does not always know the length of a stream, and guessing
        'finished' there would delete a real resume point."""
        self.assertFalse(media.is_finished(1800, 0))


if __name__ == "__main__":
    unittest.main()
