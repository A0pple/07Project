"""Core scanning logic for the Red Link and Short Article scanners.

This module contains the networking utilities, caches and worker thread
implementations that were previously used by the Tkinter desktop
application.  The classes defined here are UI agnostic which allows them to
be reused both by the web application as well as by any potential command
line interfaces or automated scripts.
"""

from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
from collections import Counter, OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# --------------------------- Constants & Settings ---------------------------- #

API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "AutopediaRedLinkScanner/1.1 (+https://autopedia.local)"
REQUEST_TIMEOUT = 15  # seconds
DEFAULT_RPM = 70.0  # requests per minute cap (all HTTP calls combined)
MAX_RETRIES = 3
BACKOFF_BASE = 1.5
CACHE_TTL_SECONDS = 60 * 30  # 30 min TTL for caches
CACHE_SIZES = dict(links=2000, search=4000)


# --------------------------- Tiny LRU Cache w/ TTL --------------------------- #


@dataclass
class _CacheEntry:
    value: object
    expires_at: float


class TTLRU:
    """Thread-safe LRU cache with a time-to-live for each entry."""

    def __init__(self, capacity: int, ttl_seconds: float) -> None:
        self.capacity = capacity
        self.ttl = ttl_seconds
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[object]:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            if entry.expires_at < now:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return entry.value

    def set(self, key: str, value: object) -> None:
        now = time.time()
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = _CacheEntry(value=value, expires_at=now + self.ttl)
            if len(self._store) > self.capacity:
                self._store.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# Global caches (thread-safe)
LINKS_CACHE = TTLRU(CACHE_SIZES["links"], CACHE_TTL_SECONDS)  # page_title -> [red links]
SEARCH_CACHE = TTLRU(CACHE_SIZES["search"], CACHE_TTL_SECONDS)  # query -> count int|None


# --------------------------- Title Classification --------------------------- #


CATEGORY_PERSON = "Person"
CATEGORY_PLACE = "Place"
CATEGORY_ORGANIZATION = "Organization"
CATEGORY_WORK = "Work"
CATEGORY_EVENT = "Event"
CATEGORY_CONCEPT = "Concept"
CATEGORY_OTHER = "Other"

CATEGORIES_LEGACY_MAP = {
    "Individual": CATEGORY_PERSON,
    "General": CATEGORY_OTHER,
}

CATEGORY_DISPLAY_ORDER = [
    CATEGORY_PERSON,
    CATEGORY_PLACE,
    CATEGORY_ORGANIZATION,
    CATEGORY_WORK,
    CATEGORY_EVENT,
    CATEGORY_CONCEPT,
    CATEGORY_OTHER,
]

DEFAULT_CATEGORY = CATEGORY_OTHER

NAME_PARTICLES = {
    "al",
    "bin",
    "da",
    "de",
    "del",
    "della",
    "der",
    "di",
    "du",
    "el",
    "ibn",
    "la",
    "le",
    "mac",
    "mc",
    "saint",
    "st",
    "st.",
    "van",
    "von",
}

PERSON_DISQUALIFYING_FIRST_WORDS = {
    "new",
    "old",
    "north",
    "south",
    "east",
    "west",
    "upper",
    "lower",
    "the",
}

PERSON_SUFFIXES = {
    "ii",
    "iii",
    "iv",
    "jr",
    "junior",
    "md",
    "m.d",
    "obe",
    "oc",
    "phd",
    "qc",
    "senior",
    "sr",
    "v",
}

PLACE_PREFIXES = {
    "al",
    "cape",
    "fort",
    "lake",
    "loch",
    "mount",
    "mt",
    "port",
    "rio",
    "san",
    "santa",
    "santo",
    "saint",
    "st",
    "st.",
}

PLACE_TOKENS = {
    "airport",
    "archipelago",
    "atoll",
    "avenue",
    "bay",
    "beach",
    "borough",
    "bridge",
    "canal",
    "canyon",
    "cape",
    "castle",
    "cathedral",
    "cave",
    "cemetery",
    "city",
    "coast",
    "colony",
    "county",
    "creek",
    "dam",
    "delta",
    "desert",
    "district",
    "expressway",
    "falls",
    "fjord",
    "forest",
    "garden",
    "glacier",
    "gorge",
    "gulf",
    "harbor",
    "harbour",
    "haven",
    "highway",
    "hill",
    "hills",
    "island",
    "islands",
    "lagoon",
    "lake",
    "lakes",
    "metropolitan",
    "monument",
    "mountain",
    "mountains",
    "municipality",
    "ocean",
    "park",
    "parkway",
    "peninsula",
    "plain",
    "plateau",
    "prefecture",
    "province",
    "range",
    "reef",
    "region",
    "reservoir",
    "ridge",
    "river",
    "road",
    "route",
    "sea",
    "shore",
    "square",
    "state",
    "station",
    "stream",
    "street",
    "suburb",
    "summit",
    "territory",
    "town",
    "township",
    "trail",
    "tunnel",
    "valley",
    "village",
    "waterfall",
    "watershed",
    "way",
    "zoo",
}

PLACE_SUFFIX_ENDINGS = (
    "shire",
    "stan",
    "land",
    "lands",
    "ton",
    "ville",
    "burg",
    "burgh",
    "holm",
    "mouth",
    "ness",
    "port",
    "stead",
    "vale",
    "wood",
    "woods",
)

PLACE_PHRASES = {
    "autonomous region",
    "bay of",
    "borough of",
    "city of",
    "county of",
    "federal district",
    "gulf of",
    "island of",
    "lake of",
    "metropolitan area",
    "mountain range",
    "national forest",
    "national park",
    "natural park",
    "nature reserve",
    "province of",
    "river of",
    "state park",
    "state of",
    "strait of",
    "territory of",
    "wildlife refuge",
}

PLACE_PAREN_HINTS = {
    "airport",
    "archipelago",
    "bay",
    "bridge",
    "census-designated place",
    "census tract",
    "city",
    "commune",
    "county",
    "district",
    "hamlet",
    "island",
    "lake",
    "municipality",
    "neighborhood",
    "neighbourhood",
    "park",
    "peninsula",
    "province",
    "region",
    "river",
    "settlement",
    "state",
    "station",
    "suburb",
    "territory",
    "town",
    "township",
    "valley",
    "village",
}

ORGANIZATION_SUFFIXES = {
    "academy",
    "agency",
    "alliance",
    "association",
    "authority",
    "bank",
    "ballet",
    "board",
    "church",
    "club",
    "college",
    "commission",
    "committee",
    "company",
    "congregation",
    "consortium",
    "corporation",
    "council",
    "department",
    "division",
    "enterprise",
    "federation",
    "foundation",
    "group",
    "hospital",
    "inc",
    "inc.",
    "institute",
    "institution",
    "laboratories",
    "laboratory",
    "league",
    "limited",
    "ltd",
    "ltd.",
    "ministry",
    "museum",
    "network",
    "office",
    "organization",
    "organisation",
    "orchestra",
    "party",
    "philharmonic",
    "press",
    "productions",
    "recordings",
    "records",
    "school",
    "society",
    "studio",
    "studios",
    "team",
    "trust",
    "union",
    "university",
}

ORGANIZATION_TOKENS = {
    "academy",
    "agency",
    "airlines",
    "airways",
    "alliance",
    "association",
    "athletic",
    "bank",
    "ballet",
    "brigade",
    "broadcasting",
    "club",
    "college",
    "company",
    "companies",
    "committee",
    "commission",
    "community",
    "concert",
    "congress",
    "consortium",
    "corporation",
    "council",
    "department",
    "division",
    "enterprise",
    "f.c",
    "fc",
    "federation",
    "foundation",
    "group",
    "h.c",
    "hc",
    "hospital",
    "inc",
    "inc.",
    "institute",
    "institution",
    "laboratories",
    "laboratory",
    "league",
    "limited",
    "ltd",
    "ltd.",
    "media",
    "ministries",
    "ministry",
    "museum",
    "network",
    "office",
    "orchestra",
    "party",
    "philharmonic",
    "press",
    "productions",
    "publishing",
    "radio",
    "railway",
    "railways",
    "records",
    "school",
    "society",
    "studios",
    "team",
    "television",
    "trust",
    "tv",
    "united",
    "university",
}

ORGANIZATION_PHRASES = {
    "association of",
    "bank of",
    "board of",
    "church of",
    "city council",
    "college of",
    "committee of",
    "department of",
    "friends of",
    "group of",
    "hospital of",
    "ministry of",
    "museum of",
    "society of",
    "team of",
    "university of",
}

ORGANIZATION_PAREN_HINTS = {
    "agency",
    "band",
    "business",
    "church",
    "club",
    "company",
    "department",
    "diocese",
    "football club",
    "institution",
    "media company",
    "military unit",
    "museum",
    "organization",
    "organisation",
    "orchestra",
    "political party",
    "record label",
    "school",
    "sports club",
    "team",
    "television channel",
    "university",
}

EVENT_SUFFIXES = {
    "accident",
    "accord",
    "agreement",
    "battle",
    "bombing",
    "campaign",
    "championship",
    "collapse",
    "conference",
    "crash",
    "cup",
    "derby",
    "disaster",
    "earthquake",
    "election",
    "eruption",
    "event",
    "fair",
    "festival",
    "final",
    "games",
    "invasion",
    "massacre",
    "meeting",
    "open",
    "playoffs",
    "rebellion",
    "referendum",
    "revolution",
    "riot",
    "series",
    "siege",
    "strike",
    "summit",
    "tournament",
    "treaty",
    "uprising",
    "war",
}

EVENT_TOKENS = {
    "battle",
    "bombing",
    "campaign",
    "championship",
    "cup",
    "derby",
    "earthquake",
    "election",
    "event",
    "festival",
    "final",
    "flood",
    "games",
    "hurricane",
    "invasion",
    "marathon",
    "massacre",
    "open",
    "playoff",
    "rebellion",
    "referendum",
    "riot",
    "siege",
    "summit",
    "tournament",
    "treaty",
    "uprising",
    "war",
}

EVENT_PHRASES = {
    "battle of",
    "championship of",
    "conference of",
    "cup of",
    "earthquake in",
    "election in",
    "festival of",
    "games in",
    "grand prix",
    "invasion of",
    "massacre of",
    "open at",
    "open in",
    "referendum on",
    "revolution of",
    "riot in",
    "siege of",
    "summit on",
    "treaty of",
    "uprising of",
    "war of",
}

EVENT_PAREN_HINTS = {
    "accident",
    "battle",
    "conflict",
    "disaster",
    "earthquake",
    "event",
    "final",
    "games",
    "match",
    "massacre",
    "race",
    "sporting event",
    "tournament",
    "war",
}

WORK_SUFFIXES = {
    "album",
    "anthem",
    "book",
    "composition",
    "documentary",
    "episode",
    "film",
    "game",
    "mixtape",
    "movie",
    "novel",
    "opera",
    "painting",
    "play",
    "poem",
    "recording",
    "series",
    "single",
    "song",
    "soundtrack",
    "story",
    "suite",
    "symphony",
    "track",
    "video",
}

WORK_TOKENS = {
    "album",
    "anthem",
    "book",
    "chapter",
    "documentary",
    "episode",
    "film",
    "game",
    "memoir",
    "movie",
    "novel",
    "opera",
    "painting",
    "play",
    "poem",
    "recording",
    "season",
    "series",
    "single",
    "song",
    "story",
    "suite",
    "symphony",
    "track",
    "video",
}

WORK_PHRASES = {
    "episode ",
    "film adaptation",
    "season ",
    "soundtrack",
    "story of",
    "tale of",
    "volume ",
}

WORK_PAREN_HINTS = {
    "album",
    "anime",
    "comic",
    "documentary",
    "film",
    "graphic novel",
    "manga",
    "miniseries",
    "novel",
    "opera",
    "play",
    "poem",
    "radio programme",
    "radio program",
    "short film",
    "single",
    "song",
    "soundtrack",
    "television episode",
    "television film",
    "television series",
    "tv episode",
    "tv film",
    "tv series",
    "video game",
    "web series",
}

CONCEPT_SUFFIXES = (
    "ism",
    "ity",
    "ness",
    "ment",
    "tion",
    "sion",
    "ship",
    "ics",
    "ogy",
    "ology",
    "graphy",
    "metry",
    "phobia",
    "philia",
)

CONCEPT_TOKENS = {
    "algorithm",
    "anthropology",
    "biology",
    "chemistry",
    "concept",
    "disease",
    "doctrine",
    "economics",
    "ethics",
    "geography",
    "geometry",
    "ideology",
    "language",
    "law",
    "linguistics",
    "logic",
    "mathematics",
    "method",
    "model",
    "philosophy",
    "physics",
    "policy",
    "principle",
    "process",
    "psychology",
    "religion",
    "science",
    "strategy",
    "syndrome",
    "theorem",
    "theory",
}

CONCEPT_PAREN_HINTS = {
    "concept",
    "disease",
    "disorder",
    "language",
    "law",
    "mathematics",
    "medical",
    "philosophy",
    "physics",
    "psychology",
    "science",
    "syndrome",
    "theory",
}


def _clean_word(word: str) -> str:
    return re.sub(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$", "", word)


def _normalize_person_words(base: str) -> List[str]:
    if "," in base:
        parts = [part.strip() for part in base.split(",")]
        if len(parts) == 2 and 0 < len(parts[1].split()) <= 3:
            recombined = f"{parts[1]} {parts[0]}".strip()
            return [w for w in re.split(r"[ \u00A0]+", recombined) if w]
    return [w for w in re.split(r"[ \u00A0]+", base) if w]


def _looks_like_person(words: List[str]) -> bool:
    if not (2 <= len(words) <= 4):
        return False

    candidate = words[:]
    if candidate and candidate[-1].lower().rstrip(".") in PERSON_SUFFIXES:
        candidate = candidate[:-1]
        if len(candidate) < 2:
            return False

    first_lower = candidate[0].lower()
    if first_lower in PERSON_DISQUALIFYING_FIRST_WORDS:
        return False

    capitalized = 0
    for word in candidate:
        if not word:
            continue
        stripped = word.strip(".")
        lowered = stripped.lower()
        if lowered in NAME_PARTICLES:
            continue
        if any(ch.isdigit() for ch in stripped):
            return False
        parts = stripped.split("-")
        meaningful_parts = [part for part in parts if part]
        if not meaningful_parts:
            return False
        for part in meaningful_parts:
            part_lower = part.lower()
            if part_lower in NAME_PARTICLES:
                continue
            if not part[0].isupper():
                return False
        if stripped[0].isupper():
            capitalized += 1

    return capitalized >= 2


def _contains_phrase(haystack: str, phrases: Iterable[str]) -> bool:
    return any(phrase in haystack for phrase in phrases)


def _word_list_contains(words: Iterable[str], tokens: Iterable[str]) -> bool:
    token_set = tokens if isinstance(tokens, set) else set(tokens)
    return any(word in token_set for word in words)


def _word_has_suffix(word: str, suffixes: Iterable[str]) -> bool:
    lowered = word.lower().rstrip(".")
    return any(lowered.endswith(suffix) for suffix in suffixes)


def _looks_like_event(lower_words: List[str], base_lower: str, paren_hint: str) -> bool:
    if paren_hint and _contains_phrase(paren_hint, EVENT_PAREN_HINTS):
        return True

    last_word = lower_words[-1]
    if last_word in EVENT_SUFFIXES:
        return True
    if _word_list_contains(lower_words, EVENT_TOKENS):
        return True
    if _contains_phrase(base_lower, EVENT_PHRASES):
        return True
    return False


def _looks_like_organization(
    words: List[str], lower_words: List[str], base_lower: str, paren_hint: str
) -> bool:
    if paren_hint and _contains_phrase(paren_hint, ORGANIZATION_PAREN_HINTS):
        return True

    last_word = lower_words[-1]
    if last_word in ORGANIZATION_SUFFIXES:
        return True
    if _word_list_contains(lower_words, ORGANIZATION_TOKENS):
        return True
    if _contains_phrase(base_lower, ORGANIZATION_PHRASES):
        return True
    if any(word.rstrip(".").lower() in ORGANIZATION_SUFFIXES for word in words):
        return True
    return False


def _looks_like_work(lower_words: List[str], base_lower: str, paren_hint: str) -> bool:
    if paren_hint and _contains_phrase(paren_hint, WORK_PAREN_HINTS):
        return True

    last_word = lower_words[-1]
    if last_word in WORK_SUFFIXES:
        return True
    if _word_list_contains(lower_words, WORK_TOKENS):
        return True
    if _contains_phrase(base_lower, WORK_PHRASES):
        return True
    return False


def _looks_like_place(
    words: List[str], lower_words: List[str], base_lower: str, paren_hint: str
) -> bool:
    if paren_hint and _contains_phrase(paren_hint, PLACE_PAREN_HINTS):
        return True

    last_word = lower_words[-1]
    if last_word in PLACE_TOKENS:
        return True
    if any(word in PLACE_TOKENS for word in lower_words):
        return True
    if _word_has_suffix(last_word, PLACE_SUFFIX_ENDINGS):
        return True
    first_word = lower_words[0]
    if first_word in PLACE_PREFIXES:
        return True
    if _contains_phrase(base_lower, PLACE_PHRASES):
        return True
    return False


def _looks_like_concept(lower_words: List[str], base_lower: str, paren_hint: str) -> bool:
    if paren_hint and _contains_phrase(paren_hint, CONCEPT_PAREN_HINTS):
        return True
    if _word_list_contains(lower_words, CONCEPT_TOKENS):
        return True
    if any(_word_has_suffix(word, CONCEPT_SUFFIXES) for word in lower_words):
        return True
    return False


def normalize_category_label(label: str) -> str:
    if not label:
        return DEFAULT_CATEGORY
    cleaned = str(label).strip()
    mapped = CATEGORIES_LEGACY_MAP.get(cleaned, cleaned)
    return mapped if mapped in CATEGORY_DISPLAY_ORDER else DEFAULT_CATEGORY


def classify_missing_title(title: str) -> str:
    raw = title.strip()
    if not raw:
        return DEFAULT_CATEGORY

    paren_hint = ""
    match = re.search(r"\(([^()]*)\)\s*$", raw)
    if match:
        paren_hint = match.group(1).strip().lower()

    base = re.sub(r"\s*\(.*?\)\s*$", "", raw).strip()
    if not base:
        base = raw.strip()

    base_words = [w for w in re.split(r"[ \u00A0]+", base) if w]
    if not base_words:
        return DEFAULT_CATEGORY

    cleaned_words = [_clean_word(w) for w in base_words]
    cleaned_words = [w for w in cleaned_words if w]
    if not cleaned_words:
        return DEFAULT_CATEGORY

    lower_words = [w.lower() for w in cleaned_words]
    base_lower = " ".join(lower_words)

    person_words = _normalize_person_words(base)
    person_words = [_clean_word(w) for w in person_words]
    person_words = [w for w in person_words if w]

    if _looks_like_person(person_words):
        return CATEGORY_PERSON

    if _looks_like_event(lower_words, base_lower, paren_hint):
        return CATEGORY_EVENT

    if _looks_like_organization(cleaned_words, lower_words, base_lower, paren_hint):
        return CATEGORY_ORGANIZATION

    if _looks_like_work(lower_words, base_lower, paren_hint):
        return CATEGORY_WORK

    if _looks_like_place(cleaned_words, lower_words, base_lower, paren_hint):
        return CATEGORY_PLACE

    if _looks_like_concept(lower_words, base_lower, paren_hint):
        return CATEGORY_CONCEPT

    return CATEGORY_OTHER


# --------------------------- Networking utilities --------------------------- #


def _rate_limiter_factory():
    """Global token bucket limiter for all HTTP calls, respecting RPM."""

    lock = threading.Lock()
    tokens = 0.0
    last_refill = time.time()

    def acquire(get_rpm: Callable[[], float], stop_event: threading.Event) -> None:
        nonlocal tokens, last_refill
        while not stop_event.is_set():
            now = time.time()
            rpm = max(1.0, float(get_rpm()))
            rps = rpm / 60.0
            with lock:
                tokens = min(rpm, tokens + (now - last_refill) * rps)
                last_refill = now
                if tokens >= 1.0:
                    tokens -= 1.0
                    return
            time.sleep(0.02)

    return acquire


_global_acquire_token = _rate_limiter_factory()


def _retryable_open(
    req: Request,
    timeout: int,
    max_retries: int,
    stop_event: threading.Event,
) -> bytes:
    attempt = 0
    while True:
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as e:
            attempt += 1
            delay = BACKOFF_BASE ** attempt
            if isinstance(e, HTTPError):
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After")
                    if retry_after:
                        with suppress(Exception):
                            delay = max(delay, float(retry_after))
                elif 500 <= e.code < 600:
                    pass
                else:
                    raise
            if attempt > max_retries or stop_event.is_set():
                raise
            time.sleep(min(30.0, delay))


def api_get(
    params: Dict[str, object],
    acquire_token: Callable[[Callable[[], float], threading.Event], None],
    speed_getter: Callable[[], float],
    stop_event: threading.Event,
) -> Dict[str, object]:
    """Perform a GET request against the MediaWiki API."""

    query_params = params.copy()
    query_params.setdefault("format", "json")
    query_params.setdefault("formatversion", "2")
    url = f"{API_ENDPOINT}?{urlencode(query_params, doseq=True)}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    acquire_token(speed_getter, stop_event)
    payload = _retryable_open(req, REQUEST_TIMEOUT, MAX_RETRIES, stop_event).decode("utf-8")
    return json.loads(payload)


def fetch_random_title(
    acquire_token: Callable[[Callable[[], float], threading.Event], None],
    speed_getter: Callable[[], float],
    stop_event: threading.Event,
) -> str:
    data = api_get(
        {
            "action": "query",
            "generator": "random",
            "grnnamespace": 0,
            "grnlimit": 1,
        },
        acquire_token,
        speed_getter,
        stop_event,
    )
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        raise RuntimeError("Random query returned no pages.")
    return pages[0]["title"]


def fetch_red_links(
    title: str,
    acquire_token: Callable[[Callable[[], float], threading.Event], None],
    speed_getter: Callable[[], float],
    stop_event: threading.Event,
) -> List[str]:
    cached = LINKS_CACHE.get(title)
    if cached is not None:
        return list(cached)

    data = api_get(
        {
            "action": "parse",
            "page": title,
            "prop": "links",
            "redirects": 1,
        },
        acquire_token,
        speed_getter,
        stop_event,
    )

    if "error" in data:
        error = data["error"].get("info") or "Unknown API error."
        raise RuntimeError(error)

    parse_section = data.get("parse", {})
    links = parse_section.get("links", [])
    red_links: List[str] = []
    for link in links:
        if link.get("ns") != 0:
            continue
        if link.get("exists"):
            continue
        title_text = link.get("title")
        if title_text:
            red_links.append(title_text)

    LINKS_CACHE.set(title, red_links)
    return red_links


def fetch_page_info(
    title: str,
    acquire_token: Callable[[Callable[[], float], threading.Event], None],
    speed_getter: Callable[[], float],
    stop_event: threading.Event,
) -> Optional[Dict[str, object]]:
    params = {
        "action": "query",
        "titles": title,
        "prop": "info",
        "inprop": "url",
    }
    data = api_get(params, acquire_token, speed_getter, stop_event)
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return None
    page = pages[0]
    if page.get("missing"):
        return None
    return {
        "title": page.get("title", title),
        "length": page.get("length"),
        "touched": page.get("touched"),
        "fullurl": page.get("fullurl"),
        "pageid": page.get("pageid"),
    }


class CategorySelectionError(RuntimeError):
    """Raised when the chosen category cannot be used for scanning."""


def normalize_category_name(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned:
        return ""
    if not cleaned.lower().startswith("category:"):
        return f"Category:{cleaned}"
    return cleaned


class CategoryArticlePool:
    """Loads and serves article titles from a category (including subcats)."""

    def __init__(
        self,
        category: str,
        acquire_token: Callable[[Callable[[], float], threading.Event], None],
        speed_getter: Callable[[], float],
        stop_event: threading.Event,
    ) -> None:
        normalized = normalize_category_name(category)
        if not normalized:
            raise CategorySelectionError("Category name is empty.")
        self.category = normalized
        self._acquire = acquire_token
        self._speed_getter = speed_getter
        self._stop_event = stop_event
        self._titles = self._fetch_titles()
        if not self._titles:
            raise CategorySelectionError(
                f"Category '{self.category}' has no articles in the main namespace."
            )
        random.shuffle(self._titles)
        self._index = 0

    def next_title(self) -> str:
        if not self._titles:
            raise CategorySelectionError(
                f"Category '{self.category}' has no remaining articles to scan."
            )
        title = self._titles[self._index]
        self._index += 1
        if self._index >= len(self._titles):
            random.shuffle(self._titles)
            self._index = 0
        return title

    def size(self) -> int:
        return len(self._titles)

    def _fetch_titles(self) -> List[str]:
        titles: List[str] = []
        title_set: set[str] = set()
        to_visit: List[str] = [self.category]
        visited: set[str] = set()

        while to_visit and not self._stop_event.is_set():
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            continue_token: Optional[str] = None
            while not self._stop_event.is_set():
                params: Dict[str, object] = {
                    "action": "query",
                    "list": "categorymembers",
                    "cmtitle": current,
                    "cmnamespace": "0|14",
                    "cmtype": "page|subcat",
                    "cmlimit": "500",
                }
                if continue_token:
                    params["cmcontinue"] = continue_token

                data = api_get(params, self._acquire, self._speed_getter, self._stop_event)
                if "error" in data:
                    message = data["error"].get("info") or "Unknown category API error."
                    raise CategorySelectionError(message)

                members = data.get("query", {}).get("categorymembers", [])
                for member in members:
                    title = member.get("title")
                    namespace = member.get("ns")
                    if not title:
                        continue
                    if namespace == 0:
                        if title not in title_set:
                            title_set.add(title)
                            titles.append(title)
                    elif namespace == 14:
                        normalized = normalize_category_name(title)
                        to_visit.append(normalized)

                continue_token = data.get("continue", {}).get("cmcontinue")
                if not continue_token:
                    break

        return titles


def fetch_search_results_count(
    query: str,
    acquire_token: Callable[[Callable[[], float], threading.Event], None],
    speed_getter: Callable[[], float],
    stop_event: threading.Event,
) -> Optional[int]:
    cached = SEARCH_CACHE.get(query)
    if cached is not None:
        return cached

    params = {
        "action": "query",
        "list": "search",
        "format": "json",
        "utf8": "1",
        "srsearch": query,
        "srlimit": 1,
        "srnamespace": 0,
        "srinfo": "totalhits",
    }
    url = f"{API_ENDPOINT}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    acquire_token(speed_getter, stop_event)
    try:
        payload = _retryable_open(req, REQUEST_TIMEOUT, MAX_RETRIES, stop_event).decode(
            "utf-8", errors="ignore"
        )
        data = json.loads(payload)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        SEARCH_CACHE.set(query, None)
        return None

    count = data.get("query", {}).get("searchinfo", {}).get("totalhits")
    if isinstance(count, int):
        SEARCH_CACHE.set(query, count)
        return count

    SEARCH_CACHE.set(query, None)
    return None


# --------------------------- Worker Threads --------------------------------- #


class RedLinkScanner(threading.Thread):
    """Background worker that streams scan progress back through a queue."""

    def __init__(
        self,
        message_queue: "queue.Queue[Dict[str, object]]",
        stop_event: threading.Event,
        speed_getter: Callable[[], float],
        category_getter: Callable[[], str],
        links_per_page_getter: Callable[[], int],
    ) -> None:
        super().__init__(daemon=True)
        self._queue = message_queue
        self._stop_event = stop_event
        self._speed_getter = speed_getter
        self._category_getter = category_getter
        self._links_per_page_getter = links_per_page_getter
        self._category_pool: Optional[CategoryArticlePool] = None
        self._last_category: Optional[str] = None
        self._acquire = _global_acquire_token

    def run(self) -> None:
        consecutive_errors = 0
        while not self._stop_event.is_set():
            delay = max(1.0, 60.0 / self._safe_speed())
            title = ""
            try:
                title = self._resolve_title()
                if self._stop_event.is_set():
                    break
                self._emit_status(f"Scanning '{title}'")
                red_links = fetch_red_links(
                    title, self._acquire, self._speed_getter, self._stop_event
                )
                self._queue.put({"type": "scanned", "title": title})

                max_links = max(0, int(self._links_per_page_getter()))
                to_process = red_links if max_links == 0 else red_links[:max_links]

                if to_process:
                    links_with_sources = []
                    for missing in to_process:
                        if self._stop_event.is_set():
                            break
                        q = f'"{missing}"'
                        sources_count = fetch_search_results_count(
                            q, self._acquire, self._speed_getter, self._stop_event
                        )
                        links_with_sources.append(
                            {
                                "title": missing,
                                "sources": sources_count,
                                "category": classify_missing_title(missing),
                            }
                        )
                    if links_with_sources:
                        self._queue.put(
                            {"type": "links", "source": title, "links": links_with_sources}
                        )

                consecutive_errors = 0
            except CategorySelectionError as category_err:
                consecutive_errors = 0
                self._queue.put({"type": "error", "message": str(category_err)})
                self._wait_with_stop(5.0)
                continue
            except (HTTPError, URLError, TimeoutError) as network_err:
                consecutive_errors += 1
                backoff = min(30.0, delay * (2 ** min(consecutive_errors, 3)))
                message = getattr(network_err, "reason", None) or str(network_err)
                self._queue.put(
                    {"type": "error", "message": f"Network issue: {message}. Retrying soon..."}
                )
                self._wait_with_stop(backoff)
                continue
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                backoff = min(30.0, delay * (2 ** min(consecutive_errors, 3)))
                self._queue.put(
                    {"type": "error", "message": f"Unexpected error: {exc}. Retrying soon..."}
                )
                self._wait_with_stop(backoff)
                continue

            self._wait_with_stop(delay)

    def _safe_speed(self) -> float:
        try:
            rpm = float(self._speed_getter())
        except Exception:
            rpm = DEFAULT_RPM
        return max(1.0, rpm)

    def _emit_status(self, msg: str) -> None:
        self._queue.put({"type": "status", "message": msg})

    def _resolve_title(self) -> str:
        category = self._safe_category()
        if not category:
            self._category_pool = None
            self._last_category = None
            return fetch_random_title(self._acquire, self._speed_getter, self._stop_event)

        if category != self._last_category or not self._category_pool:
            self._queue.put({"type": "status", "message": f"Loading {category} members..."})
            self._category_pool = CategoryArticlePool(
                category, self._acquire, self._speed_getter, self._stop_event
            )
            self._last_category = category
            if self._category_pool:
                self._queue.put(
                    {
                        "type": "debug",
                        "message": f"Loaded {self._category_pool.size()} articles from {category}.",
                    }
                )

        if not self._category_pool:
            raise CategorySelectionError("Category data could not be loaded.")

        return self._category_pool.next_title()

    def _safe_category(self) -> Optional[str]:
        try:
            raw_value = self._category_getter()
        except Exception:
            return None
        category_text = str(raw_value).strip()
        if not category_text:
            return None
        return normalize_category_name(category_text)

    def _wait_with_stop(self, total_seconds: float) -> None:
        end_time = time.time() + total_seconds
        while not self._stop_event.is_set() and time.time() < end_time:
            time.sleep(0.05)


class ShortArticleScanner(threading.Thread):
    """Worker that finds existing articles under a configurable byte threshold."""

    def __init__(
        self,
        message_queue: "queue.Queue[Dict[str, object]]",
        stop_event: threading.Event,
        speed_getter: Callable[[], float],
        category_getter: Callable[[], str],
        max_bytes_getter: Callable[[], int],
    ) -> None:
        super().__init__(daemon=True)
        self._queue = message_queue
        self._stop_event = stop_event
        self._speed_getter = speed_getter
        self._category_getter = category_getter
        self._max_bytes_getter = max_bytes_getter
        self._category_pool: Optional[CategoryArticlePool] = None
        self._last_category: Optional[str] = None
        self._acquire = _global_acquire_token
        self._seen_titles: set[str] = set()

    def run(self) -> None:
        consecutive_errors = 0
        while not self._stop_event.is_set():
            delay = max(1.0, 60.0 / self._safe_speed())
            title = ""
            try:
                title = self._resolve_title()
                if self._stop_event.is_set():
                    break
                threshold = max(1, int(self._safe_max_bytes()))
                self._queue.put(
                    {
                        "type": "status",
                        "message": f"Inspecting '{title}' (≤ {threshold:,} bytes)",
                    }
                )
                info = fetch_page_info(
                    title, self._acquire, self._speed_getter, self._stop_event
                )
                self._queue.put({"type": "scanned_short", "title": title})
                if info is None:
                    consecutive_errors = 0
                    self._wait_with_stop(delay)
                    continue

                length = info.get("length")
                if not isinstance(length, int):
                    consecutive_errors = 0
                    self._wait_with_stop(delay)
                    continue

                if length <= threshold:
                    normalized_title = str(info.get("title", title))
                    if normalized_title not in self._seen_titles:
                        touched = info.get("touched") or ""
                        self._queue.put(
                            {
                                "type": "short_article",
                                "article": {
                                    "title": normalized_title,
                                    "length": length,
                                    "touched": touched,
                                    "url": info.get("fullurl"),
                                },
                            }
                        )
                        self._seen_titles.add(normalized_title)
                consecutive_errors = 0
            except CategorySelectionError as category_err:
                consecutive_errors = 0
                self._queue.put({"type": "error", "message": str(category_err)})
                self._wait_with_stop(5.0)
                continue
            except (HTTPError, URLError, TimeoutError) as network_err:
                consecutive_errors += 1
                backoff = min(30.0, delay * (2 ** min(consecutive_errors, 3)))
                message = getattr(network_err, "reason", None) or str(network_err)
                self._queue.put(
                    {"type": "error", "message": f"Network issue: {message}. Retrying soon..."}
                )
                self._wait_with_stop(backoff)
                continue
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                backoff = min(30.0, delay * (2 ** min(consecutive_errors, 3)))
                self._queue.put(
                    {"type": "error", "message": f"Unexpected error: {exc}. Retrying soon..."}
                )
                self._wait_with_stop(backoff)
                continue

            self._wait_with_stop(delay)

    def _safe_speed(self) -> float:
        try:
            rpm = float(self._speed_getter())
        except Exception:
            rpm = DEFAULT_RPM
        return max(1.0, rpm)

    def _safe_max_bytes(self) -> int:
        try:
            return int(self._max_bytes_getter())
        except Exception:
            return 8000

    def _resolve_title(self) -> str:
        category = self._safe_category()
        if not category:
            self._category_pool = None
            self._last_category = None
            return fetch_random_title(self._acquire, self._speed_getter, self._stop_event)

        if category != self._last_category or not self._category_pool:
            self._queue.put({"type": "status", "message": f"Loading {category} members..."})
            self._category_pool = CategoryArticlePool(
                category, self._acquire, self._speed_getter, self._stop_event
            )
            self._last_category = category
            if self._category_pool:
                self._queue.put(
                    {
                        "type": "debug",
                        "message": f"Loaded {self._category_pool.size()} articles from {category}.",
                    }
                )

        if not self._category_pool:
            raise CategorySelectionError("Category data could not be loaded.")

        return self._category_pool.next_title()

    def _safe_category(self) -> Optional[str]:
        try:
            raw_value = self._category_getter()
        except Exception:
            return None
        category_text = str(raw_value).strip()
        if not category_text:
            return None
        return normalize_category_name(category_text)

    def _wait_with_stop(self, total_seconds: float) -> None:
        end_time = time.time() + total_seconds
        while not self._stop_event.is_set() and time.time() < end_time:
            time.sleep(0.05)


# --------------------------- Helper containers ------------------------------ #


class ScannerState:
    """Utility container used by the web layer to keep track of progress."""

    def __init__(self) -> None:
        self.message_queue: "queue.Queue[Dict[str, object]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.running = False
        self.start_time: Optional[float] = None
        self.scanned_count = 0
        self.result_count = 0
        self.error_count = 0
        self.category_counts: Counter[str] = Counter()
        self.results: List[Dict[str, object]] = []
        self.debug_log: List[Dict[str, object]] = []
        self.status_message = "Idle"

    def reset(self) -> None:
        self.stop_event = threading.Event()
        self.message_queue = queue.Queue()
        self.worker = None
        self.running = False
        self.start_time = None
        self.scanned_count = 0
        self.result_count = 0
        self.error_count = 0
        self.category_counts.clear()
        self.results.clear()
        self.debug_log.clear()
        self.status_message = "Idle"

    def append_debug(self, message: str) -> None:
        timestamp = datetime.utcnow().strftime("%H:%M:%S")
        self.debug_log.append({"time": timestamp, "message": message})
        if len(self.debug_log) > 600:
            self.debug_log.pop(0)

    def clear_results(self) -> None:
        self.scanned_count = 0
        self.result_count = 0
        self.error_count = 0
        self.category_counts.clear()
        self.results.clear()
        self.debug_log.clear()
        self.status_message = "Idle"
        self.start_time = None
