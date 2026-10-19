"""The five verbs only a person can run.

They are deliberately absent from tools.REGISTRY, so no listing, no agent address and no MCP tool
list can offer them, and invoke() refuses each for any actor but the person. What they change is
what the app never lets an agent decide: which match is the act meant, which night an act gets,
what an inquiry says it is approved to send, which candidate leaves the board and how much a
programme may spend.
"""

from . import config, inquiry, planner, store
from .store import Refusal
from .tools import Arg, Tool

REASON_MAX = 120


def _fresh(found, revision):
    if revision != found["revision"]:
        raise Refusal("stale_revision", "the evidence changed since it was read; read it again first")


def confirm_anchor(ctx, args):
    found = ctx.programme(args["programme_id"])
    anchor = store.anchor_named(found, args["name"])
    if args["entity_id"] not in {c["entityId"] for c in anchor["candidates"]}:
        raise Refusal("unknown_entity", "that id is not one of the candidates found for this name")
    anchor["confirmed"] = args["entity_id"]
    saved = ctx.save(found)
    return {"name": anchor["name"], "entity_id": args["entity_id"], "revision": saved["revision"]}


def assign_slot(ctx, args):
    found = ctx.programme(args["programme_id"])
    _fresh(found, args["revision"])
    night = next((n for n in found["nights"] if n["id"] == args["night_id"]), None)
    if night is None:
        raise Refusal("bad_args", "night_id must be one of this programme's nights")
    entity_id = args["entity_id"]
    if entity_id is None:
        night.update(state="open", assigned=None, proposed=None)
    else:
        if store.candidate(found, entity_id)["dropped"]:
            raise Refusal("bad_args", "that act was dropped; undo the drop before assigning it")
        if night["assigned"] not in (None, entity_id):
            raise Refusal("slot_taken", "that night already holds another act; clear it first")
        night.update(state="assigned", assigned=entity_id, proposed=None)
    saved = ctx.save(found)
    return {"night_id": night["id"], "state": night["state"], "assigned": night["assigned"],
            "revision": saved["revision"]}


def approve_inquiry(ctx, args):
    found = ctx.programme(args["programme_id"])
    _fresh(found, args["revision"])
    draft = next((d for d in found["inquiries"] if d["draftId"] == args["draft_id"]), None)
    if draft is None:
        raise Refusal("bad_args", "draft_id must be a draft of this programme")
    if draft["state"] == "approved":
        raise Refusal("bad_args", "that inquiry is already approved and is frozen")
    approved = inquiry.approve(draft, args.get("sender_name"), ctx.now)
    found["inquiries"] = [approved if d is draft else d for d in found["inquiries"]]
    saved = ctx.save(found)
    return {"draft_id": approved["draftId"], "state": approved["state"], "text": approved["text"],
            "approved_by": approved["approvedBy"], "revision": saved["revision"]}


def drop_act(ctx, args):
    found = ctx.programme(args["programme_id"])
    held = store.candidate(found, args["entity_id"])
    if args.get("undo"):
        held["dropped"] = None
    else:
        reason = store.clean(args.get("reason"), "reason", REASON_MAX)
        if any(night["assigned"] == held["entityId"] for night in found["nights"]):
            raise Refusal("bad_args", "that act holds a night; clear the night before dropping it")
        held["dropped"] = reason
        for night in found["nights"]:
            if night["proposed"] == held["entityId"]:
                night.update(state="open", proposed=None)
    planner.reassess(found)
    saved = ctx.save(found)
    return {"entity_id": held["entityId"], "dropped": held["dropped"], "revision": saved["revision"]}


def raise_call_cap(ctx, args):
    found = ctx.programme(args["programme_id"])
    cap = args["cap"]
    if not found["budget"]["cap"] < cap <= config.MAX_CALLS_RAISED:
        raise Refusal("bad_args", f"a cap can only be raised, to at most {config.MAX_CALLS_RAISED}")
    found["budget"]["cap"] = cap
    saved = ctx.save(found)
    return {"cap": cap, "revision": saved["revision"]}


PROGRAMME = Arg("string", "The programme id.", True)
REVISION = Arg("integer", "The programme revision the person read; a different one is refused as stale.",
               True)

VERBS = {tool.name: tool for tool in (
    Tool("confirm_anchor",
         "Confirms that one of the candidates found for a name is the act meant.",
         {"programme_id": PROGRAMME,
          "name": Arg("string", "One of the act names the programme was opened with.", True),
          "entity_id": Arg("string", "One of the candidate ids found for that name.", True)},
         confirm_anchor),
    Tool("assign_slot",
         "Assigns a candidate to a night, or clears the night when entity_id is null.",
         {"programme_id": PROGRAMME,
          "night_id": Arg("string", "A night id of the programme.", True),
          "entity_id": Arg(("string", "null"), "A candidate id, or null to clear the night.", True),
          "revision": REVISION},
         assign_slot),
    Tool("approve_inquiry",
         "Approves a draft: names the sender, drops the DRAFT line and freezes the text.",
         {"programme_id": PROGRAMME,
          "draft_id": Arg("string", "A draft id of the programme.", True),
          "sender_name": Arg(("string", "null"), "The sender's name, 1 to 60 characters."),
          "revision": REVISION},
         approve_inquiry),
    Tool("drop_act",
         "Moves a candidate off the board with a reason, or back with undo.",
         {"programme_id": PROGRAMME,
          "entity_id": Arg("string", "A candidate id of the programme.", True),
          "reason": Arg("string", f"Why, at most {REASON_MAX} characters; needed unless undo."),
          "undo": Arg("boolean", "True brings a dropped candidate back.")},
         drop_act),
    Tool("raise_call_cap",
         "Raises the programme's live-call cap, up to the most the app allows.",
         {"programme_id": PROGRAMME, "cap": Arg("integer", "The new cap.", True)},
         raise_call_cap),
)}
