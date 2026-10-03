# -*- coding: utf8 -*-

# Copyright (C) 2015 - Philipp Temminghoff <phil65@kodi.tv>
# This program is Free Software see LICENSE file for details

# Vendored from script.module.autocompletion (lib/AutoCompletion.py) so the
# plugin has no dependency on that module addon, per ADR-005. Adaptations:
#   - all addon paths/ids resolve against this plugin (the module addon is
#     not installed, so xbmcaddon.Addon(SCRIPT_ID) would raise on import)
#   - requests replaced with stdlib urllib (zero third-party dependencies)
#   - the common_<lang>.txt wordlists now ship in this plugin's resources/data

from abc import ABC, abstractmethod
from urllib.parse import quote_plus
import os
import time
import hashlib
import urllib.request
import json

import xbmc
import xbmcaddon
import xbmcvfs

PLUGIN_ID = "plugin.program.autocompletion"
PLUGIN_ADDON = xbmcaddon.Addon(PLUGIN_ID)
SETTING = PLUGIN_ADDON.getSetting
ADDON_PATH = xbmcvfs.translatePath(PLUGIN_ADDON.getAddonInfo("path"))
ADDON_ID = PLUGIN_ADDON.getAddonInfo("id")
ADDON_DATA_PATH = xbmcvfs.translatePath(PLUGIN_ADDON.getAddonInfo("profile"))


def get_autocomplete_items(search_str, limit=10, provider=None):
    """
    get dict list with autocomplete
    """
    if xbmc.getCondVisibility("System.HasHiddenInput"):
        return []

    language = resolve_language(search_str)
    if isinstance(language, list):
        return _get_multi_language_items(search_str, limit, language)

    provider = _build_provider(language, limit)
    return provider.get_predictions(search_str)


def _build_provider(language, limit):
    setting = SETTING("autocomplete_provider").lower()

    if setting == "youtube":
        provider = GoogleProvider(youtube=True, limit=limit, language=language)
    elif setting == "google":
        provider = GoogleProvider(limit=limit, language=language)
    elif setting == "bing":
        provider = BingProvider(limit=limit, language=language)
    elif setting == "tmdb":
        provider = TmdbProvider(limit=limit, language=language)
    else:
        provider = LocalDictProvider(limit=limit, language=language)
    provider.limit = limit
    return provider


def _get_multi_language_items(search_str, limit, languages):
    """Query the configured provider once per selected language and merge.

    Each language gets the full `limit` so a language near the end of the
    list is never starved by an earlier one filling the quota — the merge
    (not the per-language fetch) is where the cap is enforced. Order is
    preserved by first appearance, and a label already seen under an
    earlier language is dropped rather than shown twice.
    """
    seen = set()
    merged = []
    for lang in languages:
        provider = _build_provider(lang, limit)
        for item in provider.get_predictions(search_str):
            key = item.get("label")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged[: int(limit)]


def detect_language(text):
    """he/en from the query text alone: one Hebrew character (U+0590-U+05FF)
    makes it Hebrew. That block is the same one `prep_search_str` below
    already tests against (1488-1514 is its letter subset), reused here per
    the request rather than invented fresh. Digits and Latin punctuation sit
    outside the block, so neither affects the result; a mixed string with
    even one Hebrew character returns "he" — the detector runs on the whole
    query, not its first token, so "שובר bad" is Hebrew exactly like "שובר
    שורות" is.
    """
    for char in text or "":
        if "֐" <= char <= "׿":
            return "he"
    return "en"


def resolve_language(search_str):
    """The effective language for this one request.

    `autocomplete_lang` stays a fixed value for everyone who has not touched
    the new "auto"/"multi" options — existing installs keep behaving exactly
    as before. Picking "auto" turns per-query he/en detection on; picking
    "multi" returns a LIST of the languages chosen in `autocomplete_lang_multi`
    (a Kodi list[string] setting, stored comma-separated), which the caller
    queries and merges instead of picking just one. The result is returned
    per call rather than written back into any setting or global: the
    configured value needs to survive being read again next time unchanged,
    and a fixed value must never be overwritten by whichever language
    happened to be typed last.
    """
    configured = (SETTING("autocomplete_lang") or "").strip()
    lowered = configured.lower()
    if lowered == "auto":
        return detect_language(search_str)
    if lowered == "multi":
        raw = SETTING("autocomplete_lang_multi") or ""
        langs = [code.strip() for code in raw.split(",") if code.strip()]
        return langs or ["en"]
    return configured


def prep_search_str(text):
    for char in text:
        if 1488 <= ord(char) <= 1514:
            return text[::-1]
    return text


class BaseProvider(ABC):

    HEADERS = {'User-agent': 'Mozilla/5.0'}

    def __init__(self, *args, **kwargs):
        self.limit = kwargs.get("limit", 10)
        # `language` is passed per call by get_autocomplete_items (the already
        # resolved value, auto-detected or fixed). The SETTING() fallback is
        # for the provider classes' own stated contract of being constructible
        # on their own; nothing in this codebase instantiates one without it.
        self.language = kwargs.get("language") or SETTING("autocomplete_lang")

    @abstractmethod
    def build_url(self, query):
        pass

    def get_predictions(self, search_str):
        if not search_str:
            return []
        items = []
        result = self.fetch_data(search_str)
        for i, item in enumerate(result):
            li = {"label": item, "search_string": prep_search_str(item)}
            items.append(li)
            if i > int(self.limit):
                break
        return items

    def get_prediction_listitems(self, search_str):
        for item in self.get_predictions(search_str):
            li = {"label": item, "search_string": search_str}
            yield li

    def fetch_data(self, search_str):
        url = self.build_url(quote_plus(search_str))
        result = get_JSON_response(url=self.BASE_URL.format(endpoint=url), headers=self.HEADERS, folder=self.FOLDER)
        return self.process_result(result)

    def process_result(self, result):
        if not result or len(result) <= 1:
            return []
        else:
            return result[1] if isinstance(result[1], list) else result


class GoogleProvider(BaseProvider):

    BASE_URL = "http://clients1.google.com/complete/{endpoint}"
    FOLDER = "Google"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.youtube = kwargs.get("youtube", False)

    def build_url(self, query):
        url = f"search?hl={self.language}&q={query}&json=t&client=serp"
        if self.youtube:
            url += "&ds=yt"
        return url


class BingProvider(BaseProvider):

    BASE_URL = "http://api.bing.com/osjson.aspx?{endpoint}"
    FOLDER = "Bing"

    def __init__(self, *args, **kwargs):
        super(BingProvider, self).__init__(*args, **kwargs)

    def build_url(self, query):
        url = f"query={query}"
        return url


class TmdbProvider(BaseProvider):

    BASE_URL = "https://www.themoviedb.org/search/multi?{endpoint}"
    FOLDER = "TMDB"

    def __init__(self, *args, **kwargs):
        super(TmdbProvider, self).__init__(*args, **kwargs)

    def build_url(self, query):
        url = f"language={self.language}&query={query}"
        return url

    def process_result(self, result):
        if not result or not result.get("results"):
            return []
        out = []
        results = result.get("results")
        for i in results:
            title = None
            media_type = i.get("media_type")
            if media_type == "movie":
                title = i["title"]
            elif media_type in ["tv", "person"]:
                title = i["name"]
            else:
                title = i
            out.append(title)
        return out


class LocalDictProvider(BaseProvider):
    def __init__(self, *args, **kwargs):
        super(LocalDictProvider, self).__init__(*args, **kwargs)
        local = SETTING("autocomplete_lang_local")
        if local:
            self.language = local
        else:
            self.language = "en"

    def build_url(self, query):
        return super().build_url(query)

    def fetch_data(self, search_str):
        k = search_str.rfind(" ")
        if k >= 0:
            search_str = search_str[k + 1 :]

        path = os.path.join(ADDON_PATH, "resources", "data", f"common_{self.language}.txt")
        suggestions = []

        with xbmcvfs.File(path) as f:
            for line in f.read().split('\n'):
                if not line.startswith(search_str) or len(line) <= 2:
                    continue
                suggestions.append(line)
                if len(suggestions) > int(self.limit):
                    break

        return suggestions


def get_JSON_response(url="", cache_days=7.0, folder=False, headers=False):
    """
    get JSON response for *url, makes use of file cache.
    """
    now = time.time()
    hashed_url = hashlib.md5(url.encode("utf-8")).hexdigest()
    cache_path = xbmcvfs.translatePath(os.path.join(ADDON_DATA_PATH, folder) if folder else ADDON_DATA_PATH)
    path = os.path.join(cache_path, f"{hashed_url}.txt")
    cache_seconds = int(cache_days * 86400)
    results = []

    if xbmcvfs.exists(path) and ((now - os.path.getmtime(path)) < cache_seconds):
        results = read_from_file(path)
        log(f"loaded file for {url}. time: {float(time.time() - now)}")
    else:
        response = get_http(url, headers)
        try:
            results = json.loads(response)
            log(f"download {url}. time: {float(time.time() - now)}")
            save_to_file(results, hashed_url, cache_path)
        except Exception:
            log(f"Exception: Could not get new JSON data from {url}. Trying to fallback to cache")
            log(response)
            results = read_from_file(path)

    return results


def get_http(url, headers):
    """
    fetches data from *url, returns it as a string
    """
    succeed = 0
    monitor = xbmc.Monitor()
    while (succeed < 2) and (not monitor.abortRequested()):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception:
            log(f"get_http: could not get data from {url}")
            monitor.waitForAbort(1)
            succeed += 1
    return None


def read_from_file(path="", raw=False):
    """
    return data from file with *path
    """
    if not xbmcvfs.exists(path):
        return []

    try:
        with xbmcvfs.File(path) as f:
            log(f"opened textfile {path}.")
            if raw:
                return f.read()
            else:
                return json.load(f)
    except Exception:
        log(f"failed to load textfile: {path}")
        return []


def log(txt):
    message = f"{ADDON_ID}: {txt}"
    xbmc.log(msg=message, level=xbmc.LOGDEBUG)


def save_to_file(content, filename, path=""):
    """
    dump json and save to *filename in *path
    """
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)

    text_file_path = os.path.join(path, f"{filename}.txt")
    now = time.time()

    with xbmcvfs.File(text_file_path, "w") as text_file:
        json.dump(content, text_file)

    log(f"saved textfile {text_file_path}. Time: {float(time.time() - now)}")
    return True
