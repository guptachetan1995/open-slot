"""The first-day probe: a few live calls that show what Qloo's answers look like.

It prints response shapes (JSON paths with their types), counts, status codes, which of five
thresholds pass, and a block of constants for pins.py. It never prints a name, an id, any text
from a response or the key, and it never retries: a retry would count against the budget.
"""

import json
import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from . import pins, qloo, transport, trend

BUDGET = 14
POPULARITY_MAX = 0.8
EXPECTED_TYPES = (qloo.ARTIST, qloo.PERSON, qloo.PODCAST)
NONSENSE = "zzqxjvkwpq"
UNSUPPORTED_TYPE = "urn:demographics"
FIELD_NAME = re.compile(r"[a-z][a-z0-9_.:-]{0,40}")
PERCENTILE, DATE = trend.PERCENTILE, trend.DATE


class BudgetSpent(Exception):
    pass


@dataclass
class Asked:
    """`status` is the HTTP status, or a fixed word when no answer came; `bad` is the response path
    a reader did not recognise."""

    status: object
    body: object
    read: object = None
    bad: str = None


def type_name(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "null" if value is None else "string"


def shape(value, path="", found=None, seen=frozenset()):
    """JSON paths with the types found there. A key is printed only if it looks like a field name
    and is not itself a string the response (or the key file) carried; any other key is <key>."""
    found = {} if found is None else found
    if isinstance(value, dict):
        found.setdefault(path or "(body)", set()).add("object")
        for key, item in value.items():
            name = key if FIELD_NAME.fullmatch(key) and key.lower() not in seen else "<key>"
            shape(item, f"{path}.{name}" if path else name, found, seen)
    elif isinstance(value, list):
        found.setdefault(path or "(body)", set()).add("array")
        for item in value:
            shape(item, path + "[]", found, seen)
    else:
        found.setdefault(path or "(body)", set()).add(type_name(value))
    return found


def strings_in(value):
    if isinstance(value, str):
        yield value.lower()
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


def scale(values):
    return [round(min(values), 1), round(max(values), 1)] if values else None


def count(asked):
    if asked.read is None:
        return None
    return len(asked.read["entities"] if isinstance(asked.read, dict) else asked.read)


class Probe:
    def __init__(self, key, names, base_url=qloo.BASE_URL, today=None, send=transport.send):
        self.key, self.names, self.base_url, self.send = key, names, base_url, send
        self.today = today or date.today()
        self.calls = 0
        self.seen = {key.lower()}
        self.shapes = {}
        self.ids, self.wide, self.eps = [], [], None
        self.report = {"budget": BUDGET}

    def ask(self, label, request, reader):
        """One live call, never retried, with its response shape recorded under `label`."""
        if self.calls >= BUDGET:
            raise BudgetSpent
        self.calls += 1
        try:
            reply = self.send(self.base_url, request, self.key)
        except transport.TransportError as error:
            return Asked(error.kind, None)
        self.seen.update(strings_in(reply.body))
        shape(reply.body, "", self.shapes.setdefault(label, {}), self.seen)
        asked = Asked(reply.status, reply.body)
        if reply.status == 200:
            try:
                asked.read = reader(reply.body)
            except qloo.UnknownShape as error:
                asked.bad = error.where
        return asked

    def insights(self, label, kind, ids, **options):
        return self.ask(label, qloo.insights(kind, ids, exclude=ids, take=20, **options), qloo.read_insights)

    def p1_search(self):
        info = {"status": [], "counts": [], "types_match": [], "has_popularity": [], "has_disambiguation": []}
        for name, expected in zip(self.names, EXPECTED_TYPES):
            asked = self.ask("p1_search", qloo.search(name), qloo.read_search)
            top = asked.read[0] if asked.read else None
            info["status"].append(asked.status)
            info["counts"].append(count(asked))
            info["types_match"].append(None if top is None else top["type"] == expected)
            info["has_popularity"].append(None if top is None else top["popularity"] is not None)
            info["has_disambiguation"].append(None if top is None else top["disambiguation"] is not None)
            if asked.bad:
                info["unreadable_at"] = asked.bad
            if top:
                self.ids.append(top["entityId"])
        info["comma_types_accepted"] = any(info["counts"])
        self.report["p1"] = info

    def p2_artists(self):
        band = (0.0, POPULARITY_MAX)
        first = self.insights("p2_artists", qloo.ARTIST, self.ids, popularity=band)
        calls, retried = 1, None
        if not (first.read and first.read["entities"]):
            retried = self.insights("p2_artists", qloo.ARTIST, self.ids[:1], popularity=band)
            calls = 2
            first = retried if retried.read and retried.read["entities"] else first
        entities = first.read["entities"] if first.read else []
        self.wide = entities
        popularity = [e["popularity"] for e in entities if e["popularity"] is not None]
        info = {
            "calls": calls,
            "status": first.status,
            "count": len(entities),
            "artist_count": sum(e["type"] == qloo.ARTIST for e in entities),
            "explainability_scores": any(e["explain"] for e in entities),
            "popularity_share": round(len(popularity) / len(entities), 2) if entities else None,
            "popularity_scale": scale(popularity),
            "with_tags": sum(bool(e["tags"]) for e in entities),
            "with_growth": sum(e["growth"] is not None for e in entities),
        }
        if first.bad:
            info["unreadable_at"] = first.bad
        if retried is not None:
            info["retry_with_other_anchors"] = {"status": retried.status, "count": count(retried)}
        if entities and calls == 1:
            wider = self.insights("p2_unfiltered", qloo.ARTIST, self.ids)
            info["calls"] = 2
            more = wider.read["entities"] if wider.read else []
            above = any(e["popularity"] is not None and e["popularity"] > POPULARITY_MAX for e in more)
            info["popularity_filter_changes_result"] = (len(more) != len(entities) or above) if more else None
        self.report["p2"] = info

    def p3_cross_domain(self):
        podcast = self.insights("p3_podcast", qloo.PODCAST, self.ids)
        info = {"podcast": {"status": podcast.status, "count": count(podcast)}}
        found, calls = [], 0
        for word in ("comedian", "stand-up"):
            asked = self.ask("p3_tags", qloo.tags(word), qloo.read_tags)
            calls += 1
            if asked.read:
                found = asked.read
                break
        tag = found[0]["id"] if found else None
        usable = bool(tag) and tag.startswith("urn:tag:")
        info["tags"] = {"calls": calls, "found": bool(found), "id_shaped_urn_tag": usable}
        if usable:
            person = self.insights("p3_person", qloo.PERSON, self.ids, tag=tag)
            info["comedy"] = {"status": person.status, "count": count(person)}
        self.report["p3"] = info

    def p4_trending(self):
        start, end = self.today - timedelta(days=pins.TREND_WINDOW_DAYS), self.today
        info = {"calls": 0, "per_call": []}
        series = []
        for entity in self.wide[:2]:
            asked = self.ask("p4_trending", qloo.trending(qloo.ARTIST, entity["entityId"], start, end),
                             qloo.read_trending)
            rows = asked.read or []
            info["calls"] += 1
            info["per_call"].append({"status": asked.status, "rows": count(asked), "unreadable_at": asked.bad,
                                     "enough_weeks": len(rows) >= trend.TREND_WEEKS})
            series.append(rows)
        pairs = []
        for rows in series:
            ordered = sorted(rows, key=lambda row: row[DATE])
            pairs += list(zip(ordered, ordered[1:]))
        changes = [abs(b[PERCENTILE] - a[PERCENTILE]) for a, b in pairs]
        self.eps = round(statistics.median(changes), 1) if changes else None
        ranked = [(a, b) for a, b in pairs if None not in (a["population_rank"], b["population_rank"])]
        moved = [(b[PERCENTILE] - a[PERCENTILE], b["population_rank"] - a["population_rank"])
                 for a, b in ranked
                 if a[PERCENTILE] != b[PERCENTILE] and a["population_rank"] != b["population_rank"]]
        agree = sum((percentile > 0) == (rank < 0) for percentile, rank in moved)
        info["entity_filter_honoured"] = len(series) == 2 and all(series) and series[0] != series[1]
        info["percentile_varies"] = bool(any(series)) and all(len({r[PERCENTILE] for r in rows}) > 1
                                                              for rows in series if rows)
        info["percentile_scale"] = scale([row[PERCENTILE] for rows in series for row in rows])
        info["rank_falls_when_percentile_rises"] = round(agree / len(moved), 2) if moved else None
        info["direction_check"] = bool(moved) and agree / len(moved) >= 0.8
        if series and series[0] and self.wide[0]["popularity"] is not None:
            gap = abs(trend.newest_fraction(series[0]) - self.wide[0]["popularity"])
            info["newest_percentile_vs_popularity"] = "within 0.1" if gap <= 0.1 else "differs by over 0.1"
        self.report["p4"] = info

    def p5_bias(self):
        high = self.insights("p5_bias_high", qloo.ARTIST, self.ids,
                             popularity=(0.0, POPULARITY_MAX), bias="high")
        shown = high.read["entities"] if high.read else []
        order, before = [e["entityId"] for e in shown], [e["entityId"] for e in self.wide]
        self.report["p5"] = {
            "calls": 1,
            "status": high.status,
            "count": len(order),
            "order_differs_from_default": order != before if order and before else None,
            "with_growth": sum(e["growth"] is not None for e in shown) if high.read else None,
        }

    def p6_errors(self):
        unsupported = self.ask("p6_unsupported_type", qloo.Request(qloo.INSIGHTS, {
            "filter.type": UNSUPPORTED_TYPE, "signal.interests.entities": self.ids[0]}), qloo.read_insights)
        nonsense = self.ask("p6_nonsense_search", qloo.search(NONSENSE), qloo.read_search)
        self.report["p6"] = {
            "unsupported_type": {"status": unsupported.status,
                                 "maps_to": self.outcome(unsupported, qloo.INSIGHTS)},
            "nonsense_search": {"status": nonsense.status, "maps_to": self.outcome(nonsense, qloo.SEARCH)},
        }

    @staticmethod
    def outcome(asked, path):
        return qloo.outcome(asked.status, path, asked.read) if isinstance(asked.status, int) else None

    def thresholds(self):
        found = self.report
        p2, p3, p4 = found.get("p2"), found.get("p3"), found.get("p4")

        def verdict(passed, why):
            return {"pass": bool(passed), "why": why}

        podcast = p3 and (p3["podcast"]["count"] or 0) >= 5
        comedy = p3 and (p3.get("comedy") or {}).get("count", 0) and p3["comedy"]["count"] >= 5
        trending = p4 and p4["calls"] == 2 and all(c["rows"] for c in p4["per_call"])
        return {
            "T1": verdict(p2 and p2["artist_count"] >= 10, "P2 returned at least 10 artist candidates"),
            "T2": {
                "podcast": verdict(podcast, "the podcast call returned at least 5 candidates"),
                "comedy": verdict(comedy, "a comedian tag exists and the tagged call returned at least 5"),
            },
            "T3": verdict(trending and p4["entity_filter_honoured"] and p4["percentile_varies"]
                          and p4["direction_check"],
                          "rows for both acts, different rows, percentile varies, rank falls as it rises"),
            "T4": verdict(p2 and p2["explainability_scores"], "explainability scores came back for artists"),
            "T5": verdict(p2 and (p2["popularity_share"] or 0) >= 0.8, "entities carry popularity"),
        }

    def pins_block(self, thresholds):
        lenses = ["music"] if thresholds["T1"]["pass"] else []
        lenses += [lens for lens in ("podcast", "comedy") if thresholds["T2"][lens]["pass"]]
        bias = "high" if (self.report.get("p5") or {}).get("order_differs_from_default") else "medium"
        top = max((self.report.get("p4") or {}).get("percentile_scale") or [0, 0])
        defaults = {lens: {**pins.LENS_DEFAULTS[lens], "bias_trends": bias, "tag": None} for lens in lenses}
        lines = [
            "VERIFIED = True",
            f"CONFIRMED_LENSES = {tuple(lenses)!r}",
            "LENS_DEFAULTS = " + json.dumps(defaults, indent=4).replace("null", "None"),
            f"TREND_EPS = {pins.TREND_EPS if self.eps is None else self.eps}",
            f"TREND_FIELDS = {pins.TREND_FIELDS!r}",
            f"PERCENTILE_SCALE = {1 if 0 < top <= 1.0 else 100}",
            f"TREND_WINDOW_DAYS = {pins.TREND_WINDOW_DAYS}",
        ]
        notes = []
        if not thresholds["T1"]["pass"]:
            notes.append("T1 did not pass: do not paste this block; report to the owner the same day.")
        if (self.report.get("p3") or {}).get("tags", {}).get("found"):
            notes.append("A comedian tag exists. Its id is not printed: pin it only if Qloo's public "
                         "documentation lists it, else leave tag None and let find_tag resolve it.")
        return "\n".join(lines), notes

    def run(self):
        self.p1_search()
        if len(self.ids) >= 2:
            for phase in (self.p2_artists, self.p3_cross_domain, self.p4_trending,
                          self.p5_bias, self.p6_errors):
                phase()
        else:
            self.report["stopped"] = "fewer than two of the three names resolved; later steps need their ids"
        thresholds = self.thresholds()
        block, notes = self.pins_block(thresholds)
        self.report.update({
            "calls_used": self.calls,
            "shapes": {label: sorted(f"{path}: {'|'.join(sorted(types))}" for path, types in found.items())
                       for label, found in self.shapes.items()},
            "thresholds": thresholds,
            "pins": block,
            "pins_notes": notes,
        })
        return self.report


def run(key, names, **kwargs):
    return Probe(key, names, **kwargs).run()


def format_report(report):
    body = {k: v for k, v in report.items() if k not in ("pins", "pins_notes")}
    notes = "".join(f"# {line}\n" for line in report["pins_notes"])
    return (json.dumps(body, indent=2) + "\n\n# constants for open_slot/pins.py\n"
            + notes + report["pins"] + "\n")


def render_notes(report):
    lines = ["# Qloo API notes", "",
             f"Written by the probe from {report['calls_used']} of {report['budget']} live calls. "
             "Shapes, counts, status codes and constants only: no name, id or value from a response.", ""]
    for label, paths in report["shapes"].items():
        lines += [f"## Shape: {label}", ""] + [f"- `{path}`" for path in paths] + [""]
    lines += ["## Thresholds", ""]
    for name, verdict in report["thresholds"].items():
        for label, item in ([(name, verdict)] if "pass" in verdict else verdict.items()):
            lines.append(f"- {name if label == name else f'{name} {label}'}: "
                         f"{'pass' if item['pass'] else 'FAIL'} ({item['why']})")
    measured = {k: v for k, v in report.items() if k in ("p1", "p2", "p3", "p4", "p5", "p6")}
    lines += ["", "## Measurements", "", "```json", json.dumps(measured, indent=2), "```", "",
              "## Constants for pins.py", "", "```python", report["pins"], "```", ""]
    return "\n".join(lines)
