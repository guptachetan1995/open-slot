"""The planner: a wide pass per kind of act, then depth where it can change the answer.

Every call, skip and stop goes into the programme's trace with its reason. The planner works only
from acts a person confirmed, never falls back to synthetic data, and stops at the first of: enough
ready candidates, the programme's cap, the agent's allowance, the server's ceiling, Qloo refusing
twice running, or nothing left that is worth a trend check.
"""

from dataclasses import dataclass
from datetime import timedelta

from . import pins, qloo, rank, store, trend
from .store import Refusal

AGENT = "agent"
ENOUGH, EXHAUSTED, TRENDS_OFF = "enough_ready", "shortlist_exhausted", "trends_off"
LIMIT_STOPS = ("cap", "agent_allowance", "ceiling")
# The ledger can refuse the very attempt that headroom() had just allowed, when another workspace
# took the last call in between. That is the same stop, not an error.
SPENT_STOPS = {"agent_allowance_spent": "agent_allowance", "daily_allowance_spent": "ceiling"}
REFUSED = "qloo_refused"

# False when the probe's threshold T3 fails: the trend rule does not stand, nothing can be
# `ready`, and the plan runs its wide calls only.
TREND_CHECKS = True

TAG_WORDS = ("stand-up", "standup", "stand up", "comedian")
TAG_SEARCHES = ("stand-up", "comedian")
REFUSED_OUTCOMES = ("qloo_rate_limited", "qloo_unavailable")
TREND_ANSWERED = ("ok", "qloo_no_results", "qloo_unsupported_type", "qloo_bad_request")


class Stop(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass
class Planned:
    """The programme as stored after the plan, why it stopped, the calls this plan spent and the
    trace steps it wrote."""

    found: dict
    stopped: str
    spent: int
    trace: list


def _n(count, noun):
    return f"{count} {noun}" + ("" if count == 1 else "s")


def reassess(found):
    found["candidates"] = rank.assess(found["candidates"], found["range"], store.confirmed_anchors(found))


def remember_tags(found, tags):
    known = {tag["id"]: tag for tag in found["knownTags"]}
    known.update({tag["id"]: tag for tag in tags})
    found["knownTags"] = list(known.values())


def comedy_tag(found):
    pinned = pins.LENS_DEFAULTS["comedy"]["tag"]
    if pinned:
        return pinned
    for word in TAG_WORDS:
        for tag in found["knownTags"]:
            if word in tag["name"].casefold():
                return tag["id"]
    return None


def trend_request(held, today):
    start = today - timedelta(days=pins.TREND_WINDOW_DAYS)
    return qloo.trending(held["type"], held["entityId"], start, today)


def apply_trend(found, entity_id, rows):
    store.candidate(found, entity_id)["trend"] = trend.classify(rows or [])
    reassess(found)


def confirmed_for_plan(found):
    """The confirmed anchors, or the named refusal that says what a person still has to do."""
    anchors = store.confirmed_anchors(found)
    waiting = [a["name"] for a in found["anchors"] if a["candidates"] and a["confirmed"] is None]
    if waiting:
        raise Refusal("anchor_unconfirmed",
                      f"{_n(len(waiting), 'act')} found but not confirmed ({', '.join(waiting)}): "
                      "only a person can say which match is the act meant")
    if len(anchors) < 2:
        raise Refusal("needs_anchors",
                      f"{len(anchors)} of {len(found['anchors'])} acts are confirmed and a plan needs "
                      "at least 2: search for each name, then a person confirms the matches")
    return anchors


def _options(found, args):
    known = {tag["id"] for tag in found["knownTags"]}
    chosen = args.get("lens_tags") or {}
    for lens, tag in chosen.items():
        if lens not in found["lenses"]:
            raise Refusal("bad_args", "lens_tags names a kind of act this programme does not look for")
        if not isinstance(tag, str) or tag not in known:
            raise Refusal("unknown_tag", "plan_acts takes only tag ids this programme knows from find_tag")
    band = args.get("band")
    if band is not None and (set(band) != {"min", "max"} or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in band.values())):
        raise Refusal("bad_args", "band is {min, max}: two percentiles from 0 to 1")
    limit = args.get("max_calls")
    if limit is not None and limit < 1:
        raise Refusal("bad_args", "max_calls must be at least 1")
    return chosen, band, limit


def plan(ctx, found, args):
    anchors = confirmed_for_plan(found)
    chosen, band, max_calls = _options(found, args)
    try:
        rng = rank.room_range([a["popularity"] for a in anchors], band, ctx.actor)
    except ValueError as invalid:
        raise Refusal("bad_args", str(invalid)) from None
    started = found["budget"]["spent"]
    run = Plan(ctx, found, anchors, rng, None if max_calls is None else started + max_calls, chosen)
    run.start()
    try:
        stopped = run.search()
    except Stop as halt:
        stopped = halt.code
    return run.finish(stopped, started)


class Plan:
    def __init__(self, ctx, found, anchors, rng, limit, chosen):
        self.ctx, self.found, self.anchors, self.rng = ctx, found, anchors, rng
        self.limit, self.chosen = limit, chosen
        self.ids = [anchor["entityId"] for anchor in anchors]
        self.nights = sum(1 for night in found["nights"] if night["assigned"] is None)
        self.previous = {held["entityId"]: held for held in found["candidates"]}
        self.first = len(found["trace"]) + 1
        self.by_lens, self.tags = {}, {}
        self.tried, self.refused, self.triaged = set(), 0, False

    def note(self, kind, because, **fields):
        store.trace_step(self.found, kind=kind, tool="plan_acts", actor=AGENT, because=because, **fields)

    def ask(self, request, tool, because, entity_id=None):
        """One call with the limits checked first. Returns the outcome and what the reader made of
        the answer, which is None when Qloo gave none."""
        code = self.ctx.headroom(self.found, self.limit)
        if code:
            raise Stop(code)
        try:
            self.found, outcome, parsed = self.ctx.ask(
                self.found, request, tool, because, entity_id, self.limit, AGENT)
        except Refusal as spent:
            if spent.code in SPENT_STOPS:
                raise Stop(SPENT_STOPS[spent.code]) from None
            raise
        self.ctx.sub_activity("insights" if tool == "plan_acts" else tool,
                              {"programme_id": self.found["id"], "via": "plan_acts"}, outcome)
        if outcome == "qloo_auth":
            raise Refusal(outcome, qloo.OUTCOME_MESSAGES[outcome])
        self.refused = self.refused + 1 if outcome in REFUSED_OUTCOMES else 0
        if self.refused >= 2:
            raise Stop(REFUSED)
        return outcome, parsed

    def start(self):
        rng = self.rng
        if rng is None:
            text = ("no range: Qloo gave no popularity for your acts, so nothing is set aside as above or "
                    "below your room")
        else:
            how = (f"set by the {rng['movedBy']}" if rng["source"] == "moved" else
                   f"from your {len(self.anchors)} confirmed acts (this app's rule: lowest minus "
                   f"{rank.BAND_DOWN}, highest plus {rank.BAND_UP})")
            text = f"your room's range is Qloo popularity {rng['min']:.2f} to {rng['max']:.2f}, {how}"
        self.note("note", text)

    def carry(self, candidates):
        """What the person decided and what a trend check already found survive a re-run."""
        for held in candidates:
            before = self.previous.get(held["entityId"])
            if before is not None:
                held["dropped"], held["trend"] = before["dropped"], before["trend"]
        return candidates

    def insights(self, lens, popularity=None):
        default = pins.LENS_DEFAULTS[lens]
        return qloo.insights(default["type"], self.ids, exclude=self.ids, tag=self.tags[lens],
                             bias=default["bias_trends"], popularity=popularity)

    def tag_for(self, lens):
        if lens in self.chosen:
            return self.chosen[lens]
        if pins.LENS_DEFAULTS[lens]["type"] != qloo.PERSON:
            return None
        tag = comedy_tag(self.found)
        for word in TAG_SEARCHES:
            if tag is not None:
                break
            because = (f"{lens}: this kind needs a comedian or stand-up tag id and the programme "
                       f"knows none, so look up '{word}'")
            _, tags = self.ask(qloo.tags(word), "find_tag", because)
            remember_tags(self.found, tags or [])
            tag = comedy_tag(self.found)
        return tag

    @staticmethod
    def explained(read):
        if read["entities"] and not read["explainability"]:
            raise qloo.UnknownShape("results.entities[].query.explainability")

    def wide(self, lens):
        tag = self.tag_for(lens)
        if tag is None and pins.LENS_DEFAULTS[lens]["type"] == qloo.PERSON:
            self.note("skip", f"{lens}: no comedian or stand-up tag was found, so this kind is left out")
            return
        self.tags[lens] = tag
        because = (f"{lens}: one wide call anchored on your {len(self.ids)} confirmed acts"
                   f"{'' if tag is None else ', filtered by the tag Qloo returned'}; no popularity "
                   "filter, so acts above your room stay visible as set aside")
        outcome, read = self.ask(self.insights(lens), "plan_acts", because)
        if read is None:
            self.note("skip", f"{lens}: Qloo could not answer ({outcome}), so this kind is left out")
            return
        self.explained(read)
        self.by_lens[lens] = self.carry(rank.build_candidates(read["entities"], lens, self.anchors))

    def kept_over(self):
        """Acts a night or a draft already refers to stay, even if this pass did not return them."""
        referenced = {night["assigned"] for night in self.found["nights"]}
        referenced |= {draft["entityId"] for draft in self.found["inquiries"]}
        fresh = {held["entityId"] for held in sum(self.by_lens.values(), [])}
        return [held for entity_id, held in self.previous.items()
                if entity_id in referenced and entity_id not in fresh]

    def triage(self):
        every = sum((self.by_lens.get(lens, []) for lens in rank.LENS_ORDER), []) + self.kept_over()
        self.found["range"] = self.rng
        self.found["candidates"] = rank.assess(every, self.rng, self.anchors)
        for night in self.found["nights"]:
            if night["state"] == "proposed":
                night.update(state="open", proposed=None)
        counts = rank.tally(self.found["candidates"])
        reach = sum(rank.qualifies(held) for held in self.found["candidates"])
        self.note("note", f"triage, no call: {len(every)} candidates; {counts['outside_band']} outside your "
                          f"room's range, {counts['weak_link']} connected to fewer than "
                          f"{_n(rank.min_links(len(self.anchors)), 'act')} of yours, {reach} qualify")
        self.triaged = True

    def fill_short(self):
        need = self.nights + 1
        for lens in list(self.by_lens):
            held = [c for c in self.found["candidates"] if c["lens"] == lens]
            reach = sum(rank.qualifies(c) for c in held)
            if reach >= need:
                continue
            if self.rng is None:
                self.note("skip", f"{lens}: {reach} in reach, {need} needed, and Qloo gave no popularity "
                                  "to build a range from, so no filtered call is made")
                continue
            because = (f"{lens}: only {reach} connected acts in range for {_n(self.nights, 'open night')} "
                       f"({need} needed); the unfiltered 50 were spent on acts above the room, and only "
                       "a filtered call can find the ones in reach")
            request = self.insights(lens, (self.rng["min"], self.rng["max"]))
            _, read = self.ask(request, "plan_acts", because)
            if read is None or not read["entities"]:
                continue
            self.explained(read)
            known = {c["entityId"] for c in held}
            added = [c for c in self.carry(rank.build_candidates(read["entities"], lens, self.anchors))
                     if c["entityId"] not in known]
            for new in added:
                new["qlooOrder"] += len(held)
            self.found["candidates"] = rank.assess(self.found["candidates"] + added, self.rng, self.anchors)

    def deepen(self):
        while True:
            held = self.found["candidates"]
            if sum(c["status"] == "ready" for c in held) >= self.nights + 1:
                return ENOUGH
            todo = next((c for c in held if rank.needs_trend(c) and c["entityId"] not in self.tried), None)
            if todo is None:
                return EXHAUSTED
            because = (f"connected to {len(todo['links'])} of {len(self.anchors)} acts, in range, "
                       "and no trend yet")
            outcome, rows = self.ask(trend_request(todo, self.ctx.today), "check_trend", because,
                                     todo["entityId"])
            self.tried.add(todo["entityId"])
            if outcome in TREND_ANSWERED:
                apply_trend(self.found, todo["entityId"], rows)

    def search(self):
        for lens in rank.LENS_ORDER:
            if lens in self.found["lenses"]:
                self.wide(lens)
        self.triage()
        self.fill_short()
        if not TREND_CHECKS:
            return TRENDS_OFF
        return self.deepen()

    def finish(self, stopped, started):
        if not self.triaged and self.by_lens:
            self.triage()
        found = self.found
        saved = 0 if stopped == TRENDS_OFF else sum(
            rank.needs_trend(c) and c["entityId"] not in self.tried for c in found["candidates"])
        ready = sum(c["status"] == "ready" for c in found["candidates"])
        cap = found["budget"]["cap"]
        texts = {
            ENOUGH: f"{_n(ready, 'candidate')} {'is' if ready == 1 else 'are'} ready for "
                    f"{_n(self.nights, 'night')}; {_n(saved, 'call')} not spent",
            EXHAUSTED: f"every candidate that qualifies for a trend check has one; {_n(ready, 'candidate')} "
                       f"ready for {_n(self.nights, 'night')}",
            TRENDS_OFF: "trend checks are off, so candidates are watch at best and nothing is ready",
            "cap": f"the programme's cap of {cap} live calls is reached; {_n(saved, 'trend check')} not run",
            "agent_allowance": "the agent's daily allowance of live Qloo calls is spent for this workspace",
            "ceiling": "the server's daily ceiling of live Qloo calls is spent",
            REFUSED: "Qloo refused twice running (rate limit or server error) after retries; nothing "
                     "falls back to synthetic data",
        }
        found["budget"]["saved"], found["budget"]["stoppedBecause"] = saved, stopped
        self.note("stop", f"stopped: {texts[stopped]}", code=stopped)
        found = self.ctx.save(found)
        return Planned(found, stopped, found["budget"]["spent"] - started, found["trace"][self.first - 1:])
