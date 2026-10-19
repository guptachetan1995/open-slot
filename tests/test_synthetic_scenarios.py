"""The synthetic scenario files drive the rules to the counts the planner's traces are written
against: what a wide call returns, how triage sorts it, and which candidates the trend check
reaches first. The planner's own steps are tested with the planner."""

import json
import unittest

from open_slot import pins, providers, qloo, rank, trend
from tests.support import ROOT

TAG = "urn:tag:synthetic:stand-up"
FAKE = providers.FakeProvider()


def scenario(name):
    return json.loads((ROOT / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))


def confirm(names):
    anchors = []
    for name in names:
        top = qloo.read_search(FAKE.fetch(qloo.search(name)).body)[0]
        anchors.append({"name": name, "entityId": top["entityId"], "popularity": top["popularity"]})
    return anchors


def wide(anchors, lens, tag=None, popularity=None):
    ids = [a["entityId"] for a in anchors]
    kind = pins.LENS_DEFAULTS[lens]["type"]
    request = qloo.insights(kind, ids, exclude=ids, tag=tag, popularity=popularity)
    read = qloo.read_insights(FAKE.fetch(request).body)
    return rank.build_candidates(read["entities"], lens, anchors)


def check_trend(candidate):
    request = qloo.trending(candidate["type"], candidate["entityId"], "2026-08-24", "2026-10-19")
    return {**candidate, "trend": trend.classify(qloo.read_trending(FAKE.fetch(request).body))}


def readied(assessed, rng, anchors, open_nights):
    """Deepen in rank order until there is one more ready candidate than open nights."""
    candidates, spent = list(assessed), 0
    while sum(c["status"] == "ready" for c in candidates) < open_nights + 1:
        todo = next((c for c in candidates if rank.needs_trend(c)), None)
        if todo is None:
            break
        candidates = rank.assess([check_trend(todo) if c is todo else c for c in candidates], rng, anchors)
        spent += 1
    return candidates, spent


class ScenarioFiles(unittest.TestCase):
    def test_the_sample_programmes_are_names_and_nights_only(self):
        lantern = scenario("lantern-room")
        self.assertEqual(set(lantern), {"label", "capacity_note", "nights", "anchors"})
        self.assertEqual(len(lantern["anchors"]), 4)
        self.assertEqual(len(lantern["nights"]), 3)
        campus = scenario("campus-board")
        self.assertEqual((len(campus["anchors"]), len(campus["nights"]), campus["lenses"]),
                         (2, 2, ["comedy"]))

    def test_every_anchor_of_both_files_resolves_on_the_fake(self):
        for name in ("lantern-room", "campus-board"):
            for typed in scenario(name)["anchors"]:
                self.assertEqual(FAKE.fetch(qloo.search(typed)).status, 200, typed)


class ScenarioA(unittest.TestCase):
    def setUp(self):
        self.anchors = confirm(scenario("lantern-room")["anchors"])
        self.rng = rank.room_range([a["popularity"] for a in self.anchors])
        self.candidates = (wide(self.anchors, "music") + wide(self.anchors, "podcast")
                           + wide(self.anchors, "comedy", tag=TAG))

    def test_the_room_range(self):
        self.assertEqual((self.rng["min"], self.rng["max"]), (0.4, 0.76))

    def test_the_wide_calls_return_38_and_triage_sorts_them_with_no_further_call(self):
        self.assertEqual([len(wide(self.anchors, lens, tag=TAG if lens == "comedy" else None))
                          for lens in ("music", "podcast", "comedy")], [14, 12, 12])
        counts = rank.tally(rank.assess(self.candidates, self.rng, self.anchors))
        self.assertEqual(len(self.candidates), 38)
        self.assertEqual((counts["outside_band"], counts["weak_link"], counts["watch"]), (12, 14, 12))
        shown = rank.sections(rank.assess(self.candidates, self.rng, self.anchors))
        self.assertEqual(len(shown["set_aside"]), 26)

    def test_no_lens_comes_up_short_for_three_nights_so_there_is_no_step_2b(self):
        for lens in ("music", "podcast", "comedy"):
            found = rank.assess(wide(self.anchors, lens, tag=TAG if lens == "comedy" else None),
                                self.rng, self.anchors)
            self.assertGreaterEqual(sum(rank.qualifies(c) for c in found), 3 + 1, lens)

    def test_four_become_ready_after_four_trend_calls_and_eight_are_not_checked(self):
        assessed = rank.assess(self.candidates, self.rng, self.anchors)
        deepened, spent = readied(assessed, self.rng, self.anchors, open_nights=3)
        self.assertEqual(spent, 4)
        self.assertEqual(sum(c["status"] == "ready" for c in deepened), 4)
        self.assertEqual(sum(rank.needs_trend(c) for c in deepened), 8)
        self.assertEqual([c["name"] for c in deepened[:4]],
                         ["Hollowmere Radio", "Odile Thackeray", "The Gallowglass Hour", "Lindenwick Trio"])

    def test_the_card_for_a_third_ranked_candidate_says_why_it_was_placed(self):
        assessed = rank.assess(self.candidates, self.rng, self.anchors)
        self.assertEqual([(c["name"], c["rankedBy"]) for c in assessed[:3]],
                         [("Hollowmere Radio", "links"), ("Odile Thackeray", "growth"),
                          ("The Gallowglass Hour", "growth")])

    def test_a_fading_act_further_down_is_set_aside_with_the_rule_as_the_reason(self):
        assessed = rank.assess(self.candidates, self.rng, self.anchors)
        alderfield = next(c for c in assessed if c["name"] == "Alderfield Union")
        deepened = check_trend(alderfield)
        again = rank.assess([deepened if c is alderfield else c for c in assessed], self.rng, self.anchors)
        fading = next(c for c in again if c["name"] == "Alderfield Union")
        self.assertEqual(fading["status"], "fading")
        self.assertIn("(this app's rule)", rank.label(fading, self.rng, len(self.anchors)))

    def test_every_candidate_is_synthetic_and_in_the_reserved_range(self):
        for candidate in self.candidates:
            self.assertTrue(candidate["entityId"].startswith("00000000-0000-4000-8000-"))
        self.assertFalse({a["entityId"] for a in self.anchors} & {c["entityId"] for c in self.candidates})

    def test_with_trends_dropped_nothing_is_ready(self):
        assessed = rank.assess(self.candidates, self.rng, self.anchors)
        self.assertEqual(sum(c["status"] == "ready" for c in assessed), 0)
        self.assertEqual(sum(c["status"] == "watch" for c in assessed), 12)


class ScenarioB(unittest.TestCase):
    def test_twenty_candidates_six_qualify_and_three_are_ready_after_three_trend_calls(self):
        anchors = confirm(scenario("campus-board")["anchors"])
        rng = rank.room_range([a["popularity"] for a in anchors])
        self.assertEqual((rng["min"], rng["max"]), (0.35, 0.65))
        candidates = wide(anchors, "comedy", tag=TAG)
        assessed = rank.assess(candidates, rng, anchors)
        self.assertEqual(len(candidates), 20)
        counts = rank.tally(assessed)
        self.assertEqual((counts["outside_band"], counts["weak_link"], counts["watch"]), (5, 9, 6))
        deepened, spent = readied(assessed, rng, anchors, open_nights=2)
        self.assertEqual((spent, sum(c["status"] == "ready" for c in deepened)), (3, 3))
        self.assertEqual(sum(rank.needs_trend(c) for c in deepened), 3)


class ScenarioC(unittest.TestCase):
    def setUp(self):
        self.anchors = confirm(["Fallowmere Strings", "Greywether Bell"])
        self.rng = rank.room_range([a["popularity"] for a in self.anchors])

    def test_the_wide_call_spends_its_fifty_above_the_room(self):
        candidates = wide(self.anchors, "music")
        self.assertEqual(len(candidates), 50)
        counts = rank.tally(rank.assess(candidates, self.rng, self.anchors))
        self.assertEqual(counts["watch"], 1)
        self.assertLess(counts["watch"], 2 + 1)

    def test_only_the_range_filtered_call_finds_the_acts_in_reach(self):
        candidates = wide(self.anchors, "music", popularity=(self.rng["min"], self.rng["max"]))
        self.assertEqual(len(candidates), 14)
        assessed = rank.assess(candidates, self.rng, self.anchors)
        self.assertEqual(rank.tally(assessed)["watch"], 6)
        deepened, spent = readied(assessed, self.rng, self.anchors, open_nights=2)
        self.assertEqual((spent, sum(c["status"] == "ready" for c in deepened)), (3, 3))


if __name__ == "__main__":
    unittest.main()
