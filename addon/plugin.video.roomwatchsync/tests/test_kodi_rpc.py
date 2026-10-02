import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "resources", "lib"))

import kodi_rpc


class KodiRpcTests(unittest.TestCase):
    def test_set_movie_resume_uses_kodi_schema(self):
        calls = []
        original = kodi_rpc.call
        kodi_rpc.call = lambda method, params=None: calls.append((method, params)) or {}
        try:
            kodi_rpc.set_resume({"type": "movie", "movieid": 9}, 120.5, 600)
        finally:
            kodi_rpc.call = original
        self.assertEqual("VideoLibrary.SetMovieDetails", calls[0][0])
        self.assertEqual({"position": 120.5, "total": 600.0}, calls[0][1]["resume"])

    def test_set_episode_resume_uses_local_id(self):
        calls = []
        original = kodi_rpc.call
        kodi_rpc.call = lambda method, params=None: calls.append((method, params)) or {}
        try:
            kodi_rpc.set_resume({"type": "episode", "episodeid": 4}, 20, 100)
        finally:
            kodi_rpc.call = original
        self.assertEqual("VideoLibrary.SetEpisodeDetails", calls[0][0])
        self.assertEqual(4, calls[0][1]["episodeid"])


if __name__ == "__main__":
    unittest.main()
