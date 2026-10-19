import json
import unittest
from unittest import mock

from open_slot import pins, tools
from tests.rig import GONE, TAG, THIN, Rig, scenario
from tests.support import fake_key

JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


class TheRegistryIsDocumentation(unittest.TestCase):
    def test_every_description_says_what_the_tool_does_not_do(self):
        for tool in tools.REGISTRY.values():
            self.assertIn("NOT", tool.description, tool.name)
            self.assertGreater(len(tool.description), 150, tool.name)

    def test_every_argument_is_described_and_typed_in_json_schema_terms(self):
        for tool in tools.REGISTRY.values():
            for name, arg in tool.args.items():
                self.assertTrue(arg.text.strip(), f"{tool.name}.{name}")
                kinds = {arg.kind} if isinstance(arg.kind, str) else set(arg.kind)
                self.assertLessEqual(kinds, JSON_TYPES, f"{tool.name}.{name}")

    def test_the_listing_is_json_with_a_closed_schema_for_each_tool(self):
        listed = json.loads(json.dumps(tools.listing()))
        for entry in listed:
            schema = entry["inputSchema"]
            self.assertEqual((schema["type"], schema["additionalProperties"]), ("object", False))
            self.assertLessEqual(set(schema["required"]), set(schema["properties"]))
        by_name = {entry["name"]: entry["inputSchema"] for entry in listed}
        self.assertEqual(by_name["open_programme"]["required"], ["label", "nights", "anchors"])
        self.assertEqual(by_name["qloo_budget"]["required"], [])

    def test_an_array_argument_says_what_its_members_are_and_a_wrong_member_is_bad_args(self):
        schema = {entry["name"]: entry["inputSchema"] for entry in tools.listing()}
        self.assertEqual(schema["draft_inquiry"]["properties"]["night_ids"]["items"], {"type": "string"})
        self.assertEqual(schema["open_programme"]["properties"]["anchors"]["items"], {"type": "string"})
        rig = Rig()
        pid = rig.planned()
        entity = rig.stored(pid)["candidates"][0]["entityId"]
        for bad in ([["n1"]], [1], [None], "n1", [{"id": "n1"}]):
            args = {"programme_id": pid, "entity_id": entity, "night_ids": bad}
            self.assertEqual(rig.refused("draft_inquiry", args), "bad_args", bad)
        self.assertEqual(rig.stored(pid)["inquiries"], [])

    def test_the_descriptions_name_no_fee_availability_or_sales_claim(self):
        text = " ".join(tool.description for tool in tools.REGISTRY.values())
        for forbidden in ("will sell", "affordable", "cheaper", "is available", "books", "booked"):
            self.assertNotIn(forbidden, text)

    def test_the_two_search_tools_say_a_person_decides_and_name_no_verb(self):
        for name in ("find_anchor", "draft_inquiry", "suggest_slots"):
            self.assertRegex(tools.REGISTRY[name].description, r"person")


class OpenProgramme(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.args = {"label": "A room", "nights": ["Tue 3 Nov", "Tue 10 Nov"], "anchors": ["One", "Two"]}

    def test_it_returns_the_ids_the_other_tools_use_and_the_cap(self):
        result = self.rig.ok("open_programme", {**self.args, "capacity_note": "300 seats"})
        self.assertEqual(result["programme_id"], "prog_001")
        self.assertEqual([n["id"] for n in result["nights"]], ["n1", "n2"])
        self.assertEqual((result["anchors"], result["call_cap"]), (["One", "Two"], 16))
        self.assertEqual(result["lenses"], list(pins.CONFIRMED_LENSES))

    def test_it_spends_no_call_and_searches_nothing(self):
        pid = self.rig.open(self.args)
        self.assertEqual(self.rig.stored(pid)["budget"]["spent"], 0)
        self.assertEqual(self.rig.stored(pid)["trace"], [])
        self.assertEqual(self.rig.runtime.doc["calls"], [])

    def test_the_documented_limits_are_refused_as_bad_args(self):
        too_many = [f"Night {n}" for n in range(7)]
        refusals = (
            {"label": "x" * 61}, {"nights": []}, {"nights": too_many}, {"anchors": ["Only"]},
            {"anchors": [f"A{n}" for n in range(7)]}, {"nights": ["Tue", "tue"]},
            {"anchors": ["Same", "same"]}, {"lenses": ["movies"]}, {"lenses": []},
            {"capacity_note": "c" * 41}, {"nights": ["x" * 41]}, {"anchors": ["a", "b" * 61]},
        )
        for changes in refusals:
            refused = self.rig.refused("open_programme", {**self.args, **changes})
            self.assertEqual(refused, "bad_args", changes)

    def test_the_lenses_can_be_narrowed(self):
        result = self.rig.ok("open_programme", {**self.args, "lenses": ["comedy"]})
        self.assertEqual(result["lenses"], ["comedy"])

    def test_a_documented_tag_would_be_known_from_the_start(self):
        with mock.patch.dict(pins.LENS_DEFAULTS["comedy"], {"tag": TAG}):
            pid = self.rig.open(self.args)
        self.assertEqual([t["id"] for t in self.rig.stored(pid)["knownTags"]], [TAG])


class FindAnchor(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.open({"label": "A room", "nights": ["Tue"],
                                  "anchors": ["The Milk Carton Kids", "Nobody Known"]})

    def test_it_returns_up_to_five_candidates_and_stores_them_for_the_name(self):
        result = self.rig.ok("find_anchor", {"programme_id": self.pid, "name": "The Milk Carton Kids"})
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(set(result["candidates"][0]),
                         {"entityId", "name", "type", "popularity", "disambiguation"})
        self.assertEqual(self.rig.stored(self.pid)["anchors"][0]["candidates"], result["candidates"])
        self.assertIsNone(self.rig.stored(self.pid)["anchors"][0]["confirmed"])

    def test_it_costs_one_call_each_time_and_writes_a_trace_step(self):
        self.rig.ok("find_anchor", {"programme_id": self.pid, "name": "the milk carton kids"})
        found = self.rig.stored(self.pid)
        self.assertEqual(found["budget"]["spent"], 1)
        step = found["trace"][0]
        self.assertEqual((step["tool"], step["request"]["path"], step["callsSpent"], step["got"]),
                         ("find_anchor", "/search", 1, 2))
        self.assertEqual(step["request"]["params"]["take"], "5")

    def test_a_name_with_no_match_is_a_result_not_a_failure_and_still_counts(self):
        envelope = self.rig.call("find_anchor", {"programme_id": self.pid, "name": "Nobody Known"})
        self.assertEqual((envelope["ok"], envelope["outcome"]), (True, "qloo_no_results"))
        self.assertEqual(envelope["result"]["candidates"], [])
        self.assertEqual(self.rig.stored(self.pid)["budget"]["spent"], 1)

    def test_only_a_name_of_the_programme_can_be_searched(self):
        self.assertEqual(self.rig.refused("find_anchor", {"programme_id": self.pid, "name": "Someone Else"}),
                         "bad_args")
        self.assertEqual(self.rig.stored(self.pid)["budget"]["spent"], 0)


class FindTag(unittest.TestCase):
    def test_it_remembers_the_tags_qloo_returned_and_merges_later_searches(self):
        rig = Rig()
        pid = rig.open({"label": "A room", "nights": ["Tue"], "anchors": ["One", "Two"]})
        first = rig.ok("find_tag", {"programme_id": pid, "query": "stand-up"})
        self.assertEqual([t["id"] for t in first["tags"]], [TAG])
        rig.ok("find_tag", {"programme_id": pid, "query": "comedian"})
        rig.ok("find_tag", {"programme_id": pid, "query": "stand-up"})
        self.assertEqual({t["id"] for t in rig.stored(pid)["knownTags"]},
                         {TAG, "urn:tag:synthetic:comedian"})
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 3)

    def test_a_word_qloo_has_no_tag_for_is_a_result_with_no_tags(self):
        rig = Rig()
        pid = rig.open({"label": "A room", "nights": ["Tue"], "anchors": ["One", "Two"]})
        envelope = rig.call("find_tag", {"programme_id": pid, "query": "polka"})
        self.assertEqual((envelope["ok"], envelope["outcome"], envelope["result"]["tags"]),
                         (True, "qloo_no_results", []))


class CheckTrend(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.planned()
        found = self.rig.stored(self.pid)
        self.watch = next(c for c in found["candidates"] if c["status"] == "watch")

    def test_it_sets_the_trend_with_one_call_and_returns_the_evidence(self):
        before = self.rig.stored(self.pid)["budget"]["spent"]
        card = self.rig.ok("check_trend", {"programme_id": self.pid, "entity_id": self.watch["entityId"]})
        self.assertEqual(self.rig.stored(self.pid)["budget"]["spent"], before + 1)
        self.assertIn(card["trend"]["state"], ("rising", "steady", "fading", "unknown"))
        self.assertEqual(card["trend"]["weeks"], 4)
        self.assertEqual(len(card["trend"]["changes"]), 3)
        self.assertNotEqual(card["status"], "watch")

    def test_it_sends_one_entity_and_the_programmes_window(self):
        self.rig.ok("check_trend", {"programme_id": self.pid, "entity_id": self.watch["entityId"]})
        params = self.rig.stored(self.pid)["trace"][-1]["request"]["params"]
        self.assertEqual(params["signal.interests.entities"], self.watch["entityId"])
        self.assertEqual((params["filter.start_date"], params["filter.end_date"]),
                         ("2026-08-25", "2026-10-20"))
        self.assertEqual(params["filter.type"], self.watch["type"])

    def test_the_agent_asking_is_recorded_as_the_agent_and_a_person_as_the_person(self):
        self.rig.ok("check_trend", {"programme_id": self.pid, "entity_id": self.watch["entityId"]}, "person")
        self.assertEqual(self.rig.stored(self.pid)["trace"][-1]["actor"], "person")

    def test_an_entity_the_programme_does_not_hold_is_refused_with_no_call(self):
        before = self.rig.stored(self.pid)["budget"]["spent"]
        self.assertEqual(self.rig.refused("check_trend", {"programme_id": self.pid, "entity_id": GONE}),
                         "unknown_entity")
        self.assertEqual(self.rig.stored(self.pid)["budget"]["spent"], before)

    def test_a_fading_act_leaves_the_board_for_the_pile_with_the_rule_as_the_reason(self):
        fading = next(c for c in self.rig.stored(self.pid)["candidates"] if c["name"] == "Alderfield Union")
        card = self.rig.ok("check_trend", {"programme_id": self.pid, "entity_id": fading["entityId"]})
        self.assertEqual((card["trend"]["state"], card["status"]), ("fading", "fading"))
        self.assertIn("(this app's rule)", card["status_text"])


class SuggestSlots(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.planned()

    def test_it_proposes_ready_acts_for_open_nights_and_never_assigns(self):
        result = self.rig.ok("suggest_slots", {"programme_id": self.pid})
        self.assertEqual([n["state"] for n in result["nights"]], ["proposed"] * 3)
        self.assertTrue(all(n["assigned"] is None for n in result["nights"]))
        self.assertEqual(len({n["proposed"] for n in result["nights"]}), 3)
        self.assertEqual(len(result["backups"]), 1)
        found = self.rig.stored(self.pid)
        lens_of = {c["entityId"]: c["lens"] for c in found["candidates"]}
        lenses = [lens_of[n["proposed"]] for n in found["nights"]]
        self.assertTrue(all(a != b for a, b in zip(lenses, lenses[1:])))

    def test_with_no_ready_act_every_night_stays_open(self):
        rig = Rig()
        pid = rig.ready()
        result = rig.ok("suggest_slots", {"programme_id": pid})
        self.assertEqual([n["state"] for n in result["nights"]], ["open"] * 3)
        self.assertEqual(result["backups"], [])


class DraftInquiry(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.planned()
        self.entity = self.rig.stored(self.pid)["candidates"][0]["entityId"]

    def draft(self, **args):
        return self.rig.call("draft_inquiry", {"programme_id": self.pid, "entity_id": self.entity, **args})

    def test_the_draft_is_numbered_watermarked_short_and_names_no_fee(self):
        first, second = self.draft()["result"], self.draft()["result"]
        self.assertEqual((first["draft_id"], second["draft_id"]), ("d1", "d2"))
        self.assertTrue(first["text"].startswith("DRAFT. Not approved. Do not send."))
        self.assertLessEqual(first["words"], 120)
        self.assertIn("No offer is made by this message.", first["text"])
        self.assertIn("300 seats", first["text"])
        for forbidden in ("$", "fee", "price", "paid", "contract"):
            self.assertNotIn(forbidden, first["text"].lower())

    def test_it_can_ask_about_chosen_nights_only(self):
        result = self.draft(night_ids=["n2"])["result"]
        self.assertEqual(result["night_ids"], ["n2"])
        self.assertIn("Tue 10 Nov", result["text"])
        self.assertNotIn("Tue 3 Nov", result["text"])

    def test_an_unknown_night_or_candidate_is_refused_and_nothing_is_stored(self):
        self.assertEqual(self.draft(night_ids=["n9"])["outcome"], "bad_args")
        self.assertEqual(self.draft(night_ids=[])["outcome"], "bad_args")
        envelope = self.rig.call("draft_inquiry", {"programme_id": self.pid, "entity_id": GONE})
        self.assertEqual(envelope["outcome"], "unknown_entity")
        self.assertEqual(self.rig.stored(self.pid)["inquiries"], [])

    def test_the_text_quotes_only_the_programme_and_the_stored_evidence(self):
        text = self.draft()["result"]["text"]
        card = self.rig.ok("explain_act", {"programme_id": self.pid, "entity_id": self.entity})
        self.assertIn(card["name"], text)
        self.assertIn("The Lantern Room (fictional)", text)


class GetProgrammeAndBudget(unittest.TestCase):
    def test_the_programme_comes_back_as_a_copy_that_changes_nothing_when_edited(self):
        rig = Rig()
        pid = rig.planned()
        copy = rig.ok("get_programme", {"programme_id": pid})
        copy["candidates"].clear()
        copy["budget"]["spent"] = 0
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 12)
        self.assertTrue(rig.stored(pid)["candidates"])

    def test_the_budget_says_calls_spent_and_left_and_never_the_key(self):
        rig = Rig(key=fake_key())
        pid = rig.planned()
        shown = rig.ok("qloo_budget", {"programme_id": pid})
        self.assertEqual(shown["key"], "set")
        self.assertEqual(shown["provider"], "fake")
        self.assertEqual(shown["programme"], {
            "data": "synthetic", "cap": 16, "spent": 12, "left": 4, "not_spent": 8,
            "stopped_because": "enough_ready", "cache_hits": 0})
        self.assertEqual((shown["agent_allowance"]["limit"], shown["server_ceiling"]["limit"]), (48, 600))
        self.assertNotIn(fake_key(), json.dumps(shown))

    def test_without_a_programme_it_reports_the_workspace_and_the_server_only(self):
        shown = Rig().ok("qloo_budget")
        self.assertIsNone(shown["programme"])
        self.assertEqual((shown["key"], shown["cache"]["entries"]), ("missing", 0))

    def test_an_unknown_programme_is_refused(self):
        self.assertEqual(Rig().refused("qloo_budget", {"programme_id": "prog_404"}), "unknown_programme")


class ThinLensThroughTheTools(unittest.TestCase):
    def test_a_programme_of_one_lens_never_looks_up_a_comedy_tag(self):
        rig = Rig()
        pid = rig.planned(THIN)
        self.assertEqual(rig.stored(pid)["knownTags"], [])
        self.assertNotIn("find_tag", [s["tool"] for s in rig.stored(pid)["trace"]])
        self.assertEqual(scenario("campus-board")["lenses"], ["comedy"])


if __name__ == "__main__":
    unittest.main()
