"""The room's range, links, candidate status and ranking.

Every figure here is either Qloo's (explanation score, popularity percentile) or this app's
rule applied to Qloo's figures, and the labels say which. No figure is combined into a score.
"""

import statistics

from . import trend

EXPLAIN_MIN = 0.1
BAND_DOWN = 0.15
BAND_UP = 0.05
SHORTLIST_SIZE = 8

STATUS_ORDER = ("ready", "watch", "fading", "weak_link", "outside_band", "dropped")
BOARD_STATUSES = ("ready", "watch")
IN_REACH = ("ready", "watch", "fading")
LENS_ORDER = ("music", "podcast", "comedy")


def min_links(confirmed):
    return min(2, confirmed)


def room_range(popularities, moved=None, moved_by=None):
    """From the anchors' popularity percentiles, or None when Qloo gave none (no range, no 'in reach').
    `moved` is a {min, max} the person or agent chose instead."""
    if moved is not None:
        low, high = moved["min"], moved["max"]
        if not 0 <= low <= high <= 1:
            raise ValueError("a range is min and max percentiles with 0 <= min <= max <= 1")
        return {"min": low, "max": high, "source": "moved", "movedBy": moved_by}
    known = [p for p in popularities if p is not None]
    if not known:
        return None
    return {
        "min": round(max(0.0, min(known) - BAND_DOWN), 4),
        "max": round(min(1.0, max(known) + BAND_UP), 4),
        "source": "anchors",
        "movedBy": None,
    }


def links(explain, anchors):
    """The confirmed anchors whose Qloo explanation score reaches EXPLAIN_MIN, strongest first.
    `anchors` are {name, entityId, ...}; a link names the anchor by the name that was typed."""
    names = {anchor["entityId"]: anchor["name"] for anchor in anchors}
    kept = [{"anchor": names[item["entityId"]], "score": item["score"]}
            for item in explain if item["entityId"] in names and item["score"] >= EXPLAIN_MIN]
    return sorted(kept, key=lambda link: -link["score"])


def build_candidates(entities, lens, anchors):
    """Candidates from one reader result, in Qloo's order. An anchor is never a candidate."""
    anchor_ids = {anchor["entityId"] for anchor in anchors}
    found = []
    for order, entity in enumerate(entities):
        if entity["entityId"] in anchor_ids:
            continue
        found.append({
            "entityId": entity["entityId"],
            "name": entity["name"],
            "type": entity["type"],
            "lens": lens,
            "popularity": entity["popularity"],
            "tags": entity["tags"],
            "growth": entity["growth"],
            "links": links(entity["explain"], anchors),
            "trend": None,
            "status": None,
            "dropped": None,
            "rankedBy": None,
            "qlooOrder": order,
        })
    return found


def outside(candidate, rng):
    popularity = candidate["popularity"]
    return rng is not None and popularity is not None and not rng["min"] <= popularity <= rng["max"]


def classify(candidate, rng, confirmed):
    if candidate["dropped"]:
        return "dropped"
    if outside(candidate, rng):
        return "outside_band"
    if len(candidate["links"]) < min_links(confirmed):
        return "weak_link"
    state = (candidate["trend"] or {}).get("state")
    if state == "fading":
        return "fading"
    if state in ("rising", "steady"):
        return "ready"
    return "watch"


def label(candidate, rng, confirmed):
    status = candidate["status"]
    if status == "dropped":
        return f"Dropped by you: {candidate['dropped']}"
    if status == "outside_band":
        popularity = candidate["popularity"]
        if popularity > rng["max"]:
            return (f"Above your room's range: Qloo popularity {popularity:.2f}; "
                    f"your range tops out at {rng['max']:.2f}")
        return (f"Below your room's range: Qloo popularity {popularity:.2f}; "
                f"your range starts at {rng['min']:.2f}")
    if status == "weak_link":
        return (f"Connects to {len(candidate['links'])} of your {confirmed} acts. "
                f"This app asks for {min_links(confirmed)}")
    if status == "fading":
        return (f"Qloo's weekly popularity percentile for it fell in at least {trend.TREND_MIN_MOVES} "
                f"of the last {trend.TREND_WEEKS - 1} weeks (this app's rule)")
    if status == "ready":
        return "Connected, in range, trend not falling"
    return "Connected and in range; trend not checked"


def qualifies(candidate):
    return candidate["status"] in IN_REACH


def needs_trend(candidate):
    return candidate["status"] == "watch" and candidate["trend"] is None


def _lens_position(candidate):
    lens = candidate["lens"]
    return LENS_ORDER.index(lens) if lens in LENS_ORDER else len(LENS_ORDER)


def _criteria(candidate, median):
    popularity, growth = candidate["popularity"], candidate["growth"]
    distance = None if popularity is None or median is None else abs(popularity - median)
    return (
        ("links", -len(candidate["links"])),
        ("growth", (growth is None, -(growth or 0.0))),
        ("closeness", (distance is None, distance or 0.0)),
        ("order", (candidate["qlooOrder"], _lens_position(candidate))),
    )


def assess(candidates, rng, anchors):
    """Statuses, then the order: ready before watch, and within a status by links (more first),
    then Qloo's growth figure if it gave one, then closeness of popularity to the anchors' median,
    then Qloo's own order. `rankedBy` names the first of these that placed the candidate."""
    known = [a["popularity"] for a in anchors if a.get("popularity") is not None]
    median = statistics.median(known) if known else None
    graded = [{**c, "status": classify(c, rng, len(anchors))} for c in candidates]
    keyed = {id(c): _criteria(c, median) for c in graded}
    graded.sort(key=lambda c: (STATUS_ORDER.index(c["status"]), *(value for _, value in keyed[id(c)])))
    for index, current in enumerate(graded):
        around = [graded[i] for i in (index + 1, index - 1) if 0 <= i < len(graded)]
        neighbour = next((other for other in around if other["status"] == current["status"]), None)
        current["rankedBy"] = "status" if neighbour is None else next(
            (name for (name, mine), (_, theirs) in zip(keyed[id(current)], keyed[id(neighbour)])
             if mine != theirs),
            "order",
        )
    return graded


def tally(assessed):
    counts = dict.fromkeys(STATUS_ORDER, 0)
    for candidate in assessed:
        counts[candidate["status"]] += 1
    return counts


def sections(assessed):
    """The board (at most SHORTLIST_SIZE), the rest that is connected and in range, and the
    set-aside pile with each reason on its label."""
    reachable = [c for c in assessed if c["status"] in BOARD_STATUSES]
    return {
        "board": reachable[:SHORTLIST_SIZE],
        "also_connected": reachable[SHORTLIST_SIZE:],
        "set_aside": [c for c in assessed if c["status"] not in BOARD_STATUSES],
    }
