import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "resources", "lib"))

from api import ApiClient


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ApiTests(unittest.TestCase):
    def test_authorization_and_progress_contract(self):
        seen = []

        def opener(request, timeout):
            seen.append(request)
            return Response({"items": [{"media_key": "movie:tmdb:1"}]})

        api = ApiClient("http://server/", "secret", opener=opener)
        items = api.list_progress()
        self.assertEqual("movie:tmdb:1", items[0]["media_key"])
        self.assertEqual("Bearer secret", seen[0].get_header("Authorization"))
        self.assertEqual("http://server/v1/progress?limit=50&unfinished=true", seen[0].full_url)

    def test_post_uses_expected_endpoint(self):
        seen = []

        def opener(request, timeout):
            seen.append(request)
            return Response({"ok": True})

        ApiClient("http://server", "secret", opener=opener).save_progress({"position_seconds": 12})
        self.assertEqual("POST", seen[0].method)
        self.assertEqual("http://server/v1/progress", seen[0].full_url)
        self.assertEqual(12, json.loads(seen[0].data)["position_seconds"])


if __name__ == "__main__":
    unittest.main()
