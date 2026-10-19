"""Workspaces and programmes as pure functions over one JSON document.

Every function that changes something returns a new document and leaves its argument alone, so
a test is a round trip, and a host that keeps no state could carry the document in the browser.
"""

import copy
from datetime import datetime

from . import pins

LENSES = ("music", "podcast", "comedy")
LABEL_MAX = 60
NIGHT_MAX = 40
CAPACITY_MAX = 40
NIGHTS_RANGE = (1, 6)
ANCHORS_RANGE = (2, 6)
MAX_WORKSPACES = 200
ACTIVITY_MAX = 500
CALLS_MAX = 2000


class Refusal(Exception):
    """A named refusal. `code` is the name the registry, the page and the logs use."""

    def __init__(self, code, message=""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def empty_document(server=None):
    return {"workspaces": [], "calls": [], "server": dict(server or {})}


def _seen(workspace):
    return datetime.fromisoformat(workspace["lastSeenAt"])


def _with_workspace(doc, workspace_id, change):
    changed, found = [], False
    for workspace in doc["workspaces"]:
        if workspace["id"] == workspace_id:
            workspace, found = change(copy.deepcopy(workspace)), True
        changed.append(workspace)
    if not found:
        raise Refusal("no_workspace")
    return {**doc, "workspaces": changed}


def workspace(doc, workspace_id):
    for found in doc["workspaces"]:
        if found["id"] == workspace_id:
            return found
    raise Refusal("no_workspace")


def new_workspace(doc, workspace_id, agent_token, at):
    """Adds a workspace; when the table is full the least recently seen one is forgotten first."""
    fresh = {
        "id": workspace_id, "agentToken": agent_token, "createdAt": at, "lastSeenAt": at,
        "agentCalls24h": 0, "programmes": [], "activityLog": [],
    }
    kept = sorted(doc["workspaces"], key=_seen)[max(0, len(doc["workspaces"]) - MAX_WORKSPACES + 1):]
    return {**doc, "workspaces": kept + [fresh]}


def touch(doc, workspace_id, at):
    def change(workspace):
        workspace["lastSeenAt"] = at
        return workspace
    return _with_workspace(doc, workspace_id, change)


def prune(doc, at, ttl_s):
    """Forgets workspaces idle for ttl_s seconds or more."""
    cutoff = datetime.fromisoformat(at).timestamp() - ttl_s
    return {**doc, "workspaces": [w for w in doc["workspaces"] if _seen(w).timestamp() > cutoff]}


def clean(value, name, limit):
    if not isinstance(value, str):
        raise Refusal("bad_args", f"{name} must be text")
    text = " ".join(value.split())
    if not text or len(text) > limit or not text.isprintable():
        raise Refusal("bad_args", f"{name} must be 1 to {limit} printable characters")
    return text


def _clean_list(value, name, limit, count):
    if not isinstance(value, list) or not count[0] <= len(value) <= count[1]:
        raise Refusal("bad_args", f"{name} must list {count[0]} to {count[1]} items")
    cleaned = [clean(item, name, limit) for item in value]
    if len({item.casefold() for item in cleaned}) != len(cleaned):
        raise Refusal("bad_args", f"{name} must not repeat an item")
    return cleaned


def _lenses(value):
    if value is None:
        return list(pins.CONFIRMED_LENSES)
    if (not isinstance(value, list) or not value or len(set(value)) != len(value)
            or not set(value) <= set(pins.CONFIRMED_LENSES)):
        raise Refusal("bad_args", f"lenses must be a subset of {', '.join(pins.CONFIRMED_LENSES)}")
    return list(value)


def new_programme(doc, workspace_id, args, cap, at, provider="fake"):
    """`args` are open_programme's: label, nights, anchors, and optionally lenses and capacity_note.
    `provider` names where its Qloo answers come from, `live` or `fake`, and never changes."""
    label = clean(args.get("label"), "label", LABEL_MAX)
    nights = _clean_list(args.get("nights"), "nights", NIGHT_MAX, NIGHTS_RANGE)
    anchors = _clean_list(args.get("anchors"), "anchors", 60, ANCHORS_RANGE)
    lenses = _lenses(args.get("lenses"))
    note = args.get("capacity_note")
    capacity = None if note is None else clean(note, "capacity_note", CAPACITY_MAX)
    known = []
    for lens in lenses:
        tag = pins.LENS_DEFAULTS[lens]["tag"]
        if tag:
            known.append({"id": tag, "name": lens, "type": None})

    def change(found):
        programme_id = f"prog_{len(found['programmes']) + 1:03d}"
        found["lastSeenAt"] = at
        found["programmes"].append({
            "id": programme_id,
            "revision": 0,
            "provider": provider,
            "label": label,
            "capacityNote": capacity,
            "nights": [{"id": f"n{i}", "label": text, "state": "open", "proposed": None, "assigned": None}
                       for i, text in enumerate(nights, 1)],
            "anchors": [{"name": name, "candidates": [], "confirmed": None} for name in anchors],
            "knownTags": known,
            "lenses": lenses,
            "range": None,
            "budget": {"cap": cap, "spent": 0, "saved": 0, "stoppedBecause": None},
            "candidates": [],
            "inquiries": [],
            "trace": [],
        })
        return found

    changed = _with_workspace(doc, workspace_id, change)
    return changed, workspace(changed, workspace_id)["programmes"][-1]["id"]


def programme(doc, workspace_id, programme_id):
    for found in workspace(doc, workspace_id)["programmes"]:
        if found["id"] == programme_id:
            return found
    raise Refusal("unknown_programme")


def replace_programme(doc, workspace_id, changed):
    """Stores a changed copy of a programme and adds 1 to its revision: the revision is how a
    person's assign or approve learns that the evidence moved since they read it."""
    stored = {**changed, "revision": changed["revision"] + 1}

    def change(found):
        found["programmes"] = [stored if p["id"] == stored["id"] else p for p in found["programmes"]]
        return found

    programme(doc, workspace_id, changed["id"])
    return _with_workspace(doc, workspace_id, change)


def log_activity(doc, workspace_id, at, actor, tool, args, outcome):
    def change(found):
        found["activityLog"] = (found["activityLog"] + [
            {"ts": at, "actor": actor, "tool": tool, "args": args, "outcome": outcome},
        ])[-ACTIVITY_MAX:]
        return found
    return _with_workspace(doc, workspace_id, change)


def log_call(doc, entry):
    """`entry`: ts, provider, path, cache, ms, startedBy, workspace. Never a name or a query value."""
    return {**doc, "calls": (doc["calls"] + [entry])[-CALLS_MAX:]}


def confirmed_anchors(found):
    """The anchors the person has confirmed, each with the entity they confirmed."""
    anchors = []
    for anchor in found["anchors"]:
        if anchor["confirmed"] is None:
            continue
        entity = next(c for c in anchor["candidates"] if c["entityId"] == anchor["confirmed"])
        anchors.append({
            "name": anchor["name"], "entityId": entity["entityId"],
            "resolvedName": entity["name"], "popularity": entity["popularity"],
        })
    return anchors


def trace_step(found, **fields):
    """Appends one step to a programme's trace, numbered after the last."""
    found["trace"].append({"step": len(found["trace"]) + 1, **fields})


def anchor_named(found, name):
    wanted = " ".join(str(name).split()).casefold()
    for anchor in found["anchors"]:
        if " ".join(anchor["name"].split()).casefold() == wanted:
            return anchor
    raise Refusal("bad_args", "name must be one of this programme's acts")


def candidate(found, entity_id):
    for held in found["candidates"]:
        if held["entityId"] == entity_id:
            return held
    raise Refusal("unknown_entity", "the programme holds no candidate with that id")
