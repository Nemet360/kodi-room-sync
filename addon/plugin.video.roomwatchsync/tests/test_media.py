import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "resources", "lib"))

from media import media_key, progress_payload, resume_seconds, seconds


class MediaTests(unittest.TestCase):
    def test_provider_id_is_preferred(self):
        item = {"type": "movie", "uniqueid": {"imdb": "tt123"}, "file": "/a.mkv"}
        self.assertEqual("movie:imdb:tt123", media_key(item))

    def test_episode_fallback_is_cross_device_stable(self):
        item = {"type": "episode", "showtitle": "My Show", "season": 2, "episode": 3}
        self.assertEqual("episode:show:my show:s02e03", media_key(item))

    def test_seconds(self):
        self.assertEqual(3723.5, seconds({"hours": 1, "minutes": 2, "seconds": 3, "milliseconds": 500}))

    def test_payload_marks_nearly_finished(self):
        payload = progress_payload(
            {"type": "movie", "title": "X", "uniqueid": {"tmdb": "7"}},
            {"time": {"minutes": 94}, "totaltime": {"minutes": 100}},
            "bedroom",
        )
        self.assertTrue(payload["completed"])
        self.assertEqual("bedroom", payload["device_id"])

    def test_rewind_never_negative(self):
        self.assertEqual(0, resume_seconds({"position_seconds": 10}, 20))
        self.assertEqual(80, resume_seconds({"position_seconds": 100}, 20))


if __name__ == "__main__":
    unittest.main()
