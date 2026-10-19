import unittest

from open_slot import rank

IDS = [f"00000000-0000-4000-8000-0000000003{n:02d}" for n in range(1, 60)]
ANCHORS = [
    {"name": "Folk duo", "entityId": IDS[0], "popularity": 0.62},
    {"name": "Indie band", "entityId": IDS[1], "popularity": 0.71},
    {"name": "Stand-up", "entityId": IDS[2], "popularity": 0.55},
    {"name": "Podcast", "entityId": IDS[3], "popularity": 0.68},
]
RANGE = rank.room_range([a["popularity"] for a in ANCHORS])


def entity(n, popularity=0.6, scores=None, growth=None, kind="urn:entity:artist"):
    return {
        "entityId": IDS[10 + n], "name": f"Candidate {n}", "type": kind, "popularity": popularity,
        "tags": [], "growth": growth,
        "explain": [{"entityId": ANCHORS[i]["entityId"], "score": s} for i, s in (scores or {}).items()],
    }


def candidate(n, popularity=0.6, linked=2, trend=None, dropped=None, growth=None, lens="music", order=None):
    found = rank.build_candidates(
        [entity(n, popularity, {i: 0.3 for i in range(linked)}, growth)], lens, ANCHORS)[0]
    found["trend"] = None if trend is None else {"state": trend}
    found["dropped"] = dropped
    found["qlooOrder"] = n if order is None else order
    return found


class Range(unittest.TestCase):
    def test_from_the_anchors_popularity(self):
        self.assertEqual(RANGE, {"min": 0.4, "max": 0.76, "source": "anchors", "movedBy": None})

    def test_it_stays_inside_zero_and_one(self):
        wide = rank.room_range([0.05, 0.99])
        self.assertEqual((wide["min"], wide["max"]), (0.0, 1.0))

    def test_an_anchor_without_popularity_is_ignored_and_none_at_all_means_no_range(self):
        self.assertEqual(rank.room_range([0.5, None])["min"], 0.35)
        self.assertIsNone(rank.room_range([None, None]))
        self.assertIsNone(rank.room_range([]))

    def test_a_moved_range_says_who_moved_it(self):
        moved = rank.room_range([0.5], moved={"min": 0.2, "max": 0.9}, moved_by="person")
        self.assertEqual(moved, {"min": 0.2, "max": 0.9, "source": "moved", "movedBy": "person"})
        for bad in ({"min": 0.9, "max": 0.2}, {"min": -0.1, "max": 0.5}, {"min": 0.1, "max": 1.2}):
            with self.assertRaises(ValueError):
                rank.room_range([0.5], moved=bad)

    def test_the_constants_are_the_documented_ones(self):
        self.assertEqual((rank.EXPLAIN_MIN, rank.BAND_DOWN, rank.BAND_UP, rank.SHORTLIST_SIZE),
                         (0.1, 0.15, 0.05, 8))
        self.assertEqual([rank.min_links(n) for n in (1, 2, 3, 6)], [1, 2, 2, 2])


class Links(unittest.TestCase):
    def test_a_score_exactly_at_the_minimum_is_a_link_and_just_below_is_not(self):
        explain = [{"entityId": IDS[0], "score": 0.1}, {"entityId": IDS[1], "score": 0.0999},
                   {"entityId": IDS[2], "score": 0.1001}]
        self.assertEqual(rank.links(explain, ANCHORS), [
            {"anchor": "Stand-up", "score": 0.1001}, {"anchor": "Folk duo", "score": 0.1},
        ])

    def test_strongest_first_named_by_the_typed_name(self):
        explain = [{"entityId": IDS[3], "score": 0.2}, {"entityId": IDS[0], "score": 0.9}]
        self.assertEqual([link["anchor"] for link in rank.links(explain, ANCHORS)], ["Folk duo", "Podcast"])

    def test_scores_for_entities_that_are_not_confirmed_anchors_are_ignored(self):
        self.assertEqual(rank.links([{"entityId": IDS[20], "score": 0.9}], ANCHORS), [])

    def test_only_confirmed_anchors_can_be_linked(self):
        self.assertEqual(rank.links([{"entityId": IDS[0], "score": 0.9}], ANCHORS[1:]), [])


class Candidates(unittest.TestCase):
    def test_an_anchor_never_appears_as_a_candidate(self):
        anchor_entity = {**entity(0), "entityId": ANCHORS[0]["entityId"]}
        found = rank.build_candidates([anchor_entity, entity(1), entity(2)], "music", ANCHORS)
        self.assertEqual([c["name"] for c in found], ["Candidate 1", "Candidate 2"])

    def test_keeps_qloos_order_and_the_lens(self):
        found = rank.build_candidates([entity(1), entity(2)], "podcast", ANCHORS)
        self.assertEqual([(c["qlooOrder"], c["lens"]) for c in found], [(0, "podcast"), (1, "podcast")])

    def test_weak_links_below_the_minimum_score_are_not_stored(self):
        found = rank.build_candidates([entity(1, scores={0: 0.05, 1: 0.5})], "music", ANCHORS)[0]
        self.assertEqual(found["links"], [{"anchor": "Indie band", "score": 0.5}])


class Statuses(unittest.TestCase):
    def status(self, **kwargs):
        return rank.assess([candidate(1, **kwargs)], RANGE, ANCHORS)[0]["status"]

    def test_every_row_of_the_table_in_order(self):
        self.assertEqual(self.status(dropped="not our crowd", popularity=0.99, linked=0), "dropped")
        self.assertEqual(self.status(popularity=0.97, linked=0), "outside_band")
        self.assertEqual(self.status(popularity=0.2, linked=0, trend="fading"), "outside_band")
        self.assertEqual(self.status(linked=1, trend="fading"), "weak_link")
        self.assertEqual(self.status(trend="fading"), "fading")
        self.assertEqual(self.status(trend="rising"), "ready")
        self.assertEqual(self.status(trend="steady"), "ready")
        self.assertEqual(self.status(), "watch")
        self.assertEqual(self.status(trend="unknown"), "watch")

    def test_the_range_edges_are_inside(self):
        self.assertEqual(self.status(popularity=RANGE["max"]), "watch")
        self.assertEqual(self.status(popularity=RANGE["min"]), "watch")
        self.assertEqual(self.status(popularity=RANGE["max"] + 0.01), "outside_band")
        self.assertEqual(self.status(popularity=RANGE["min"] - 0.01), "outside_band")

    def test_without_a_range_or_popularity_nothing_is_outside_it(self):
        self.assertEqual(rank.assess([candidate(1, popularity=0.99)], None, ANCHORS)[0]["status"], "watch")
        self.assertEqual(rank.assess([candidate(1, popularity=None)], RANGE, ANCHORS)[0]["status"], "watch")

    def test_the_minimum_links_is_the_number_of_anchors_when_there_are_fewer_than_two(self):
        one = ANCHORS[:1]
        found = rank.build_candidates([entity(1, scores={0: 0.4})], "music", one)
        self.assertEqual(rank.assess(found, RANGE, one)[0]["status"], "watch")

    def test_assess_does_not_change_its_input(self):
        original = candidate(1)
        rank.assess([original], RANGE, ANCHORS)
        self.assertIsNone(original["status"])

    def test_qualifying_and_needing_a_trend(self):
        graded = {c["name"]: c for c in rank.assess([
            candidate(1, trend="rising"), candidate(2), candidate(3, trend="unknown"),
            candidate(4, trend="fading"), candidate(5, linked=1), candidate(6, popularity=0.9),
            candidate(7, dropped="no"),
        ], RANGE, ANCHORS)}
        self.assertEqual([n for n, c in sorted(graded.items()) if rank.qualifies(c)],
                         ["Candidate 1", "Candidate 2", "Candidate 3", "Candidate 4"])
        self.assertEqual([n for n, c in sorted(graded.items()) if rank.needs_trend(c)], ["Candidate 2"])


class Labels(unittest.TestCase):
    def label(self, **kwargs):
        found = rank.assess([candidate(1, **kwargs)], RANGE, ANCHORS)[0]
        return rank.label(found, RANGE, len(ANCHORS))

    def test_each_status_has_its_card_label(self):
        self.assertEqual(self.label(dropped="not our crowd"), "Dropped by you: not our crowd")
        self.assertEqual(self.label(popularity=0.97),
                         "Above your room's range: Qloo popularity 0.97; your range tops out at 0.76")
        self.assertEqual(self.label(popularity=0.2),
                         "Below your room's range: Qloo popularity 0.20; your range starts at 0.40")
        self.assertEqual(self.label(linked=1), "Connects to 1 of your 4 acts. This app asks for 2")
        self.assertEqual(self.label(trend="fading"),
                         "Qloo's weekly popularity percentile for it fell in at least 2 of the last 3 weeks "
                         "(this app's rule)")
        self.assertEqual(self.label(trend="rising"), "Connected, in range, trend not falling")
        self.assertEqual(self.label(), "Connected and in range; trend not checked")


class Ranking(unittest.TestCase):
    def order(self, candidates):
        return [c["name"].split()[-1] for c in rank.assess(candidates, RANGE, ANCHORS)]

    def test_ready_before_watch_then_the_set_aside_statuses(self):
        found = [
            candidate(1, popularity=0.97), candidate(2), candidate(3, linked=1), candidate(4, trend="fading"),
            candidate(5, trend="rising"), candidate(6, dropped="no"),
        ]
        self.assertEqual(self.order(found), ["5", "2", "4", "3", "1", "6"])

    def test_within_a_status_more_links_first(self):
        found = [candidate(1, linked=2), candidate(2, linked=4), candidate(3, linked=3)]
        graded = rank.assess(found, RANGE, ANCHORS)
        self.assertEqual([c["name"].split()[-1] for c in graded], ["2", "3", "1"])
        self.assertEqual([c["rankedBy"] for c in graded], ["links", "links", "links"])

    def test_then_growth_when_qloo_gave_it_and_a_missing_figure_goes_last(self):
        found = [candidate(1, growth=None), candidate(2, growth=0.01), candidate(3, growth=0.09)]
        graded = rank.assess(found, RANGE, ANCHORS)
        self.assertEqual([c["name"].split()[-1] for c in graded], ["3", "2", "1"])
        self.assertEqual({c["rankedBy"] for c in graded}, {"growth"})

    def test_then_closeness_to_the_anchors_median_popularity(self):
        median = 0.65
        found = [candidate(1, popularity=0.76), candidate(2, popularity=median),
                 candidate(3, popularity=0.55)]
        graded = rank.assess(found, RANGE, ANCHORS)
        self.assertEqual([c["name"].split()[-1] for c in graded], ["2", "3", "1"])
        self.assertEqual({c["rankedBy"] for c in graded}, {"closeness"})

    def test_then_qloos_own_order(self):
        found = [candidate(1, order=3), candidate(2, order=1), candidate(3, order=2)]
        graded = rank.assess(found, RANGE, ANCHORS)
        self.assertEqual([c["name"].split()[-1] for c in graded], ["2", "3", "1"])
        self.assertEqual({c["rankedBy"] for c in graded}, {"order"})

    def test_ranked_by_names_the_first_criterion_that_separated_it_from_its_neighbour(self):
        found = [candidate(1, linked=3, growth=0.1), candidate(2, linked=2, growth=0.1),
                 candidate(3, linked=2, growth=0.05)]
        graded = rank.assess(found, RANGE, ANCHORS)
        self.assertEqual([(c["name"].split()[-1], c["rankedBy"]) for c in graded],
                         [("1", "links"), ("2", "growth"), ("3", "growth")])

    def test_a_lone_candidate_in_its_status_is_ranked_by_status(self):
        graded = rank.assess([candidate(1, trend="rising"), candidate(2)], RANGE, ANCHORS)
        self.assertEqual([c["rankedBy"] for c in graded], ["status", "status"])

    def test_candidates_from_different_lenses_with_the_same_order_are_broken_by_lens(self):
        found = [candidate(1, lens="comedy", order=0), candidate(2, lens="music", order=0)]
        self.assertEqual(self.order(found), ["2", "1"])

    def test_two_candidates_tied_on_every_criterion_still_grade_and_say_order(self):
        twin = candidate(1, order=3)
        found = [twin, {**twin, "entityId": IDS[40], "name": "Twin"}]
        graded = rank.assess(found, RANGE, ANCHORS)
        self.assertEqual([c["rankedBy"] for c in graded], ["order", "order"])

    def test_no_composite_score_is_stored(self):
        graded = rank.assess([candidate(1, trend="rising")], RANGE, ANCHORS)[0]
        self.assertFalse([k for k in graded if "score" in k.lower() or "rating" in k.lower()])


class Sections(unittest.TestCase):
    def test_the_board_holds_eight_and_the_rest_are_also_connected(self):
        found = [candidate(n, trend="rising", order=n) for n in range(1, 12)]
        found += [candidate(20, popularity=0.97), candidate(21, linked=1), candidate(22, trend="fading")]
        graded = rank.assess(found, RANGE, ANCHORS)
        shown = rank.sections(graded)
        self.assertEqual(len(shown["board"]), 8)
        self.assertEqual(len(shown["also_connected"]), 3)
        self.assertEqual(sorted(c["status"] for c in shown["set_aside"]),
                         ["fading", "outside_band", "weak_link"])

    def test_tally_counts_every_status(self):
        graded = rank.assess([candidate(1, trend="rising"), candidate(2), candidate(3, popularity=0.9)],
                             RANGE, ANCHORS)
        self.assertEqual(rank.tally(graded),
                         {"ready": 1, "watch": 1, "fading": 0, "weak_link": 0, "outside_band": 1,
                          "dropped": 0})


if __name__ == "__main__":
    unittest.main()
