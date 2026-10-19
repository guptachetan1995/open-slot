import unittest

from open_slot import slots

IDS = [f"00000000-0000-4000-8000-0000000004{n:02d}" for n in range(1, 30)]


def ready(n, lens, status="ready"):
    return {"entityId": IDS[n], "name": f"Act {n}", "lens": lens, "status": status}


def programme(candidates, *nights):
    return {
        "candidates": candidates,
        "nights": [{"id": f"n{i}", "label": f"Night {i}", "state": state, "proposed": None,
                    "assigned": assigned}
                   for i, (state, assigned) in enumerate(nights, 1)],
    }


OPEN = ("open", None)


class Proposals(unittest.TestCase):
    def proposed(self, found):
        nights, backups = slots.propose(found)
        return [night["proposed"] for night in nights], backups, nights

    def test_each_ready_candidate_goes_to_the_next_open_night_in_rank_order(self):
        found = programme([ready(1, "music"), ready(2, "podcast"), ready(3, "comedy")], OPEN, OPEN, OPEN)
        picks, backups, nights = self.proposed(found)
        self.assertEqual(picks, [IDS[1], IDS[2], IDS[3]])
        self.assertEqual(backups, [])
        self.assertEqual({n["state"] for n in nights}, {"proposed"})

    def test_never_two_acts_of_the_same_kind_back_to_back_when_another_fits(self):
        found = programme([ready(1, "music"), ready(2, "music"), ready(3, "podcast"), ready(4, "music")],
                          OPEN, OPEN, OPEN)
        picks, backups, _ = self.proposed(found)
        self.assertEqual(picks, [IDS[1], IDS[3], IDS[2]])
        self.assertEqual(backups, [IDS[4]])

    def test_when_only_one_kind_is_left_the_rule_gives_way(self):
        found = programme([ready(1, "music"), ready(2, "music")], OPEN, OPEN)
        self.assertEqual(self.proposed(found)[0], [IDS[1], IDS[2]])

    def test_one_candidate_is_never_proposed_twice(self):
        found = programme([ready(1, "music"), ready(2, "podcast")], OPEN, OPEN, OPEN)
        picks, backups, nights = self.proposed(found)
        self.assertEqual(picks, [IDS[1], IDS[2], None])
        self.assertEqual(nights[2]["state"], "open")
        self.assertEqual(len([p for p in picks if p]), len({p for p in picks if p}))

    def test_only_ready_candidates_are_proposed(self):
        found = programme([ready(1, "music", "watch"), ready(2, "music", "fading"), ready(3, "music")],
                          OPEN, OPEN)
        self.assertEqual(self.proposed(found)[0], [IDS[3], None])

    def test_a_proposal_is_proposed_and_never_assigned(self):
        _, _, nights = self.proposed(programme([ready(1, "music")], OPEN))
        self.assertEqual((nights[0]["state"], nights[0]["assigned"]), ("proposed", None))

    def test_an_assigned_night_is_left_alone_and_counts_as_the_previous_kind(self):
        found = programme([ready(1, "music"), ready(2, "podcast"), ready(3, "music")],
                          ("assigned", IDS[9]), OPEN, OPEN)
        found["candidates"].append(ready(9, "music", status="watch"))
        picks, backups, nights = self.proposed(found)
        self.assertEqual(nights[0], found["nights"][0])
        self.assertEqual(picks[1:], [IDS[2], IDS[1]])
        self.assertEqual(backups, [IDS[3]])

    def test_an_act_already_assigned_to_a_night_is_not_proposed_for_another(self):
        found = programme([ready(1, "music"), ready(2, "podcast")], ("assigned", IDS[1]), OPEN)
        self.assertEqual(self.proposed(found)[0], [None, IDS[2]])

    def test_running_it_again_replaces_the_earlier_proposal(self):
        found = programme([ready(1, "music"), ready(2, "podcast")], OPEN, OPEN)
        again = {**found, "nights": slots.propose(found)[0]}
        found["candidates"][0]["status"] = "fading"
        self.assertEqual(self.proposed(again)[0], [IDS[2], None])

    def test_it_does_not_change_its_input(self):
        found = programme([ready(1, "music")], OPEN)
        slots.propose(found)
        self.assertEqual(found["nights"][0]["proposed"], None)
        self.assertEqual(found["nights"][0]["state"], "open")


if __name__ == "__main__":
    unittest.main()
