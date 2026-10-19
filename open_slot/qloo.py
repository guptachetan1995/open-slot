"""Requests to, and readers of responses from, Qloo's hackathon API.

Qloo silently ignores a parameter it does not know for a type, so every request is built from
an allowlist per path and per filter.type, and anything else is refused here, before it is sent.
The response shapes below follow Qloo's published examples and are UNVERIFIED until the probe
has run with a key; a reader that meets a shape it does not know raises UnknownShape.
"""

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from . import pins

BASE_URL = "https://hackathon.api.qloo.com"

ARTIST = "urn:entity:artist"
PODCAST = "urn:entity:podcast"
PERSON = "urn:entity:person"
ENTITY_TYPES = (ARTIST, PODCAST, PERSON)
BIAS_LEVELS = ("off", "very_low", "low", "mid", "medium", "high", "very_high")

SEARCH = "/search"
TAGS = "/v2/tags"
INSIGHTS = "/v2/insights"
TRENDING = "/v2/trending"

NAME_MAX = 60
ANCHORS_MAX = 6

_INSIGHTS_PARAMS = frozenset({
    "filter.type", "signal.interests.entities", "filter.exclude.entities",
    "filter.popularity.min", "filter.popularity.max", "filter.tags", "bias.trends",
    "feature.explainability", "take",
})
_TRENDING_PARAMS = frozenset({
    "filter.type", "signal.interests.entities", "filter.start_date", "filter.end_date", "take",
})

# One entry per filter.type, so the probe can narrow a single type without touching the rest.
ALLOWED = {
    SEARCH: {None: frozenset({"query", "types", "take"})},
    TAGS: {None: frozenset({"filter.query", "take"})},
    INSIGHTS: {kind: _INSIGHTS_PARAMS for kind in ENTITY_TYPES},
    TRENDING: {kind: _TRENDING_PARAMS for kind in ENTITY_TYPES},
}
REQUIRED = {
    SEARCH: ("query",),
    TAGS: ("filter.query",),
    INSIGHTS: ("filter.type", "signal.interests.entities"),
    TRENDING: ("filter.type", "signal.interests.entities", "filter.start_date", "filter.end_date"),
}

OUTCOME_MESSAGES = {
    "qloo_auth": "Qloo did not accept the key. A hackathon key works only against the hackathon address.",
    "qloo_unsupported_type": "Qloo does not support that entity type for this key.",
    "qloo_rate_limited": "Qloo is rate limiting this key. Try again shortly.",
    "qloo_bad_request": "Qloo rejected the request.",
    "qloo_unavailable": "Qloo is not answering.",
    "qloo_no_results": "Qloo has nothing for that.",
    "qloo_unknown_shape": "Qloo's answer does not have the shape this app reads.",
}


class RequestRefused(ValueError):
    pass


class UnknownShape(ValueError):
    """`where` is a path in the response, never a value from it."""

    def __init__(self, where):
        super().__init__(f"the response does not have the expected shape at {where}")
        self.where = where


@dataclass(frozen=True)
class Request:
    path: str
    params: dict

    def query_string(self):
        return "&".join(f"{quote(k, safe='.')}={quote(v, safe=':,')}" for k, v in self.params.items())

    def url(self, base):
        return f"{base.rstrip('/')}{self.path}?{self.query_string()}"

    def cache_key(self):
        return self.path + "?" + "&".join(f"{k}={v}" for k, v in sorted(self.params.items()))


def _text(value, name, limit=NAME_MAX):
    if not isinstance(value, str):
        raise RequestRefused(f"{name} must be text")
    text = " ".join(value.split())
    if not text or len(text) > limit or not text.isprintable():
        raise RequestRefused(f"{name} must be 1 to {limit} printable characters")
    return text


def _ids(value, name, low, high):
    if not isinstance(value, (list, tuple)):
        raise RequestRefused(f"{name} must be a list of entity ids")
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"[^\s,]{1,64}", item):
            raise RequestRefused(f"{name} takes plain entity ids only, with no weights")
    if not low <= len(value) <= high:
        raise RequestRefused(f"{name} takes {low} to {high} entity ids")
    return ",".join(value)


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise RequestRefused(f"{name} must be a number from {low} to {high}")
    return value


def _day(value, name):
    try:
        return value if isinstance(value, date) else date.fromisoformat(value)
    except (TypeError, ValueError):
        raise RequestRefused(f"{name} must be a date written YYYY-MM-DD") from None


def _normalise(path, name, value):
    if name == "filter.type":
        if value not in ENTITY_TYPES:
            raise RequestRefused("filter.type must be an artist, podcast or person")
        return value
    if name == "types":
        if (not isinstance(value, (list, tuple)) or not value
                or not all(item in ENTITY_TYPES for item in value)):
            raise RequestRefused("types must list artist, podcast or person")
        return ",".join(dict.fromkeys(value))
    if name in ("query", "filter.query"):
        return _text(value, name)
    if name == "take":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
            raise RequestRefused("take must be a whole number from 1 to 50")
        return str(value)
    if name == "signal.interests.entities":
        one = path == TRENDING
        return _ids(value, name, 1, 1 if one else ANCHORS_MAX)
    if name == "filter.exclude.entities":
        return _ids(value, name, 1, ANCHORS_MAX)
    if name in ("filter.popularity.min", "filter.popularity.max"):
        return str(round(float(_number(value, name, 0, 1)), 4))
    if name == "filter.tags":
        if not isinstance(value, str) or not re.fullmatch(r"urn:tag:[^\s,]{1,120}", value):
            raise RequestRefused("filter.tags takes a tag id Qloo returned, shaped urn:tag:...")
        return value
    if name == "bias.trends":
        if value not in BIAS_LEVELS:
            raise RequestRefused("bias.trends must be one of Qloo's documented levels")
        return value
    if name == "feature.explainability":
        if not isinstance(value, bool):
            raise RequestRefused("feature.explainability must be true or false")
        return "true" if value else "false"
    return _day(value, name).isoformat()


def build(path, params):
    kinds = ALLOWED.get(path)
    if kinds is None:
        raise RequestRefused(f"{path} is not a path Open Slot calls")
    if None in kinds:
        allowed = kinds[None]
    else:
        kind = params.get("filter.type")
        if kind not in kinds:
            raise RequestRefused("filter.type must be an artist, podcast or person")
        allowed = kinds[kind]
    for name in params:
        if name not in allowed:
            raise RequestRefused(f"{name} is not an allowed parameter for {path}")
    for name in REQUIRED[path]:
        if name not in params:
            raise RequestRefused(f"{path} needs {name}")
    built = {name: _normalise(path, name, value) for name, value in params.items()}
    low, high = built.get("filter.popularity.min"), built.get("filter.popularity.max")
    if low is not None and high is not None and float(low) > float(high):
        raise RequestRefused("filter.popularity.min is above filter.popularity.max")
    start, end = built.get("filter.start_date"), built.get("filter.end_date")
    if start is not None and start > end:
        raise RequestRefused("filter.start_date is after filter.end_date")
    return Request(path, built)


def search(name, take=5):
    return build(SEARCH, {"query": name, "types": ENTITY_TYPES, "take": take})


def tags(word, take=10):
    return build(TAGS, {"filter.query": word, "take": take})


def insights(filter_type, anchors, exclude=(), tag=None, bias=None, popularity=None,
             explain=True, take=50):
    params = {"filter.type": filter_type, "signal.interests.entities": list(anchors)}
    if exclude:
        params["filter.exclude.entities"] = list(exclude)
    if tag is not None:
        params["filter.tags"] = tag
    if popularity is not None:
        params["filter.popularity.min"], params["filter.popularity.max"] = popularity
    if bias is not None:
        params["bias.trends"] = bias
    params["feature.explainability"] = explain
    params["take"] = take
    return build(INSIGHTS, params)


def trending(filter_type, entity_id, start, end, take=10):
    return build(TRENDING, {
        "filter.type": filter_type, "signal.interests.entities": [entity_id],
        "filter.start_date": start, "filter.end_date": end, "take": take,
    })


def outcome(status, path, items=None):
    if 200 <= status < 300:
        return "qloo_no_results" if items is not None and not items else "ok"
    if status == 401:
        return "qloo_auth"
    if status == 403:
        return "qloo_unsupported_type"
    if status == 404:
        return "qloo_no_results" if path == SEARCH else "qloo_bad_request"
    if status == 429:
        return "qloo_rate_limited"
    if status >= 500:
        return "qloo_unavailable"
    return "qloo_bad_request"


def _obj(value, where):
    if not isinstance(value, dict):
        raise UnknownShape(where)
    return value


def _list(value, where):
    if not isinstance(value, list):
        raise UnknownShape(where)
    return value


def _str(value, where):
    if not isinstance(value, str) or not value:
        raise UnknownShape(where)
    return value


def _opt_str(entry, key, where):
    value = entry.get(key)
    return value if value is None else _str(value, f"{where}.{key}")


def _opt_num(entry, key, where, low=None, high=None):
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnknownShape(f"{where}.{key}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise UnknownShape(f"{where}.{key}")
    return float(value)


def _num(entry, key, where, low=None, high=None):
    value = _opt_num(entry, key, where, low, high)
    if value is None:
        raise UnknownShape(f"{where}.{key}")
    return value


def _results(body):
    """Qloo's empty answer is {} or an empty results; any other body without `results` is a shape
    this reader does not know."""
    body = _obj(body, "body")
    if body and "results" not in body:
        raise UnknownShape("results")
    return body.get("results")


def _rows(body, key):
    results = _results(body)
    if not results:
        return []
    return _list(_obj(results, "results").get(key), f"results.{key}")


def read_search(body):
    entries = _results(body)
    if not entries:
        return []
    found = []
    for item in _list(entries, "results")[:5]:
        entry = _obj(item, "results[]")
        found.append({
            "entityId": _str(entry.get("entity_id"), "results[].entity_id"),
            "name": _str(entry.get("name"), "results[].name"),
            "type": _str(entry.get("type"), "results[].type"),
            "popularity": _opt_num(entry, "popularity", "results[]", 0, 1),
            "disambiguation": _opt_str(entry, "disambiguation", "results[]"),
        })
    return found


def read_tags(body):
    found = []
    for item in _rows(body, "tags")[:10]:
        entry = _obj(item, "results.tags[]")
        kinds = _list(entry.get("types"), "results.tags[].types")
        found.append({
            "id": _str(entry.get("tag_id"), "results.tags[].tag_id"),
            "name": _str(entry.get("name"), "results.tags[].name"),
            "type": _str(kinds[0], "results.tags[].types[]") if kinds else None,
        })
    return found


def _explain(query):
    block = query.get("explainability")
    if block is None:
        return None
    where = "results.entities[].query.explainability"
    listed = _list(_obj(block, where).get("signal.interests.entities"), where + ".signal.interests.entities")
    scores = []
    for item in listed:
        entry = _obj(item, where + ".signal.interests.entities[]")
        scores.append({
            "entityId": _str(entry.get("entity_id"), where + ".signal.interests.entities[].entity_id"),
            "score": _num(entry, "score", where + ".signal.interests.entities[]", 0, 1),
        })
    return scores


def read_insights(body):
    where = "results.entities[]"
    entities, explained = [], False
    for item in _rows(body, "entities"):
        entry = _obj(item, where)
        query = _obj(entry.get("query") or {}, where + ".query")
        measurements = _obj(query.get("measurements") or {}, where + ".query.measurements")
        scores = _explain(query)
        explained = explained or scores is not None
        entities.append({
            "entityId": _str(entry.get("entity_id"), where + ".entity_id"),
            "name": _str(entry.get("name"), where + ".name"),
            "type": _str(entry.get("type"), where + ".type"),
            "popularity": _opt_num(entry, "popularity", where, 0, 1),
            "tags": [_str(_obj(tag, where + ".tags[]").get("name"), where + ".tags[].name")
                     for tag in _list(entry.get("tags") or [], where + ".tags")],
            "growth": _opt_num(measurements, "audience_growth", where + ".query.measurements"),
            "explain": scores or [],
        })
    return {"entities": entities, "explainability": explained}


def read_trending(body):
    date_field, percentile_field = pins.TREND_FIELDS
    where = "results.trends[]"
    rows = []
    for item in _rows(body, "trends"):
        entry = _obj(item, where)
        stamp = _str(entry.get(date_field), f"{where}.{date_field}")
        try:
            date.fromisoformat(stamp[:10])
        except ValueError:
            raise UnknownShape(f"{where}.{date_field}") from None
        rows.append({
            date_field: stamp,
            percentile_field: _num(entry, percentile_field, where, 0, pins.PERCENTILE_SCALE),
            "population_rank": _opt_num(entry, "population_rank", where, 0),
        })
    return rows
