import copy
import re
import unittest

from open_slot import inquiry, store
from tests.support import ANCHOR_IDS, AT, lantern_programme

ACT = "00000000-0000-4000-8000-000000000601"


def with_candidate(found, links=(0, 2, 3), name="Marlow Hour"):
    found = copy.deepcopy(found)
    found["candidates"] = [{
        "entityId": ACT, "name": name, "type": "urn:entity:podcast", "lens": "podcast",
        "popularity": 0.6, "tags": [], "growth": None, "trend": None, "status": "ready",
        "links": [{"anchor": found["anchors"][i]["name"], "score": 0.3} for i in links],
    }]
    return found


class Drafts(unittest.TestCase):
    def setUp(self):
        _, found = lantern_programme()
        self.found = with_candidate(found)
        self.draft = inquiry.draft(self.found, ACT, "d1")

    def test_a_draft_opens_with_the_draft_line_and_ends_asking_for_a_sender(self):
        lines = self.draft["text"].split("\n")
        self.assertEqual(lines[0], "DRAFT. Not approved. Do not send.")
        self.assertEqual(lines[-1], "[sender name needed]")
        self.assertEqual(self.draft["state"], "draft")
        self.assertEqual((self.draft["approvedBy"], self.draft["approvedAt"]), (None, None))

    def test_the_template_is_filled_from_the_programme_and_the_card(self):
        self.assertEqual(self.draft["text"], (
            "DRAFT. Not approved. Do not send.\n"
            "Hello. I program The Lantern Room (fictional), 300 seats. Some of our audience's favourites are "
            "Resolved Folk Duo, Resolved Indie Band, Resolved Stand-Up and Resolved Live Podcast. "
            "Qloo's taste data, which we use to find acts, connects Marlow Hour with "
            "Resolved Folk Duo, Resolved Stand-Up and Resolved Live Podcast. "
            "We have Tue 3 Nov, Tue 10 Nov and Tue 17 Nov open and would like to ask whether Marlow Hour "
            "might be available for one of them, and what your terms would be. "
            "No offer is made by this message.\n"
            "[sender name needed]"
        ))

    def test_it_stays_within_the_word_limit_for_the_sample_programme(self):
        self.assertLessEqual(inquiry.words(self.draft["text"]), inquiry.MAX_WORDS)

    def test_the_capacity_phrase_appears_only_when_a_note_was_typed(self):
        self.found["capacityNote"] = None
        text = inquiry.draft(self.found, ACT, "d1")["text"]
        self.assertIn("I program The Lantern Room (fictional). Some of", text)
        self.assertNotIn("300", text)

    def test_the_text_names_no_fee_no_availability_and_no_claim_beyond_qloos(self):
        text = self.draft["text"].lower()
        for word in ("$", "fee", "price", "paid", "per ", "guarantee", "interested", "booked",
                     "will be available"):
            self.assertNotIn(word, text)
        self.assertFalse(re.search(r"\d+\s?(usd|eur|inr|dollars|pounds)", text))
        self.assertIn("might be available", text)
        self.assertIn("no offer is made by this message.", text)

    def test_the_evidence_sentence_is_dropped_when_asked_or_when_there_are_no_links(self):
        off = inquiry.draft(self.found, ACT, "d1", evidence=False)
        self.assertNotIn("Qloo's taste data", off["text"])
        self.assertFalse(off["snapshot"]["evidence"])
        found = with_candidate(self.found, links=())
        self.assertNotIn("Qloo's taste data", inquiry.draft(found, ACT, "d1")["text"])

    def test_the_evidence_names_only_the_anchors_the_candidate_links_to_by_their_resolved_names(self):
        self.assertEqual(self.draft["snapshot"]["links"],
                         ["Resolved Folk Duo", "Resolved Stand-Up", "Resolved Live Podcast"])

    def test_the_nights_default_to_the_ones_not_yet_assigned_and_can_be_chosen(self):
        self.found["nights"][0]["assigned"] = ANCHOR_IDS[0]
        default = inquiry.draft(self.found, ACT, "d1")
        self.assertEqual(default["nightIds"], ["n2", "n3"])
        self.assertIn("We have Tue 10 Nov and Tue 17 Nov open", default["text"])
        chosen = inquiry.draft(self.found, ACT, "d1", night_ids=["n3"])
        self.assertIn("We have Tue 17 Nov open", chosen["text"])

    def test_unknown_candidates_and_nights_are_refused(self):
        with self.assertRaises(store.Refusal) as caught:
            inquiry.draft(self.found, ANCHOR_IDS[0], "d1")
        self.assertEqual(caught.exception.code, "unknown_entity")
        for bad in (["n9"], [], ["n1", "n9"]):
            with self.assertRaises(store.Refusal) as caught:
                inquiry.draft(self.found, ACT, "d1", night_ids=bad)
            self.assertEqual(caught.exception.code, "bad_args")

    def test_a_longer_inquiry_loses_its_evidence_sentence_before_it_loses_the_limit(self):
        found = copy.deepcopy(self.found)
        found["label"] = "The Lantern Room and Cellar Bar of the Old Brewery Quarter (fictional)"
        found["nights"] = [{**found["nights"][0], "id": f"n{i}", "label": f"Tuesday {i} November"}
                           for i in range(1, 7)]
        for anchor in found["anchors"]:
            anchor["candidates"][0]["name"] += " and her Wandering Orchestra"
        found["candidates"][0]["links"] = [{"anchor": a["name"], "score": 0.3} for a in found["anchors"]]
        made = inquiry.draft(found, ACT, "d1")
        self.assertLessEqual(inquiry.words(made["text"]), inquiry.MAX_WORDS)
        self.assertFalse(made["snapshot"]["evidence"])

    def test_an_inquiry_that_cannot_fit_is_refused_not_cut(self):
        found = copy.deepcopy(self.found)
        found["label"] = " ".join(["a"] * 25)
        for anchor in found["anchors"]:
            anchor["candidates"][0]["name"] = " ".join(["b"] * 10)
        found["nights"] = [{**found["nights"][0], "id": f"n{i}", "label": "c d e f"} for i in range(1, 7)]
        with self.assertRaises(store.Refusal) as caught:
            inquiry.draft(found, ACT, "d1")
        self.assertEqual(caught.exception.code, "draft_too_long")


class Approval(unittest.TestCase):
    def setUp(self):
        _, found = lantern_programme()
        self.found = with_candidate(found)
        self.draft = inquiry.draft(self.found, ACT, "d1")

    def test_approval_names_the_sender_and_drops_the_draft_line(self):
        approved = inquiry.approve(self.draft, "  Maya   Rao ", AT)
        self.assertEqual(approved["state"], "approved")
        self.assertEqual((approved["approvedBy"], approved["approvedAt"]), ("Maya Rao", AT))
        self.assertFalse(approved["text"].startswith("DRAFT"))
        self.assertNotIn("DRAFT", approved["text"])
        self.assertNotIn("[sender name needed]", approved["text"])
        self.assertTrue(approved["text"].endswith("No offer is made by this message.\nMaya Rao"))

    def test_the_draft_is_unchanged_and_the_only_text_without_the_draft_line_is_an_approval(self):
        before = copy.deepcopy(self.draft)
        inquiry.approve(self.draft, "Maya Rao", AT)
        self.assertEqual(self.draft, before)
        self.assertTrue(self.draft["text"].startswith("DRAFT."))

    def test_a_sender_is_required_and_bounded(self):
        for bad in (None, "", "   ", "x" * 61, 5, "bell\x07"):
            with self.assertRaises(store.Refusal, msg=repr(bad)) as caught:
                inquiry.approve(self.draft, bad, AT)
            self.assertEqual(caught.exception.code, "sender_missing")
        inquiry.approve(self.draft, "x" * 60, AT)

    def test_the_approved_text_is_rendered_from_its_snapshot_alone(self):
        approved = inquiry.approve(self.draft, "Maya Rao", AT)
        self.assertEqual(approved["text"], inquiry.render(approved["snapshot"], "Maya Rao"))
        replanned = copy.deepcopy(self.found)
        replanned["label"] = "Somewhere Else"
        replanned["candidates"][0]["name"] = "Another Act"
        replanned["candidates"][0]["links"] = []
        self.assertEqual(inquiry.render(approved["snapshot"], "Maya Rao"), approved["text"])
        self.assertIn("The Lantern Room (fictional)", approved["text"])
        self.assertIn("Marlow Hour", approved["text"])


if __name__ == "__main__":
    unittest.main()
