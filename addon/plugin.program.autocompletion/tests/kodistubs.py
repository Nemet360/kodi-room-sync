# SPDX-License-Identifier: GPL-2.0-or-later
"""Minimal fake `xbmc`/`xbmcaddon`/`xbmcvfs` so `lib/vendor/AutoCompletion.py`
can be imported outside Kodi.

That module reads addon settings and resolves paths at IMPORT time (module-
level `PLUGIN_ADDON = xbmcaddon.Addon(PLUGIN_ID)`), so a test cannot simply
mock a function call — the fakes have to exist in `sys.modules` before the
`import AutoCompletion` line runs. `install()` does exactly that and nothing
else: no pip install, no real Kodi, consistent with this repo's own
zero-third-party-dependency rule (the module docstring: "requests replaced
with stdlib urllib").

Call `install()` once at the top of a test module, before importing
AutoCompletion. `settings` is a plain dict a test can mutate directly to
change what `SETTING("autocomplete_lang")` returns mid-test.
"""
from __future__ import annotations

import sys
import types

settings: dict[str, str] = {"autocomplete_lang": "en", "autocomplete_provider": "Google"}


class _FakeAddon:
    def getSetting(self, key):
        return settings.get(key, "")

    def getAddonInfo(self, key):
        return {"path": "/addon", "id": "plugin.program.autocompletion",
                "profile": "/profile"}.get(key, "")


def _fake_xbmcaddon():
    module = types.ModuleType("xbmcaddon")
    module.Addon = lambda *a, **k: _FakeAddon()
    return module


def _fake_xbmcvfs():
    module = types.ModuleType("xbmcvfs")
    module.translatePath = lambda path: path
    module.exists = lambda path: False
    module.mkdirs = lambda path: None
    module.File = lambda *a, **k: None
    return module


def _fake_xbmc():
    module = types.ModuleType("xbmc")
    module.getCondVisibility = lambda condition: False
    module.log = lambda msg="", level=0: None
    module.LOGDEBUG = 0

    class _Monitor:
        def abortRequested(self):
            return False

        def waitForAbort(self, seconds):
            return False

    module.Monitor = _Monitor
    return module


def install():
    """Idempotent: safe to call from every test module that needs it."""
    for name, factory in (("xbmc", _fake_xbmc), ("xbmcaddon", _fake_xbmcaddon),
                          ("xbmcvfs", _fake_xbmcvfs)):
        if name not in sys.modules:
            sys.modules[name] = factory()
