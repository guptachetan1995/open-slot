"""Which ready candidate could fit which night. A proposal only: assigning is the person's."""


def propose(found):
    """Proposals for every night the person has not assigned, in order, from the programme's `ready`
    candidates in rank order. A candidate is proposed once, never next to an act of the same kind
    when another fits, and an act already assigned to a night is not proposed again.
    Returns the nights (assigned ones untouched) and the ready candidates left over as backups."""
    assigned = {night["assigned"] for night in found["nights"] if night["assigned"]}
    lens_of = {c["entityId"]: c["lens"] for c in found["candidates"]}
    waiting = [c for c in found["candidates"] if c["status"] == "ready" and c["entityId"] not in assigned]
    nights, previous = [], None
    for night in found["nights"]:
        if night["assigned"]:
            nights.append(night)
            previous = lens_of.get(night["assigned"])
            continue
        pick = next((c for c in waiting if c["lens"] != previous), waiting[0] if waiting else None)
        if pick is None:
            nights.append({**night, "state": "open", "proposed": None})
            previous = None
            continue
        waiting.remove(pick)
        nights.append({**night, "state": "proposed", "proposed": pick["entityId"]})
        previous = pick["lens"]
    return nights, [c["entityId"] for c in waiting]
