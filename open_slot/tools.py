"""The agent's tools: names, argument schemas, descriptions and handlers.

A description is the only thing a model reads, so each one is written for someone who cannot
see the screen and says what the tool does NOT do. The human-only verbs are in verbs.py and are
never listed here: nothing that lists these tools can offer them.
"""

from dataclasses import dataclass

from . import inquiry, planner, qloo, rank, slots, store
from .store import Refusal

RANKED_BY = {
    "status": "no other candidate has the same status",
    "links": "more of your acts connect to it (Qloo's explanation scores)",
    "growth": "Qloo's growth figure for it is higher",
    "closeness": "its popularity is closer to the median of your acts' (Qloo's popularity)",
    "order": "Qloo's own order, because nothing else told the candidates apart",
}

DATA = {"live": "live", "fake": "synthetic"}

PYTHON_TYPES = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "array": list, "object": dict, "null": type(None),
}


@dataclass(frozen=True)
class Arg:
    """`kind` is a JSON type name or a tuple of them; `items` types the members of an array."""

    kind: object
    text: str
    required: bool = False
    items: str = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: dict
    handler: object


def _matches(value, kind):
    for name in (kind,) if isinstance(kind, str) else kind:
        if name in ("integer", "number") and isinstance(value, bool):
            continue
        if isinstance(value, PYTHON_TYPES[name]):
            return True
    return False


def field_names(names):
    """Argument names a caller sent, safe to log and to show back."""
    return ", ".join(sorted(n if isinstance(n, str) and n.isidentifier() and len(n) <= 40 else "<name>"
                            for n in names))


def check(args, spec):
    """The arguments if they match the declared ones. An argument that is not declared is refused,
    never dropped: a tool that quietly ignored `status` or `sender_name` would hide an attempt."""
    if not isinstance(args, dict):
        raise Refusal("bad_args", "args must be an object")
    undeclared = [name for name in args if name not in spec]
    if undeclared:
        raise Refusal("smuggled_field", f"this tool does not take: {field_names(undeclared)}")
    for name, arg in spec.items():
        if name not in args:
            if arg.required:
                raise Refusal("bad_args", f"{name} is required")
        elif not _matches(args[name], arg.kind) or (
                arg.items and not all(_matches(item, arg.items) for item in args[name])):
            kinds = arg.kind if isinstance(arg.kind, str) else " or ".join(arg.kind)
            raise Refusal("bad_args", f"{name} must be of type {kinds}"
                                      + (f", with {arg.items} members" if arg.items else ""))
    return args


def _property(arg):
    shown = {"type": list(arg.kind) if isinstance(arg.kind, tuple) else arg.kind, "description": arg.text}
    return {**shown, "items": {"type": arg.items}} if arg.items else shown


def schema(spec):
    return {
        "type": "object",
        "properties": {name: _property(arg) for name, arg in spec.items()},
        "required": [name for name, arg in spec.items() if arg.required],
        "additionalProperties": False,
    }


def evidence(found, held):
    """Everything stored about one candidate, each figure labelled with whose it is."""
    rng, confirmed = found["range"], len(store.confirmed_anchors(found))
    latest = (held["trend"] or {}).get("rows") or []
    return {
        "entity_id": held["entityId"],
        "name": held["name"],
        "type": held["type"],
        "lens": held["lens"],
        "status": held["status"],
        "status_text": rank.label(held, rng, confirmed),
        "popularity": {
            "value": held["popularity"], "source": "Qloo's popularity percentile (0 to 1)",
            "room_range": None if rng is None else {
                "min": rng["min"], "max": rng["max"],
                "source": "this app's range from your acts' popularity, or one the plan was given"},
        },
        "links": [{**link, "source": "Qloo's explanation score"} for link in held["links"]],
        "link_rule": f"scores of at least {rank.EXPLAIN_MIN} (Qloo's own inclusion threshold); "
                     f"this app asks for {rank.min_links(confirmed)} of your {confirmed} confirmed acts",
        "growth": None if held["growth"] is None else {"value": held["growth"], "source": "Qloo"},
        "trend": None if held["trend"] is None else {
            "state": held["trend"]["state"], "weeks": held["trend"]["weeks"],
            "changes": held["trend"]["changes"], "rule": held["trend"]["rule"],
            "newest_percentile": latest[0]["population_percentile"] if latest else None,
            "source": "Qloo's weekly rows; the state is this app's rule",
        },
        "tags": held["tags"],
        "ranked_by": held["rankedBy"],
        "ranked_by_text": RANKED_BY[held["rankedBy"]],
    }


def open_programme(ctx, args):
    provider = ctx.runtime.provider_for_new_programme().name
    ctx.runtime.update(lambda doc: store.new_programme(
        doc, ctx.workspace_id, args, ctx.settings.max_calls, ctx.now, provider)[0])
    found = store.workspace(ctx.runtime.doc, ctx.workspace_id)["programmes"][-1]
    programme_id = found["id"]
    return {
        "programme_id": programme_id,
        "data": DATA[provider],
        "nights": [{"id": night["id"], "label": night["label"]} for night in found["nights"]],
        "anchors": [anchor["name"] for anchor in found["anchors"]],
        "lenses": found["lenses"],
        "call_cap": found["budget"]["cap"],
    }


def find_anchor(ctx, args):
    found = ctx.programme(args["programme_id"])
    name = store.anchor_named(found, args["name"])["name"]
    because = "resolve a name the person typed to a Qloo entity; the person confirms which match is meant"
    found, outcome, candidates = ctx.ask(found, qloo.search(name), "find_anchor", because)
    ctx.require(outcome)
    anchor = store.anchor_named(found, name)
    anchor["candidates"] = candidates
    if anchor["confirmed"] not in {c["entityId"] for c in candidates}:
        anchor["confirmed"] = None
    ctx.save(found)
    ctx.outcome = outcome
    return {"name": name, "candidates": candidates}


def find_tag(ctx, args):
    found = ctx.programme(args["programme_id"])
    because = "look up a tag id Qloo knows, so that a filter never uses a free-text tag"
    found, outcome, tags = ctx.ask(found, qloo.tags(args["query"]), "find_tag", because)
    ctx.require(outcome)
    planner.remember_tags(found, tags)
    ctx.save(found)
    ctx.outcome = outcome
    return {"tags": tags}


def board_view(found):
    """The candidates as a person reads them: the board with its evidence, the rest that is connected
    and in range, and the set-aside pile with the reason for each."""
    sections = rank.sections(found["candidates"])
    confirmed = len(store.confirmed_anchors(found))
    return {
        "range": found["range"],
        "counts": rank.tally(found["candidates"]),
        "board": [evidence(found, c) for c in sections["board"]],
        "also_connected": [{"entity_id": c["entityId"], "name": c["name"], "lens": c["lens"]}
                           for c in sections["also_connected"]],
        "set_aside": [{"entity_id": c["entityId"], "name": c["name"], "status": c["status"],
                       "reason": rank.label(c, found["range"], confirmed)} for c in sections["set_aside"]],
    }


def plan_acts(ctx, args):
    planned = planner.plan(ctx, ctx.programme(args["programme_id"]), args)
    found = planned.found
    view = board_view(found)
    return {
        "programme_id": found["id"],
        "stopped_because": planned.stopped,
        "stopped_text": planned.trace[-1]["because"],
        "calls": {"spent_by_this_plan": planned.spent, "spent_in_total": found["budget"]["spent"],
                  "cap": found["budget"]["cap"], "not_spent": found["budget"]["saved"]},
        **view,
        "trace": planned.trace,
    }


def check_trend(ctx, args):
    found = ctx.programme(args["programme_id"])
    held = store.candidate(found, args["entity_id"])
    because = "the person or agent asked for this candidate's direction"
    found, outcome, rows = ctx.ask(found, planner.trend_request(held, ctx.today), "check_trend", because,
                                   held["entityId"])
    if outcome in planner.TREND_ANSWERED:
        planner.apply_trend(found, held["entityId"], rows)
        ctx.save(found)
    ctx.require(outcome)
    ctx.outcome = outcome
    return evidence(found, store.candidate(found, held["entityId"]))


def explain_act(ctx, args):
    found = ctx.programme(args["programme_id"])
    return evidence(found, store.candidate(found, args["entity_id"]))


def suggest_slots(ctx, args):
    found = ctx.programme(args["programme_id"])
    found["nights"], backups = slots.propose(found)
    ctx.save(found)
    names = {c["entityId"]: c["name"] for c in found["candidates"]}
    return {
        "nights": [{"id": n["id"], "label": n["label"], "state": n["state"],
                    "proposed": n["proposed"], "proposed_name": names.get(n["proposed"]),
                    "assigned": n["assigned"]} for n in found["nights"]],
        "backups": [{"entity_id": entity_id, "name": names[entity_id]} for entity_id in backups],
    }


def draft_inquiry(ctx, args):
    found = ctx.programme(args["programme_id"])
    draft = inquiry.draft(found, args["entity_id"], f"d{len(found['inquiries']) + 1}", args.get("night_ids"))
    found["inquiries"].append(draft)
    ctx.save(found)
    return {"draft_id": draft["draftId"], "state": draft["state"], "text": draft["text"],
            "night_ids": draft["nightIds"], "words": inquiry.words(draft["text"])}


def get_programme(ctx, args):
    return ctx.programme(args["programme_id"])


def qloo_budget(ctx, args):
    runtime = ctx.runtime
    shown = runtime.ledger.snapshot(ctx.workspace_id)
    result = {
        "provider": runtime.provider.name,
        "mode": runtime.mode(),
        "key": ctx.settings.key_status,
        "agent_allowance": shown["agent"],
        "server_ceiling": shown["daily"],
        "cache": shown["cache"],
        "programme": None,
    }
    if args.get("programme_id") is not None:
        found = ctx.programme(args["programme_id"])
        budget = found["budget"]
        result["programme"] = {
            "data": DATA[found["provider"]],
            "cap": budget["cap"], "spent": budget["spent"], "left": max(0, budget["cap"] - budget["spent"]),
            "not_spent": budget["saved"], "stopped_because": budget["stoppedBecause"],
            "cache_hits": sum(1 for step in found["trace"] if step.get("cache") == "hit"),
        }
    return result


PROGRAMME = Arg("string", "The programme id returned by open_programme.", True)
ENTITY = Arg("string", "The id of a candidate this programme holds, as returned by plan_acts.", True)

REGISTRY = {tool.name: tool for tool in (
    Tool("open_programme",
         "Starts a programme for one venue: a label for the room, 1 to 6 open nights (each a short "
         "label such as 'Tue 3 Nov') and 2 to 6 names of acts the room's audience already loves "
         "(public performers, podcasts or shows only; each name is later sent to Qloo). Optionally "
         "limits the kinds of act to look for (music, podcast, comedy) and adds a capacity note such "
         "as '300 seats' that appears in drafted inquiries. Returns the programme id, the night ids "
         "the call cap, and whether the programme runs on `live` Qloo data or on `synthetic` sample "
         "data (synthetic only when the server's daily ceiling of live calls was already spent). Does "
         "NOT search anything, does NOT confirm any act, and never sends the label, the nights or the "
         "capacity note anywhere.",
         {"label": Arg("string", "The room's name, at most 60 characters.", True),
          "nights": Arg("array", "1 to 6 open nights, each a label of at most 40 characters.", True,
                        "string"),
          "anchors": Arg("array", "2 to 6 names of public acts the room's audience loves, at most 60 "
                                  "characters each.", True, "string"),
          "lenses": Arg("array", "Which kinds of act to look for: any of music, podcast, comedy. "
                                 "Default: all of them.", items="string"),
          "capacity_note": Arg("string", "A short capacity note such as '300 seats', at most 40 "
                                         "characters. It appears in drafts only if given.")},
         open_programme),
    Tool("find_anchor",
         "Searches Qloo once for one of the programme's act names and returns up to five candidate "
         "matches, each with an id, a name, a type, a popularity figure and a short disambiguation. "
         "Costs one call from the programme's budget, and a search that finds nothing still counts. "
         "Does NOT choose a match: only a person can say which candidate is the act meant, and "
         "nothing is planned from an act until they have.",
         {"programme_id": PROGRAMME,
          "name": Arg("string", "One of the act names the programme was opened with.", True)},
         find_anchor),
    Tool("find_tag",
         "Looks up Qloo tags for one word, such as 'stand-up' or 'folk', and remembers up to ten "
         "(id, name, type) as the tags this programme knows. Costs one call. Does NOT accept a tag "
         "id from anywhere else: plan_acts refuses a tag id that Qloo did not return here.",
         {"programme_id": PROGRAMME,
          "query": Arg("string", "The word to look up, at most 60 characters.", True)},
         find_tag),
    Tool("plan_acts",
         "Runs the planner. It needs at least two acts that a person has confirmed and refuses if an "
         "act still waits for that. For each kind of act it makes one wide Qloo call anchored on the "
         "confirmed acts, sorts the answer without further calls (above the room's popularity range, "
         "connected to too few acts), makes one more range-filtered call for a kind that comes up "
         "short, then checks the weekly trend of the best candidates one by one, only until one more "
         "candidate is ready than there are open nights. Returns the board of candidates with the "
         "evidence for each, the set-aside pile with the reason for each, the calls spent and not "
         "spent, why it stopped, and the trace. Does NOT exceed the programme's call cap or the "
         "agent's allowance, assign a night, contact anyone, or use an act nobody confirmed.",
         {"programme_id": PROGRAMME,
          "lens_tags": Arg("object", "Optional tag per kind of act, such as {'comedy': '<tag id>'}. "
                                     "Only tag ids this programme knows from find_tag are accepted."),
          "band": Arg("object", "Optional {min, max} popularity percentiles from 0 to 1 that replace "
                                "the room's range for this plan. The trace shows it."),
          "max_calls": Arg("integer", "Optional most calls this plan may spend, never more than the "
                                      "programme's cap leaves.")},
         plan_acts),
    Tool("check_trend",
         "Asks Qloo once for the weekly trend of one candidate the programme holds, and sets its "
         "trend to rising, steady, fading or unknown by this app's rule on Qloo's weekly rows. "
         "Returns the candidate's evidence. Does NOT run for an id the programme does not hold, and "
         "does NOT change which acts are confirmed or assigned.",
         {"programme_id": PROGRAMME, "entity_id": ENTITY},
         check_trend),
    Tool("explain_act",
         "Returns what is stored about one candidate: the confirmed acts it connects to with Qloo's "
         "scores, where its popularity sits against the room's range, its trend rows, its tags, and "
         "the rule that placed it where it is. Every figure says whose it is. Does NOT call Qloo and "
         "does NOT add any claim that is not in a stored response.",
         {"programme_id": PROGRAMME, "entity_id": ENTITY},
         explain_act),
    Tool("suggest_slots",
         "Proposes which ready candidate could go in which open night, never two acts of the same "
         "kind on back-to-back nights when another fits, and lists the ready candidates left over "
         "as backups. A proposal is only a proposal. Does NOT assign a night: a person does that. "
         "Does NOT call Qloo.",
         {"programme_id": PROGRAMME},
         suggest_slots),
    Tool("draft_inquiry",
         "Writes a short inquiry to an act's management from a fixed template, filled from the "
         "programme and the card of one candidate, for the given nights (default: every night not "
         "yet assigned). The text opens with a DRAFT line and has a placeholder where a sender's "
         "name goes. Does NOT invent a fee, a capacity or an availability, does NOT address or send "
         "anything, and can NOT produce text without the DRAFT line: a person approves a draft.",
         {"programme_id": PROGRAMME, "entity_id": ENTITY,
          "night_ids": Arg("array", "Optional night ids to ask about; each must be a night of this "
                                    "programme.", items="string")},
         draft_inquiry),
    Tool("get_programme",
         "Returns the whole programme as stored: nights and their states, anchors with their "
         "candidates and which one is confirmed, known tags, range, budget, candidates, drafts and "
         "the trace. Does NOT write anything and does NOT call Qloo.",
         {"programme_id": PROGRAMME},
         get_programme),
    Tool("qloo_budget",
         "Returns whether a key is set (never the key), which provider is active, the live calls "
         "this workspace's agent has used in the last 24 hours against its allowance, the server's "
         "daily ceiling used, the cache's size and hits, and, when a programme id is given, that "
         "programme's calls spent and left against its cap. Does NOT call Qloo and does NOT return "
         "the key or any part of it.",
         {"programme_id": Arg("string", "Optional: a programme id, to include that programme's cap.")},
         qloo_budget),
)}


READ_ONLY = ("explain_act", "get_programme", "qloo_budget")
CALLS_QLOO = ("find_anchor", "find_tag", "plan_acts", "check_trend")
REPEATABLE = READ_ONLY + ("suggest_slots",)

# MCP tool annotations: hints for a client, not a guarantee. Nothing here deletes anything.
ANNOTATIONS = {
    name: {
        "readOnlyHint": name in READ_ONLY,
        "destructiveHint": False,
        "idempotentHint": name in REPEATABLE,
        "openWorldHint": name in CALLS_QLOO,
    }
    for name in REGISTRY
}


def listing():
    return [{"name": tool.name, "description": tool.description, "inputSchema": schema(tool.args),
             "annotations": ANNOTATIONS[tool.name]} for tool in REGISTRY.values()]
