"""Constants the first live probe fixes. Nothing here is response data.

UNVERIFIED: no Qloo key existed when these were written, so every value is the documented
default or a stand-in. `python3 -m open_slot probe` prints the block that replaces them, and
VERIFIED becomes True only when that block has been pasted in.
"""

VERIFIED = False

CONFIRMED_LENSES = ("music", "podcast", "comedy")

# A tag of None means: look it up at run time with find_tag. A tag id is pinned only if Qloo's
# public documentation lists it, so none is guessed here.
LENS_DEFAULTS = {
    "music": {"type": "urn:entity:artist", "bias_trends": "medium", "tag": None},
    "podcast": {"type": "urn:entity:podcast", "bias_trends": "medium", "tag": None},
    "comedy": {"type": "urn:entity:person", "bias_trends": "medium", "tag": None},
}

# Smallest week-over-week change in population_percentile counted as movement. A stand-in: the
# probe's value is the median absolute change over the rows it reads, rounded to 0.1 points.
TREND_EPS = 0.5
TREND_FIELDS = ("date", "population_percentile")
PERCENTILE_SCALE = 100

# How far back check_trend asks for weekly rows. The probe reports the shortest window that
# returned enough rows; 8 weeks is the window it tries first.
TREND_WINDOW_DAYS = 56
