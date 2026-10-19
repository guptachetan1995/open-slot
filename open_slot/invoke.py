"""The one path in. Every tool call, every click and every planner step goes through here.

invoke() checks who is asking, refuses the human-only verbs for anyone but the person (by
structure, not by a prompt), refuses arguments a tool does not declare, runs the handler and logs
the call with its actor. The page's buttons, the command line, the agent console, the agent
address and the MCP front end all call it: none of them has a second code path.
"""

import copy
import secrets
import threading
from datetime import datetime

from . import providers, qloo, store, tools, verbs
from .providers import AGENT, PERSON, Origin, workspace_label
from .store import Refusal

ACTORS = (AGENT, PERSON)
LOGGED_ARGS = ("programme_id", "entity_id", "night_id", "draft_id", "cap", "max_calls")
MESSAGES = {
    "not_a_tool": "That name is not one of the tools this surface offers.",
    "wrong_actor": "Only a person can run that.",
    "unknown_actor": "The actor must be person or agent.",
    "stale_revision": "The evidence changed since it was read; read it again first.",
    "smuggled_field": "That tool does not take one of the arguments sent.",
    "needs_anchors": "A plan needs at least two confirmed acts.",
    "anchor_unconfirmed": "An act is found but not yet confirmed by a person.",
    "unknown_entity": "The programme holds no such entity.",
    "unknown_tag": "That tag id is not one this programme knows.",
    "unknown_programme": "There is no such programme.",
    "no_workspace": "There is no such workspace.",
    "slot_taken": "That night already holds another act.",
    "sender_missing": "The sender's name is missing or not 1 to 60 printable characters.",
    "over_cap": "The programme's cap of live Qloo calls is spent.",
    "agent_allowance_spent": providers.AGENT_SPENT,
    "daily_allowance_spent": providers.DAILY_SPENT,
    "no_key": providers.NO_KEY,
    "rate_limited": "This workspace is calling too fast; try again in a minute.",
    "bad_args": "The arguments do not match what the tool declares.",
    "draft_too_long": "The inquiry would run past its word limit.",
    **qloo.OUTCOME_MESSAGES,
}
READERS = {
    qloo.SEARCH: (qloo.read_search, len, []),
    qloo.TAGS: (qloo.read_tags, len, []),
    qloo.INSIGHTS: (qloo.read_insights, lambda read: len(read["entities"]),
                    {"entities": [], "explainability": False}),
    qloo.TRENDING: (qloo.read_trending, len, []),
}


def _outcome_of(fetched, path):
    return "qloo_unavailable" if fetched.error else qloo.outcome(fetched.status, path)


class Runtime:
    """The state an app runs on: the document, the provider and its ledger, the settings and a clock.

    Two kinds of lock keep it right under threads. A workspace's invokes run one at a time under that
    workspace's own lock, which a plan holds for as long as Qloo takes to answer, so one workspace's
    plan never makes another wait. The document is replaced, never changed in place; every
    replacement goes through `update`, which holds a lock only for the swap, and the ledger counts
    every live attempt under its own lock."""

    def __init__(self, settings, provider, ledger, clock=None, fallback=None):
        self.settings, self.provider, self.ledger = settings, provider, ledger
        self.fallback = fallback
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.doc = store.empty_document({"provider": provider.name, "key": settings.key_status})
        self._doc_lock = threading.Lock()
        self._guard = threading.Lock()
        self._locks = {}

    @classmethod
    def build(cls, settings, name=None, directory=None, clock=None, fallback=None, **options):
        provider, ledger = providers.build(settings, name, directory, **options)
        return cls(settings, provider, ledger, clock, fallback)

    def mode(self):
        """`live` while live calls are on, `synthetic` when the server runs on the synthetic provider,
        and `spent` when it is live and today's ceiling is used up."""
        if self.provider.name != "live":
            return "synthetic"
        return "spent" if self.ledger.daily_left() < 1 else "live"

    def provider_for_new_programme(self):
        """The provider a programme opened now runs on, for as long as it exists. Only a spent ceiling
        moves a new programme to the synthetic one, and only when the server was given one; a
        programme already running never changes provider."""
        if self.fallback is not None and self.mode() == "spent":
            return self.fallback
        return self.provider

    def provider_named(self, name):
        return self.provider if self.provider.name == name else self.fallback

    def update(self, change, *args):
        """Replaces the document with `change(document, *args)`, a function from store.py."""
        with self._doc_lock:
            self.doc = change(self.doc, *args)

    def workspace_lock(self, workspace_id):
        with self._guard:
            return self._locks.setdefault(workspace_id, threading.Lock())

    def _forget_locks(self):
        with self._guard:
            alive = {found["id"] for found in self.doc["workspaces"]}
            self._locks = {name: lock for name, lock in self._locks.items() if name in alive}

    def new_workspace(self):
        workspace_id = "ws_" + secrets.token_hex(16)
        self.update(store.new_workspace, workspace_id, secrets.token_hex(16),
                    self.clock().isoformat(timespec="seconds"))
        self._forget_locks()
        return workspace_id

    def prune(self, ttl_s):
        self.update(store.prune, self.clock().isoformat(timespec="seconds"), ttl_s)
        self._forget_locks()


class Context:
    """One invoke. `started_by` is the actor of the top-level action: the planner's own sub-calls
    are logged as the agent, but a plan a person started still counts as the person's."""

    def __init__(self, runtime, workspace_id, actor):
        self.runtime, self.workspace_id, self.actor, self.started_by = runtime, workspace_id, actor, actor
        self.settings = runtime.settings
        moment = runtime.clock()
        self.now, self.today = moment.isoformat(timespec="seconds"), moment.date()
        self.outcome = "ok"

    def programme(self, programme_id):
        return copy.deepcopy(store.programme(self.runtime.doc, self.workspace_id, programme_id))

    def save(self, found):
        self.runtime.update(store.replace_programme, self.workspace_id, found)
        return self.programme(found["id"])

    def headroom(self, found, limit=None):
        """The stop code of the first limit already reached, or None. The fake costs no live call,
        so only the programme's cap applies to it."""
        budget = found["budget"]
        if budget["spent"] >= (budget["cap"] if limit is None else min(budget["cap"], limit)):
            return "cap"
        ledger = self.runtime.ledger
        if found["provider"] == "live":
            if self.started_by == AGENT and ledger.agent_left(self.workspace_id) < 1:
                return "agent_allowance"
            if ledger.daily_left() < 1:
                return "ceiling"
        return None

    def ask(self, found, request, tool, because, entity_id=None, limit=None, actor=None):
        """One Qloo call for a programme: checks the cap, asks the provider, then records the spend,
        the call log entry and the trace step, and stores the programme. Returns the stored copy,
        the outcome and what the reader made of the answer (None when Qloo gave none). A shape the
        reader does not know is raised only after the call has been recorded."""
        budget = found["budget"]
        cap = budget["cap"] if limit is None else min(budget["cap"], limit)
        if budget["spent"] >= cap:
            raise Refusal("over_cap", f"the programme's cap of {budget['cap']} live calls is spent")
        runtime = self.runtime
        provider = runtime.provider_named(found["provider"])
        fetched = provider.fetch(request, Origin(self.workspace_id, self.started_by, cap - budget["spent"]))
        budget["spent"] += fetched.cost
        runtime.update(store.log_call, {
            "ts": self.now, "provider": provider.name, "path": request.path,
            "cache": fetched.cache, "ms": fetched.ms, "status": fetched.status, "attempts": fetched.cost,
            "startedBy": self.started_by, "workspace": workspace_label(self.workspace_id),
        })
        read, count, empty = READERS[request.path]
        outcome, parsed, failure = _outcome_of(fetched, request.path), None, None
        if outcome == "ok":
            try:
                parsed = read(fetched.body)
                outcome = qloo.outcome(fetched.status, request.path, count(parsed))
            except qloo.UnknownShape as shape:
                outcome, failure = "qloo_unknown_shape", shape
        if outcome == "qloo_no_results" and parsed is None:
            parsed = copy.deepcopy(empty)
        store.trace_step(
            found, kind="call", tool=tool, actor=actor or self.actor, entityId=entity_id, because=because,
            request={"path": request.path, "params": dict(request.params)}, cache=fetched.cache,
            ms=fetched.ms, callsSpent=fetched.cost, spent=budget["spent"], outcome=outcome,
            got=None if parsed is None else count(parsed))
        found = self.save(found)
        if failure is not None:
            raise failure
        return found, outcome, parsed

    @staticmethod
    def require(outcome):
        if outcome not in ("ok", "qloo_no_results"):
            raise Refusal(outcome, qloo.OUTCOME_MESSAGES[outcome])

    def sub_activity(self, tool, args, outcome):
        self.runtime.update(store.log_activity, self.workspace_id, self.now, AGENT, tool, args, outcome)


def _logged(args):
    if not isinstance(args, dict):
        return {}
    kept = {name: args[name] for name in LOGGED_ARGS
            if name in args and isinstance(args[name], (str, int, type(None))) and args[name] is not True
            and args[name] is not False}
    return {name: value[:64] if isinstance(value, str) else value for name, value in kept.items()}


def _run(ctx, tool, args, actor, surface):
    if actor not in ACTORS:
        raise Refusal("unknown_actor", MESSAGES["unknown_actor"])
    spec = verb = None
    if isinstance(tool, str):
        spec, verb = tools.REGISTRY.get(tool), verbs.VERBS.get(tool)
    if verb is not None:
        if surface == "agent":
            raise Refusal("not_a_tool", MESSAGES["not_a_tool"])
        if actor != PERSON:
            raise Refusal("wrong_actor", MESSAGES["wrong_actor"])
    entry = spec or verb
    if entry is None:
        raise Refusal("not_a_tool", MESSAGES["not_a_tool"])
    return entry.handler(ctx, tools.check(args, entry.args))


def _failure(refusal):
    code = refusal.code
    return {"ok": False, "outcome": code,
            "error": {"code": code, "message": refusal.message or MESSAGES.get(code, code)}}


def invoke(runtime, workspace_id, tool, args, actor, surface="page"):
    """Runs one tool or verb and returns the envelope {ok, outcome, result | error}. `surface` is
    "agent" for the agent address, MCP and the command line's agent mode, which list only the
    registry: there a human verb is `not_a_tool`. On the page's own surface a human verb asked for by
    the agent is `wrong_actor`. Either way it is refused and logged, and nothing runs."""
    with runtime.workspace_lock(workspace_id):
        try:
            store.workspace(runtime.doc, workspace_id)
        except Refusal as missing:
            return _failure(missing)
        ctx = Context(runtime, workspace_id, actor)
        runtime.update(store.touch, workspace_id, ctx.now)
        try:
            result = _run(ctx, tool, args, actor, surface)
            envelope = {"ok": True, "outcome": ctx.outcome, "result": result}
        except Refusal as refusal:
            envelope = _failure(refusal)
        except qloo.UnknownShape as shape:
            envelope = _failure(Refusal(
                "qloo_unknown_shape", f"{qloo.OUTCOME_MESSAGES['qloo_unknown_shape']} (at {shape.where})"))
        except qloo.RequestRefused as refused:
            envelope = _failure(Refusal("bad_args", str(refused)))
        runtime.update(store.log_activity, workspace_id, ctx.now, actor if actor in ACTORS else "unknown",
                       tools.field_names([tool]), _logged(args), envelope["outcome"])
        return envelope
