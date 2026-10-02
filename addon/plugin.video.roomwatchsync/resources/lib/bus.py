"""The wire. Two backends, one interface, and no household data in any log.

There is no server in this project, so "where does the message live" had to be
answered out of what is actually free and needs no account. Everything was
measured rather than read off a documentation page, and most of the
documentation was wrong:

* `jsonblob.com` answers an anonymous POST with **403** (a Cloudflare block),
  though its own API page says "No login needed".
* `jsonstorage.net` answers with `{"error":"Create operation requires API
  key."}`, contradicting its own home page.
* GitHub cannot be written to at all: anonymous gist creation was removed in
  2018, and a token pushed into a public repository is revoked automatically.
* The whole category — kvdb, pantry, npoint, jsonbin, restdb — needs either an
  e-mail or a key, which a distributed add-on cannot carry.

`ntfy.sh` does work anonymously, measured: a POST returns 200 and
`GET /<topic>/json?poll=1&since=all` returns the message. Its own documentation
makes it the right shape for this: "there is no sign-up, the topic is
essentially a password, so pick something that's not easily guessable"
(https://docs.ntfy.sh/publish/) — and our topic is 130 derived bits.

**Retention is the one real weakness, and it is handled rather than hidden.**
A message lives 12 hours. A television switched off for a weekend would miss
everything, so three mechanisms stack:

1. Nothing on the wire is a source of truth — every television keeps the whole
   state locally, so a missed message is a delay, not a loss.
2. `REQUEST` / snapshot handshake: a television that has just started asks, and
   any television that is on answers with its whole state within seconds.
3. Measured: ntfy accepts `Delay` up to **3 days** (`7d` is refused with 400),
   and a delayed message is kept 12 hours *after* it is delivered. So a
   snapshot is also published as four future copies — +12h, +1d, +2d, +3d —
   each visible for 12 hours from its own delivery. That gives continuous
   recoverable state out to about three and a half days with no account and no
   server. A late copy can never overwrite newer data, because `envelope.merge`
   is last-write-wins on each item's own timestamp.

Past that, `KeyValueBackend` points at any URL that answers GET and PUT —
including the 30-line Cloudflare Worker in `server/worker.js`, which is free,
durable and deployed once with no machine to maintain.

**No log line ever contains the topic, the code or a title.** A support
request with a log attached is one of the documented ways a capability URL
leaks (W3C TAG, Good Practices for Capability URLs), and the topic is exactly
that. `redact` exists so there is one obvious way to mention the channel.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_NTFY = "https://ntfy.sh"
TIMEOUT_S = 15

# Measured against ntfy.sh: `Delay: 7d` is refused with 400, `3d` is accepted,
# and a delivered message is kept 12 hours. Four copies at these offsets give
# unbroken coverage from now to ~3.5 days.
SNAPSHOT_DELAYS = ("12h", "1d", "2d", "3d")
MAX_DELAY_S = 3 * 24 * 3600

# A television that has just started asks for the current state with this, and
# whoever is on answers with a snapshot. It carries no data of its own.
REQUEST_MARKER = "?rws-request"


def redact(value: str) -> str:
    """The only sanctioned way to mention a topic or a code in a log."""
    if not value:
        return "(none)"
    return "%s… (%d chars)" % (str(value)[:3], len(str(value)))


def _http(method: str, url: str, body=None, headers=None, timeout=TIMEOUT_S):
    data = body.encode("utf-8") if isinstance(body, str) else body
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


class Transport(object):
    """Every method is fail-soft: a dead network costs the sync, never the
    television. `publish` returns True/False, `poll` returns a list — never
    None, so a caller cannot mistake "could not look" for "nothing arrived"
    by accident; `last_error` is where the difference is recorded."""

    def __init__(self, http=_http, log=None):
        self._http = http
        self._log = log or (lambda message: None)
        self.last_error = ""

    def _failed(self, what: str, exc: Exception) -> None:
        self.last_error = "%s: %s" % (what, exc)
        self._log("room-watch-sync: %s failed (%s)" % (what, type(exc).__name__))


class NtfyTransport(Transport):
    def __init__(self, topic: str, server: str = DEFAULT_NTFY, http=_http, log=None):
        Transport.__init__(self, http=http, log=log)
        self.topic = topic
        self.server = (server or DEFAULT_NTFY).rstrip("/")

    def _url(self, suffix: str = "") -> str:
        return "%s/%s%s" % (self.server, self.topic, suffix)

    def publish(self, body: str, delay: str = "") -> bool:
        headers = {"Content-Type": "text/plain"}
        if delay:
            headers["Delay"] = delay
        try:
            status, _ = self._http("POST", self._url(), body, headers)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._failed("publish", exc)
            return False
        if status != 200:
            self.last_error = "publish returned HTTP %s" % status
            self._log("room-watch-sync: publish returned HTTP %s" % status)
            return False
        self.last_error = ""
        return True

    def publish_snapshot(self, body: str) -> bool:
        """Now, plus one future copy per delay offset.

        The future copies are what make a television that was off for two days
        able to catch up at all; the immediate one is what makes the normal
        case instant. A failure of a future copy is not a failure of the
        snapshot — the live one is the one that matters today.
        """
        live = self.publish(body)
        for delay in SNAPSHOT_DELAYS:
            self.publish(body, delay=delay)
        return live

    def request_snapshot(self) -> bool:
        return self.publish(REQUEST_MARKER)

    def poll(self, since: str = "12h") -> list:
        """Messages newer than `since`, oldest first, as `{"id", "body"}`.

        The id is carried because `since` also accepts a message id — that is
        what lets a television restarted by a reboot ask for "everything after
        the last message I acted on" instead of replaying a whole window as a
        stream of notices for positions it already has.

        `[]` on any failure — check `last_error` to tell that from a quiet
        channel.
        """
        url = self._url("/json?poll=1&since=%s" % (since or "12h"))
        try:
            status, text = self._http("GET", url, None, {"Accept": "application/json"})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._failed("poll", exc)
            return []
        if status != 200:
            self.last_error = "poll returned HTTP %s" % status
            return []
        self.last_error = ""
        messages = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if doc.get("event") != "message":
                continue  # ntfy also emits open/keepalive/poll_request frames
            body = doc.get("message")
            if isinstance(body, str) and body:
                messages.append({"id": str(doc.get("id") or ""), "body": body})
        return messages


class KeyValueBackend(Transport):
    """Any URL that answers GET and PUT with a JSON body — the durable option.

    `server/worker.js` is one such URL and is free to run, but so is a NAS, a
    Raspberry Pi or the project's own FastAPI server. This backend exists
    because 3.5 days of ntfy retention is a real limit, not because anybody
    must deploy anything.
    """

    def __init__(self, url: str, http=_http, log=None):
        Transport.__init__(self, http=http, log=log)
        self.url = (url or "").rstrip("/")

    def publish(self, body: str, delay: str = "") -> bool:
        """`delay` is accepted and ignored: a durable store has no expiry to
        work around, and raising here would make the two backends behave
        differently for the same call."""
        if not self.url:
            self.last_error = "no sync URL configured"
            return False
        try:
            status, _ = self._http("PUT", self.url, body,
                                   {"Content-Type": "application/json"})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._failed("put", exc)
            return False
        if status not in (200, 201, 204):
            self.last_error = "put returned HTTP %s" % status
            return False
        self.last_error = ""
        return True

    publish_snapshot = publish

    def request_snapshot(self) -> bool:
        """Nothing to ask: the state is already there to be read."""
        return True

    def poll(self, since: str = "") -> list:
        if not self.url:
            self.last_error = "no sync URL configured"
            return []
        try:
            status, text = self._http("GET", self.url, None,
                                      {"Accept": "application/json"})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._failed("get", exc)
            return []
        if status == 404:
            self.last_error = ""
            return []  # nothing stored yet is not an error
        if status != 200:
            self.last_error = "get returned HTTP %s" % status
            return []
        self.last_error = ""
        # No ids here: a durable store holds one current document rather than a
        # stream, so there is no cursor to keep and nothing to resume from.
        return [{"id": "", "body": text}] if text.strip() else []


def build(mode: str, topic: str, ntfy_server: str = DEFAULT_NTFY,
          url: str = "", http=_http, log=None) -> Transport:
    """`mode` is read off a settings screen, so an unknown value must not raise
    — it falls back to the backend that needs no setup at all."""
    if str(mode or "").strip().lower() in ("url", "kv", "server", "custom"):
        return KeyValueBackend(url, http=http, log=log)
    return NtfyTransport(topic, ntfy_server, http=http, log=log)
