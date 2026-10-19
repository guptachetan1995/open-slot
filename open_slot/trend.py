"""Weekly rows from Qloo's trending endpoint to rising, steady, fading or unknown.

The rule reads only the date and the population percentile. It is this app's arithmetic on
Qloo's rows, and the card says so; the other row fields mean nothing here until the probe
has fixed what they mean.
"""

from . import pins

TREND_WEEKS = 4
TREND_MIN_MOVES = 2

DATE, PERCENTILE = pins.TREND_FIELDS


class TrendRowError(ValueError):
    pass


def _percentile(row):
    value = row.get(PERCENTILE) if isinstance(row, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrendRowError(f"a weekly row has no numeric {PERCENTILE}")
    return value


def classify(rows):
    """`rows` come from qloo.read_trending in any order. Returns the state and what it rests on."""
    ordered = sorted(rows, key=lambda row: row[DATE], reverse=True)[:TREND_WEEKS]
    for row in ordered:
        _percentile(row)
    # Rounded so that a change of exactly TREND_EPS is not pushed over the line by float noise.
    changes = [round(_percentile(newer) - _percentile(older), 6)
               for newer, older in zip(ordered, ordered[1:])]
    if len(ordered) < TREND_MIN_MOVES + 1:
        state = "unknown"
    else:
        up = sum(change > pins.TREND_EPS for change in changes)
        down = sum(change < -pins.TREND_EPS for change in changes)
        if up >= TREND_MIN_MOVES and changes[0] >= 0:
            state = "rising"
        elif down >= TREND_MIN_MOVES and changes[0] <= 0:
            state = "fading"
        else:
            state = "steady"
    return {
        "state": state,
        "weeks": len(ordered),
        "rows": ordered,
        "changes": changes,
        "rule": f"TREND_MIN_MOVES={TREND_MIN_MOVES} of {TREND_WEEKS - 1} changes, "
                f"each beyond {pins.TREND_EPS} points of {PERCENTILE}",
    }


def newest_fraction(rows):
    """The newest percentile on the 0 to 1 scale Insights uses for popularity."""
    newest = max(rows, key=lambda row: row[DATE])
    return _percentile(newest) / pins.PERCENTILE_SCALE
