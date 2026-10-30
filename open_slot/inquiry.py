"""The inquiry to an act's management: a fixed template, DRAFT until a person approves it.

The text names no fee, no availability and no claim beyond what Qloo's figures say. It is
rendered from a snapshot of the inputs, so a later plan cannot change an approved inquiry.
"""

from .store import Refusal, confirmed_anchors

DRAFT_LINE = "DRAFT. Not approved. Do not send."
SENDER_NEEDED = "[sender name needed]"
MAX_WORDS = 120
SENDER_MAX = 60
# A programme on synthetic sample data never credits Qloo for the link it states.
EVIDENCE_SOURCE = {
    "live": "Qloo's taste data, which we use to find acts,",
    "fake": "Sample taste data, standing in for Qloo's,",
}


def _join(names):
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def render(snapshot, sender=None):
    """The draft text when there is no sender yet, the approved text (no DRAFT line) when there is."""
    capacity = f", {snapshot['capacityNote']}" if snapshot["capacityNote"] else ""
    evidence = ""
    if snapshot["evidence"]:
        evidence = (f" {EVIDENCE_SOURCE[snapshot['provider']]} connects {snapshot['act']} "
                    f"with {_join(snapshot['links'])}.")
    body = (
        f"Hello. I program {snapshot['label']}{capacity}. "
        f"Some of our audience's favourites are {_join(snapshot['anchors'])}.{evidence} "
        f"We have {_join(snapshot['nights'])} open and would like to ask whether {snapshot['act']} "
        "might be available for one of them, and what your terms would be. "
        "No offer is made by this message."
    )
    if sender is None:
        return f"{DRAFT_LINE}\n{body}\n{SENDER_NEEDED}"
    return f"{body}\n{sender}"


def words(text):
    return len(text.split())


def draft(found, entity_id, draft_id, night_ids=None, evidence=True):
    """A DRAFT inquiry about one candidate and some nights (default: every night not yet assigned).
    The evidence sentence goes out on its own when the candidate has no links, or when it is what
    keeps the text over MAX_WORDS."""
    candidate = next((c for c in found["candidates"] if c["entityId"] == entity_id), None)
    if candidate is None:
        raise Refusal("unknown_entity")
    chosen = [n for n in found["nights"] if n["id"] in night_ids] if night_ids is not None else [
        n for n in found["nights"] if not n["assigned"]]
    if not chosen or (night_ids is not None and len(chosen) != len(set(night_ids))):
        raise Refusal("bad_args", "night_ids must name nights of this programme, and at least one")
    anchors = confirmed_anchors(found)
    resolved = {a["name"]: a["resolvedName"] for a in anchors}
    snapshot = {
        "label": found["label"],
        "capacityNote": found["capacityNote"],
        "anchors": [a["resolvedName"] for a in anchors],
        "nights": [n["label"] for n in chosen],
        "act": candidate["name"],
        "links": [resolved[link["anchor"]] for link in candidate["links"]],
        "evidence": evidence and bool(candidate["links"]),
        "provider": found["provider"],
    }
    if words(render(snapshot)) > MAX_WORDS:
        snapshot["evidence"] = False
    if words(render(snapshot)) > MAX_WORDS:
        raise Refusal("draft_too_long", f"the inquiry would run past {MAX_WORDS} words")
    return {
        "draftId": draft_id,
        "entityId": entity_id,
        "nightIds": [n["id"] for n in chosen],
        "state": "draft",
        "text": render(snapshot),
        "approvedBy": None,
        "approvedAt": None,
        "revision": found["revision"],
        "snapshot": snapshot,
    }


def approve(inquiry, sender_name, at):
    """The approved copy of a draft: the sender named and the DRAFT line gone."""
    if not isinstance(sender_name, str):
        raise Refusal("sender_missing")
    sender = " ".join(sender_name.split())
    if not sender or len(sender) > SENDER_MAX or not sender.isprintable():
        raise Refusal("sender_missing", f"the sender's name is 1 to {SENDER_MAX} printable characters")
    return {
        **inquiry,
        "state": "approved",
        "text": render(inquiry["snapshot"], sender),
        "approvedBy": sender,
        "approvedAt": at,
    }
