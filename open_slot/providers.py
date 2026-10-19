"""Where Qloo-shaped responses come from.

The fake serves invented performers from the synthetic fixtures and never touches the network.
The live provider asks Qloo through transport.py, the one module that holds the key, behind a
private cache, a ledger of live calls, bounded retries and three limits: the programme's cap,
the agent's daily allowance and the server's daily ceiling. Both answer in the same shapes the
readers parse, and neither ever falls back to the other.
"""

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from . import qloo
from .store import Refusal
from .transport import Response, TransportError, send

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "synthetic"

SEARCH_DEFAULT_TAKE = 20
TRENDING_DEFAULT_TAKE = 20

RETRY_WAITS = (1.0, 3.0)
DAY_S = 86400
CACHE_ENTRIES_MAX = 128
AGENT, PERSON = "agent", "person"

NO_KEY = "No Qloo key is set, so live calls are off. Nothing falls back to synthetic data."
AGENT_SPENT = "The agent's daily allowance of live Qloo calls is spent for this workspace."
DAILY_SPENT = "The server's daily ceiling of live Qloo calls is spent."


@dataclass(frozen=True)
class Origin:
    """Who a call is for. `started_by` is the actor of the top-level action, so a plan a person
    started counts against the person, however many calls the planner makes inside it.
    `attempts_left` is what the programme's cap still allows."""

    workspace: str
    started_by: str
    attempts_left: int


@dataclass(frozen=True)
class Fetched(Response):
    """`cache` is hit, miss or fake. `cost` is what the call counts against the programme's cap:
    every attempt actually sent (a retry counts), 0 for a cache hit, 1 for the fake. `error` is
    the transport's fixed kind when no answer came, with status 0."""

    cache: str = "miss"
    cost: int = 1
    error: str = None


def workspace_label(workspace):
    """An opaque label for a workspace: the id is the browser's cookie, so it goes into no log."""
    return sha256(f"open-slot:{workspace}".encode("utf-8")).hexdigest()[:10]


def _key(text):
    return " ".join(text.casefold().split())


class FakeProvider:
    """Looks each request up in the scenario files. It ignores the trend window and `bias.trends`
    (the fixtures' weekly rows are fixed-date), and it applies the exclusion, popularity and tag
    filters itself, as Qloo does. A typed name resolves only if a scenario lists it."""

    name = "fake"

    def __init__(self, directory=FIXTURES):
        self._scenarios = [json.loads(path.read_text(encoding="utf-8"))
                           for path in sorted(Path(directory).glob("scenario-*.json"))]

    def fetch(self, request, origin=None):
        status, body = self.respond(request.path, request.params)
        return Fetched(status, body, 0, frozenset(), "fake", 1)

    def respond(self, path, params):
        if path == qloo.SEARCH:
            return self._search(params)
        if path == qloo.TAGS:
            return self._tags(params)
        if path in (qloo.INSIGHTS, qloo.TRENDING):
            if params.get("filter.type") not in qloo.ENTITY_TYPES:
                return 403, {"success": False, "message": "This entity type is not supported."}
            return self._insights(params) if path == qloo.INSIGHTS else self._trending(params)
        return 404, {"success": False, "message": "No path matching request parameters"}

    def _search(self, params):
        wanted = set(params.get("types", "").split(","))
        for scenario in self._scenarios:
            ids = scenario["search"].get(_key(params["query"]))
            if ids is None:
                continue
            by_id = {e["entity_id"]: e for e in scenario["entities"]}
            found = [by_id[i] for i in ids if by_id[i]["type"] in wanted][:int(params.get("take", 5))]
            if found:
                return 200, {"success": True, "results": [dict(e) for e in found]}
        return 404, {"success": False, "message": "No results"}

    def _tags(self, params):
        word, seen, found = _key(params["filter.query"]), set(), []
        for scenario in self._scenarios:
            for tag in scenario["tags"]:
                if tag["id"] not in seen and (word in _key(tag["name"]) or word in tag["id"]):
                    seen.add(tag["id"])
                    found.append({"tag_id": tag["id"], "name": tag["name"], "types": [tag["type"]],
                                  "subtype": tag["type"]})
        take = int(params.get("take", 10))
        return 200, {"success": True, "results": {"tags": found[:take]}}

    def _scenario_of(self, entity_id):
        return next((s for s in self._scenarios
                     if any(e["entity_id"] == entity_id for e in s["entities"])), None)

    def _insights(self, params):
        anchors = params["signal.interests.entities"].split(",")
        scenario = self._scenario_of(anchors[0])
        pool = scenario["pools"].get(params["filter.type"], []) if scenario else []
        excluded = set(params.get("filter.exclude.entities", "").split(","))
        low = float(params.get("filter.popularity.min", 0))
        high = float(params.get("filter.popularity.max", 1))
        tag = params.get("filter.tags")
        kept = [e for e in pool
                if e["entity_id"] not in excluded and low <= e["popularity"] <= high
                and (tag is None or tag in [t["id"] for t in e["tags"]])]
        explain = params.get("feature.explainability") == "true"
        return 200, {"success": True, "results": {"entities": [
            self._entity(e, anchors, explain) for e in kept[:int(params.get("take", SEARCH_DEFAULT_TAKE))]
        ]}}

    @staticmethod
    def _entity(entry, anchors, explain):
        query = {}
        if entry["growth"] is not None:
            query["measurements"] = {"audience_growth": entry["growth"]}
        if explain:
            query["explainability"] = {"signal.interests.entities": [
                {"entity_id": anchor, "score": entry["links"][anchor]}
                for anchor in anchors if anchor in entry["links"]
            ]}
        shown = {"entity_id": entry["entity_id"], "name": entry["name"], "type": entry["type"],
                 "popularity": entry["popularity"], "tags": entry["tags"]}
        return {**shown, "query": query} if query else shown

    def _trending(self, params):
        entity_id = params["signal.interests.entities"]
        rows = next((s["trends"][entity_id] for s in self._scenarios if entity_id in s["trends"]), [])
        take = int(params.get("take", TRENDING_DEFAULT_TAKE))
        return 200, {"success": True, "results": {"trends": rows[:take]}}


class Ledger:
    """Live attempts, the private response cache and, when given a directory, their two files.

    The counters bound one run of the server: the agent's allowance is a rolling 24 hours per
    workspace and the ceiling is per UTC day. The files hold only attempt times and cached
    responses, never the key, a workspace id or a query, and the directory is git-ignored.
    `clock` returns seconds since the epoch."""

    def __init__(self, daily_limit, agent_limit, cache_ttl, clock=time.time, directory=None):
        self.daily_limit, self.agent_limit, self.cache_ttl = daily_limit, agent_limit, cache_ttl
        self.clock = clock
        self.directory = None if directory is None else Path(directory)
        self.hits = 0
        self._lock = threading.Lock()
        self._attempts = []
        self._cache = {}
        if self.directory is not None:
            self._load()

    @staticmethod
    def _day(stamp):
        return datetime.fromtimestamp(stamp, timezone.utc).date()

    def _daily_used(self, now):
        today = self._day(now)
        return sum(1 for stamp, _, _ in self._attempts if self._day(stamp) == today)

    def _agent_used(self, workspace, now):
        return sum(1 for stamp, owner, by in self._attempts
                   if owner == workspace and by == AGENT and stamp > now - DAY_S)

    def daily_used(self):
        with self._lock:
            return self._daily_used(self.clock())

    def agent_used(self, workspace):
        with self._lock:
            return self._agent_used(workspace, self.clock())

    def daily_left(self):
        return max(0, self.daily_limit - self.daily_used())

    def agent_left(self, workspace):
        return max(0, self.agent_limit - self.agent_used(workspace))

    def admit(self, workspace, started_by):
        """Counts one live attempt, or refuses it: the agent's allowance first, then the ceiling."""
        with self._lock:
            now = self.clock()
            self._attempts = [a for a in self._attempts if a[0] > now - DAY_S]
            if started_by == AGENT and self._agent_used(workspace, now) >= self.agent_limit:
                raise Refusal("agent_allowance_spent", AGENT_SPENT)
            if self._daily_used(now) >= self.daily_limit:
                raise Refusal("daily_allowance_spent", DAILY_SPENT)
            self._attempts.append((now, workspace, started_by))
            self._write("ledger.json", [stamp for stamp, _, _ in self._attempts])

    def recall(self, key):
        with self._lock:
            found = self._cache.get(key)
            if found is None or self.clock() - found[0] >= self.cache_ttl:
                return None
            self.hits += 1
            return found[1], found[2]

    def remember(self, key, status, body):
        if self.cache_ttl <= 0:
            return
        with self._lock:
            self._cache[key] = (self.clock(), status, body)
            while len(self._cache) > CACHE_ENTRIES_MAX:
                del self._cache[min(self._cache, key=lambda k: self._cache[k][0])]
            self._write("cache.json", {k: list(v) for k, v in self._cache.items()})

    def snapshot(self, workspace=None):
        with self._lock:
            now = self.clock()
            shown = {
                "daily": {"limit": self.daily_limit, "used": self._daily_used(now),
                          "day": self._day(now).isoformat()},
                "cache": {"entries": len(self._cache), "hits": self.hits, "ttlSeconds": self.cache_ttl},
            }
            if workspace is not None:
                shown["agent"] = {"limit": self.agent_limit, "used": self._agent_used(workspace, now),
                                  "windowHours": 24}
            return shown

    def _write(self, name, data):
        if self.directory is None:
            return
        self.directory.mkdir(mode=0o700, exist_ok=True)
        target = self.directory / name
        temporary = target.with_name(name + ".tmp")
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temporary, target)

    def _read(self, name, default):
        try:
            return json.loads((self.directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _load(self):
        now = self.clock()
        stamps = self._read("ledger.json", [])
        self._attempts = [(s, None, None) for s in stamps
                          if isinstance(s, (int, float)) and s > now - DAY_S]
        cached = self._read("cache.json", {})
        self._cache = {k: (v[0], v[1], v[2]) for k, v in cached.items()
                       if isinstance(v, list) and len(v) == 3 and now - v[0] < self.cache_ttl}


def _retryable(reply):
    return reply.error is None and (reply.status == 429 or reply.status >= 500)


def _cacheable(path, status):
    return status == 200 or (status == 404 and path == qloo.SEARCH)


class LiveProvider:
    """One GET to Qloo per attempt. A 429 or a 5xx is retried at most twice, after one second and
    then three, and every attempt counts against the programme, the agent's allowance and the
    ceiling. A transport failure comes back as a Fetched with its fixed kind, never as the library's
    text. With no key every call is refused `no_key`; nothing falls back to the fake."""

    name = "live"

    def __init__(self, key, ledger, base_url=qloo.BASE_URL, timeout=15.0, sleep=time.sleep):
        self._key = key
        self.ledger = ledger
        self.base_url = base_url
        self.timeout = timeout
        self._sleep = sleep

    def __repr__(self):
        return f"LiveProvider(key {'set' if self._key else 'missing'})"

    def _once(self, request):
        try:
            reply = send(self.base_url, request, self._key, self.timeout)
        except TransportError as error:
            return Fetched(0, None, 0, frozenset(), "miss", 1, error.kind)
        return Fetched(reply.status, reply.body, reply.ms, reply.header_names, "miss", 1)

    def fetch(self, request, origin):
        if not self._key:
            raise Refusal("no_key", NO_KEY)
        key = request.cache_key()
        cached = self.ledger.recall(key)
        if cached is not None:
            return Fetched(cached[0], cached[1], 0, frozenset(), "hit", 0)
        attempts, spent_ms, reply = 0, 0, None
        while True:
            try:
                self.ledger.admit(origin.workspace, origin.started_by)
            except Refusal:
                if attempts == 0:
                    raise
                break
            attempts += 1
            reply = self._once(request)
            spent_ms += reply.ms
            if not _retryable(reply) or attempts > len(RETRY_WAITS) or attempts >= origin.attempts_left:
                break
            self._sleep(RETRY_WAITS[attempts - 1])
        if reply.error is None and _cacheable(request.path, reply.status):
            self.ledger.remember(key, reply.status, reply.body)
        return Fetched(reply.status, reply.body, spent_ms, reply.header_names, "miss", attempts, reply.error)


def build(settings, name=None, directory=None, **options):
    """The provider and its ledger for these settings. `name` overrides the configured provider.
    Asking for live with no key still builds a provider: it refuses every call `no_key`."""
    ledger = Ledger(settings.daily_live_calls, settings.agent_calls_per_day, settings.cache_ttl_s,
                    directory=directory)
    if (name or settings.provider) == "fake":
        return FakeProvider(), ledger
    return LiveProvider(settings.key, ledger, **options), ledger
