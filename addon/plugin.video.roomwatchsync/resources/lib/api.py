"""Small dependency-free HTTP client for the Room Watch Sync API."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    """A network or server response error."""


class ApiClient:
    def __init__(self, base_url, token, timeout=8, opener=urlopen):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self._opener = opener

    def _request(self, method, path, body=None):
        if not self.base_url or not self.token:
            raise ApiError("server URL and API token are required")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "RoomWatchSync-Kodi/0.1.0",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except (HTTPError, URLError, OSError) as exc:
            raise ApiError(str(exc)) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError("invalid JSON response") from exc

    def save_progress(self, payload):
        return self._request("POST", "/v1/progress", payload)

    def list_progress(self, limit=50, unfinished=True):
        query = urlencode({
            "limit": int(limit),
            "unfinished": "true" if unfinished else "false",
        })
        response = self._request("GET", "/v1/progress?" + query)
        items = response.get("items", [])
        return items if isinstance(items, list) else []

    def get_progress(self, media_key):
        response = self._request("GET", "/v1/progress/" + quote(media_key, safe=""))
        item = response.get("item") if isinstance(response, dict) else None
        return item if isinstance(item, dict) else response
