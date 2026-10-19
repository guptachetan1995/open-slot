import copy
import json
import unittest

from open_slot import config, invoke, tools, verbs
from open_slot.providers import Fetched
from tests.rig import GONE, Rig

HUMAN = ("confirm_anchor", "assign_slot", "approve_inquiry", "drop_act", "raise_call_cap")
CODES = (
    "not_a_tool wrong_actor unknown_actor stale_revision smuggled_field needs_anchors anchor_unconfirmed "
    "unknown_entity unknown_tag unknown_programme slot_taken sender_missing over_cap agent_allowance_spent "
    "daily_allowance_spent rate_limited no_key qloo_auth qloo_unsupported_type qloo_rate_limited "
    "qloo_bad_request qloo_unavailable qloo_no_results"
).split()


class TheVerbsAreNotTools(unittest.TestCase):
    def test_exactly_the_five_verbs_exist_and_none_is_in_the_registry(self):
        self.assertEqual(tuple(verbs.VERBS), HUMAN)
        self.assertFalse(set(verbs.VERBS) & set(tools.REGISTRY))

    def test_nothing_that_lists_the_tools_says_a_verb_exists(self):
        listed = json.dumps(tools.listing())
        for verb in HUMAN:
            self.assertNotIn(verb, listed)
        self.assertEqual([entry["name"] for entry in tools.listing()], list(tools.REGISTRY))

    def test_the_registry_is_the_ten_agent_tools_of_the_plan(self):
        self.assertEqual(list(tools.REGISTRY), [
            "open_programme", "find_anchor", "find_tag", "plan_acts", "check_trend", "explain_act",
            "suggest_slots", "draft_inquiry", "get_programme", "qloo_budget"])

    def test_the_agent_tools_never_write_a_human_verbs_field(self):
        for tool in tools.REGISTRY.values():
            for forbidden in ("sender_name", "status", "revision", "reason", "confirmed", "assigned"):
                self.assertNotIn(forbidden, tool.args, tool.name)


class AHumanVerbAskedForByTheAgentIsRefusedAndNothingRuns(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.programme_id = self.rig.planned()
        found = self.rig.stored(self.programme_id)
        self.entity = found["candidates"][0]["entityId"]
        self.other = found["candidates"][1]["entityId"]
        self.draft = self.rig.ok("draft_inquiry", {"programme_id": self.programme_id,
                                                   "entity_id": self.entity})["draft_id"]
        self.revision = self.rig.stored(self.programme_id)["revision"]

    def calls(self):
        pid, revision = self.programme_id, self.rig.stored(self.programme_id)["revision"]
        anchor = self.rig.stored(pid)["anchors"][0]
        return {
            "confirm_anchor": {"programme_id": pid, "name": anchor["name"],
                               "entity_id": anchor["candidates"][0]["entityId"]},
            "assign_slot": {"programme_id": pid, "night_id": "n1", "entity_id": self.entity,
                            "revision": revision},
            "approve_inquiry": {"programme_id": pid, "draft_id": self.draft, "sender_name": "Maya",
                                "revision": revision},
            "drop_act": {"programme_id": pid, "entity_id": self.other, "reason": "not this year"},
            "raise_call_cap": {"programme_id": pid, "cap": 30},
        }

    def test_on_the_pages_surface_the_agent_gets_wrong_actor(self):
        before = copy.deepcopy(self.rig.stored(self.programme_id))
        for verb, args in self.calls().items():
            self.assertEqual(self.rig.refused(verb, args, "agent"), "wrong_actor", verb)
        self.assertEqual(self.rig.stored(self.programme_id), before)

    def test_on_the_agent_surface_the_verbs_are_not_tools(self):
        before = copy.deepcopy(self.rig.stored(self.programme_id))
        for verb, args in self.calls().items():
            self.assertEqual(self.rig.refused(verb, args, "agent", "agent"), "not_a_tool", verb)
        self.assertEqual(self.rig.stored(self.programme_id), before)

    def test_even_an_actor_that_is_not_the_person_is_refused(self):
        for verb, args in self.calls().items():
            for actor in ("Person", "root", "", None, 7):
                self.assertEqual(self.rig.refused(verb, args, actor), "unknown_actor", (verb, actor))

    def test_every_refusal_is_logged_with_the_actor_and_the_code(self):
        for verb, args in self.calls().items():
            self.rig.refused(verb, args, "agent")
        tail = self.rig.activity()[-5:]
        self.assertEqual([(e["actor"], e["tool"], e["outcome"]) for e in tail],
                         [("agent", verb, "wrong_actor") for verb in HUMAN])

    def test_the_person_runs_every_one_of_them(self):
        for verb in ("confirm_anchor", "drop_act", "raise_call_cap"):
            self.assertTrue(self.rig.call(verb, self.calls()[verb], "person")["ok"], verb)
        self.assertTrue(self.rig.call("assign_slot", self.calls()["assign_slot"], "person")["ok"])
        self.assertTrue(self.rig.call("approve_inquiry", self.calls()["approve_inquiry"], "person")["ok"])


class TheActorAndTheNameAreChecked(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def test_an_unknown_actor_runs_nothing_and_is_logged_as_unknown(self):
        self.assertEqual(self.rig.refused("qloo_budget", {}, "admin"), "unknown_actor")
        self.assertEqual(self.rig.activity()[-1]["actor"], "unknown")

    def test_an_unknown_or_malformed_tool_name_is_not_a_tool(self):
        for name in ("frobnicate", "", "PLAN_ACTS", None, ["plan_acts"], {"a": 1}, 3):
            self.assertEqual(self.rig.refused(name, {}, "agent"), "not_a_tool", name)
            self.assertEqual(self.rig.refused(name, {}, "person"), "not_a_tool", name)

    def test_a_tool_name_that_is_not_a_plain_identifier_is_logged_without_its_text(self):
        self.rig.refused("x" * 90, {}, "agent")
        self.assertEqual(self.rig.activity()[-1]["tool"], "<name>")

    def test_an_unknown_workspace_is_refused_and_nothing_is_logged(self):
        before = copy.deepcopy(self.rig.runtime.doc)
        envelope = invoke.invoke(self.rig.runtime, "ws_nobody", "qloo_budget", {}, "agent")
        self.assertEqual(envelope["outcome"], "no_workspace")
        self.assertFalse(envelope["ok"])
        self.assertEqual(self.rig.runtime.doc, before)

    def test_the_person_may_run_the_agents_tools_too(self):
        self.assertTrue(self.rig.call("qloo_budget", {}, "person")["ok"])
        programme_id = self.rig.open({"label": "A room", "nights": ["Tue"], "anchors": ["One", "Two"]})
        self.assertTrue(programme_id)


class ArgumentsAreDeclaredOrRefused(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.programme_id = self.rig.planned()
        self.entity = self.rig.stored(self.programme_id)["candidates"][0]["entityId"]

    def test_a_draft_tool_sent_a_status_or_a_sender_is_refused_and_logged_not_dropped(self):
        for field in ("status", "sender_name", "approved", "state"):
            args = {"programme_id": self.programme_id, "entity_id": self.entity, field: "approved"}
            self.assertEqual(self.rig.refused("draft_inquiry", args), "smuggled_field", field)
        self.assertEqual(self.rig.stored(self.programme_id)["inquiries"], [])
        last = self.rig.activity()[-1]
        self.assertEqual((last["tool"], last["outcome"]), ("draft_inquiry", "smuggled_field"))

    def test_every_tool_and_every_verb_refuses_a_field_it_does_not_declare(self):
        for name, entry in {**tools.REGISTRY, **verbs.VERBS}.items():
            args = {"programme_id": self.programme_id, "surprise": 1}
            self.assertEqual(self.rig.refused(name, args, "person"), "smuggled_field", name)

    def test_the_message_names_the_field_but_a_strange_field_name_is_not_echoed(self):
        error = self.rig.call("qloo_budget", {"status": 1, "<script>alert(1)</script>": 2})["error"]
        self.assertIn("status", error["message"])
        self.assertNotIn("script", error["message"])

    def test_missing_required_and_wrongly_typed_arguments_are_bad_args(self):
        self.assertEqual(self.rig.refused("explain_act", {"programme_id": self.programme_id}), "bad_args")
        self.assertEqual(self.rig.refused("explain_act", {"programme_id": 7, "entity_id": "x"}), "bad_args")
        self.assertEqual(self.rig.refused("plan_acts", {"programme_id": self.programme_id,
                                                        "max_calls": True}), "bad_args")
        self.assertEqual(self.rig.refused("plan_acts", {"programme_id": self.programme_id,
                                                        "max_calls": 2.5}), "bad_args")
        self.assertEqual(self.rig.refused("raise_call_cap", {"programme_id": self.programme_id, "cap": "30"},
                                          "person"), "bad_args")

    def test_args_that_are_not_an_object_are_bad_args(self):
        for args in ([], "x", 5):
            self.assertEqual(invoke.invoke(self.rig.runtime, self.rig.workspace, "qloo_budget", args,
                                           "agent")["outcome"], "bad_args")

    def test_a_request_the_builders_refuse_is_bad_args_and_never_sent(self):
        self.rig.stored(self.programme_id)["candidates"][0]["type"] = "urn:entity:place"
        spent = self.rig.stored(self.programme_id)["budget"]["spent"]
        self.assertEqual(self.rig.refused("check_trend", {"programme_id": self.programme_id,
                                                          "entity_id": self.entity}), "bad_args")
        self.assertEqual(self.rig.stored(self.programme_id)["budget"]["spent"], spent)


class TheEnvelopeAndTheNamedRefusals(unittest.TestCase):
    def test_every_named_refusal_has_a_message(self):
        for code in CODES:
            self.assertTrue(invoke.MESSAGES.get(code), code)

    def test_a_refusal_is_an_envelope_with_a_code_and_a_message_and_a_success_is_not(self):
        rig = Rig()
        failure = rig.call("frobnicate")
        self.assertEqual(set(failure), {"ok", "outcome", "error"})
        self.assertEqual(set(failure["error"]), {"code", "message"})
        success = rig.call("qloo_budget")
        self.assertEqual(set(success), {"ok", "outcome", "result"})
        self.assertEqual(success["outcome"], "ok")

    def test_an_unknown_programme_is_named(self):
        self.assertEqual(Rig().refused("get_programme", {"programme_id": "prog_999"}), "unknown_programme")

    def test_a_response_shape_the_reader_does_not_know_is_a_loud_named_refusal_with_its_path(self):
        class Odd:
            name = "fake"

            def fetch(self, request, origin=None):
                return Fetched(200, {"unexpected": []}, 0, frozenset(), "fake", 1)

        rig = Rig(engine=Odd())
        programme_id = rig.open({"label": "A room", "nights": ["Tue"], "anchors": ["One", "Two"]})
        envelope = rig.call("find_anchor", {"programme_id": programme_id, "name": "One"})
        self.assertEqual(envelope["outcome"], "qloo_unknown_shape")
        self.assertIn("results", envelope["error"]["message"])
        self.assertEqual(rig.stored(programme_id)["budget"]["spent"], 1)
        self.assertEqual(rig.stored(programme_id)["trace"][-1]["outcome"], "qloo_unknown_shape")


class ConfirmAnchor(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.programme_id = self.rig.open({"label": "A room", "nights": ["Tue"],
                                           "anchors": ["The Milk Carton Kids", "Alvvays"]})
        self.found = self.rig.search(self.programme_id)

    def confirm(self, name, entity_id, actor="person"):
        return self.rig.call("confirm_anchor", {"programme_id": self.programme_id, "name": name,
                                                "entity_id": entity_id}, actor)

    def test_an_id_that_was_not_found_for_that_name_is_unknown_entity(self):
        other = self.found["Alvvays"][0]["entityId"]
        self.assertEqual(self.confirm("The Milk Carton Kids", other)["outcome"], "unknown_entity")
        self.assertEqual(self.confirm("The Milk Carton Kids", GONE)
                         ["outcome"], "unknown_entity")
        self.assertTrue(all(a["confirmed"] is None for a in self.rig.stored(self.programme_id)["anchors"]))

    def test_the_person_confirms_one_of_the_alternatives(self):
        second = self.found["The Milk Carton Kids"][1]["entityId"]
        self.assertTrue(self.confirm("the milk  carton kids", second)["ok"])
        self.assertEqual(self.rig.stored(self.programme_id)["anchors"][0]["confirmed"], second)

    def test_a_name_that_is_not_one_of_the_programmes_acts_is_refused(self):
        self.assertEqual(self.confirm("Somebody Else", "x")["outcome"], "bad_args")

    def test_searching_again_keeps_a_confirmation_that_still_stands_and_drops_one_that_does_not(self):
        first = self.found["The Milk Carton Kids"][0]["entityId"]
        self.confirm("The Milk Carton Kids", first)
        self.rig.search(self.programme_id)
        self.assertEqual(self.rig.stored(self.programme_id)["anchors"][0]["confirmed"], first)
        self.rig.stored(self.programme_id)["anchors"][0]["confirmed"] = GONE
        self.rig.search(self.programme_id)
        self.assertIsNone(self.rig.stored(self.programme_id)["anchors"][0]["confirmed"])


class AssignSlot(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.planned()
        self.ready = self.rig.ready_ids(self.pid)

    def assign(self, night, entity, revision=None, actor="person"):
        revision = self.rig.stored(self.pid)["revision"] if revision is None else revision
        return self.rig.call("assign_slot", {"programme_id": self.pid, "night_id": night, "entity_id": entity,
                                             "revision": revision}, actor)

    def test_assigning_makes_the_night_assigned_and_never_booked(self):
        result = self.assign("n1", self.ready[0])["result"]
        self.assertEqual((result["state"], result["assigned"]), ("assigned", self.ready[0]))
        states = {n["state"] for n in self.rig.stored(self.pid)["nights"]}
        self.assertLessEqual(states, {"open", "proposed", "assigned"})

    def test_a_stale_revision_is_refused_and_nothing_changes(self):
        stale = self.rig.stored(self.pid)["revision"] - 1
        self.assertEqual(self.assign("n1", self.ready[0], stale)["outcome"], "stale_revision")
        self.assertIsNone(self.rig.stored(self.pid)["nights"][0]["assigned"])

    def test_a_night_holding_another_act_is_slot_taken_and_the_same_act_may_hold_several_nights(self):
        self.assign("n1", self.ready[0])
        self.assertEqual(self.assign("n1", self.ready[1])["outcome"], "slot_taken")
        self.assertTrue(self.assign("n1", self.ready[0])["ok"])
        self.assertTrue(self.assign("n2", self.ready[0])["ok"])
        self.assertEqual(self.rig.stored(self.pid)["nights"][1]["assigned"], self.ready[0])

    def test_null_clears_a_night_so_another_act_may_take_it(self):
        self.assign("n1", self.ready[0])
        self.assertTrue(self.assign("n1", None)["ok"])
        self.assertEqual(self.rig.stored(self.pid)["nights"][0]["state"], "open")
        self.assertTrue(self.assign("n1", self.ready[1])["ok"])

    def test_an_unknown_entity_or_night_and_a_dropped_act_are_refused(self):
        self.assertEqual(self.assign("n1", GONE)["outcome"], "unknown_entity")
        self.assertEqual(self.assign("n9", self.ready[0])["outcome"], "bad_args")
        self.rig.ok("drop_act", {"programme_id": self.pid, "entity_id": self.ready[0], "reason": "no"},
                    "person")
        self.assertEqual(self.assign("n1", self.ready[0])["outcome"], "bad_args")

    def test_a_plan_after_an_assignment_does_not_move_the_assigned_night(self):
        self.assign("n1", self.ready[0])
        self.rig.ok("suggest_slots", {"programme_id": self.pid})
        self.rig.ok("plan_acts", {"programme_id": self.pid})
        nights = self.rig.stored(self.pid)["nights"]
        self.assertEqual((nights[0]["state"], nights[0]["assigned"]), ("assigned", self.ready[0]))


class ApproveInquiry(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.planned()
        self.entity = self.rig.stored(self.pid)["candidates"][0]["entityId"]
        self.draft = self.rig.ok("draft_inquiry", {"programme_id": self.pid, "entity_id": self.entity})

    def approve(self, sender="Maya Okafor", revision=None, draft="d1", actor="person"):
        revision = self.rig.stored(self.pid)["revision"] if revision is None else revision
        args = {"programme_id": self.pid, "draft_id": draft, "revision": revision}
        if sender != "ABSENT":
            args["sender_name"] = sender
        return self.rig.call("approve_inquiry", args, actor)

    def test_a_draft_reads_draft_and_asks_for_a_sender(self):
        self.assertTrue(self.draft["text"].startswith("DRAFT. Not approved. Do not send."))
        self.assertIn("[sender name needed]", self.draft["text"])
        self.assertEqual(self.draft["state"], "draft")

    def test_approval_names_the_sender_and_drops_the_draft_line(self):
        result = self.approve()["result"]
        self.assertEqual(result["state"], "approved")
        self.assertNotIn("DRAFT", result["text"])
        self.assertTrue(result["text"].endswith("Maya Okafor"))

    def test_no_sender_a_blank_one_or_a_long_one_is_sender_missing(self):
        for sender in ("ABSENT", None, "", "   ", "x" * 61):
            self.assertEqual(self.approve(sender)["outcome"], "sender_missing", repr(sender))
        self.assertEqual(self.approve(5)["outcome"], "bad_args")
        self.assertEqual(self.rig.stored(self.pid)["inquiries"][0]["state"], "draft")

    def test_a_stale_revision_is_refused(self):
        self.assertEqual(self.approve(revision=0)["outcome"], "stale_revision")

    def test_an_unknown_draft_is_refused_and_an_approved_one_is_frozen(self):
        self.assertEqual(self.approve(draft="d9")["outcome"], "bad_args")
        self.assertTrue(self.approve()["ok"])
        self.assertEqual(self.approve("Someone Else")["outcome"], "bad_args")
        self.assertTrue(self.rig.stored(self.pid)["inquiries"][0]["text"].endswith("Maya Okafor"))

    def test_a_later_plan_cannot_change_an_approved_inquiry(self):
        text = self.approve()["result"]["text"]
        self.rig.ok("plan_acts", {"programme_id": self.pid})
        self.assertEqual(self.rig.stored(self.pid)["inquiries"][0]["text"], text)

    def test_the_agent_cannot_produce_text_without_the_draft_line(self):
        for _ in range(3):
            again = self.rig.ok("draft_inquiry", {"programme_id": self.pid, "entity_id": self.entity})
            self.assertTrue(again["text"].startswith("DRAFT."))
        self.assertEqual(self.approve(actor="agent")["outcome"], "wrong_actor")
        self.assertTrue(all(q["state"] == "draft" for q in self.rig.stored(self.pid)["inquiries"]))


class DropActAndRaiseCap(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.planned()
        self.ready = self.rig.ready_ids(self.pid)

    def drop(self, entity, **args):
        return self.rig.call("drop_act", {"programme_id": self.pid, "entity_id": entity, **args}, "person")

    def test_a_drop_needs_a_reason_and_moves_the_act_off_the_board_and_undo_brings_it_back(self):
        self.assertEqual(self.drop(self.ready[0])["outcome"], "bad_args")
        self.assertEqual(self.drop(self.ready[0], reason=" ")["outcome"], "bad_args")
        self.assertEqual(self.drop(self.ready[0], reason="r" * 121)["outcome"], "bad_args")
        self.assertTrue(self.drop(self.ready[0], reason="played here last year")["ok"])
        held = self.rig.stored(self.pid)["candidates"]
        self.assertEqual(next(c for c in held if c["entityId"] == self.ready[0])["status"], "dropped")
        self.assertTrue(self.drop(self.ready[0], undo=True)["ok"])
        held = self.rig.stored(self.pid)["candidates"]
        self.assertEqual(next(c for c in held if c["entityId"] == self.ready[0])["status"], "ready")

    def test_an_unknown_entity_is_refused(self):
        self.assertEqual(self.drop(GONE, reason="x")["outcome"],
                         "unknown_entity")

    def test_dropping_a_proposed_act_clears_the_proposal_and_an_assigned_one_is_refused(self):
        self.rig.ok("suggest_slots", {"programme_id": self.pid})
        proposed = self.rig.stored(self.pid)["nights"][0]["proposed"]
        self.assertTrue(self.drop(proposed, reason="no")["ok"])
        night = self.rig.stored(self.pid)["nights"][0]
        self.assertEqual((night["state"], night["proposed"]), ("open", None))
        self.drop(proposed, undo=True)
        revision = self.rig.stored(self.pid)["revision"]
        self.rig.ok("assign_slot", {"programme_id": self.pid, "night_id": "n2", "entity_id": proposed,
                                    "revision": revision}, "person")
        self.assertEqual(self.drop(proposed, reason="no")["outcome"], "bad_args")

    def raise_cap(self, cap):
        return self.rig.call("raise_call_cap", {"programme_id": self.pid, "cap": cap}, "person")

    def test_the_cap_can_only_be_raised_and_never_past_the_most_the_app_allows(self):
        self.assertTrue(self.raise_cap(24)["ok"])
        self.assertEqual(self.rig.stored(self.pid)["budget"]["cap"], 24)
        for cap in (24, 16, 41, 1000, 0, -3):
            self.assertEqual(self.raise_cap(cap)["outcome"], "bad_args", cap)
        self.assertTrue(self.raise_cap(config.MAX_CALLS_RAISED)["ok"])

    def test_the_agent_has_no_way_to_spend_past_the_cap(self):
        rig = Rig(max_calls=5)
        pid = rig.ready()
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 4)
        rig.ok("plan_acts", {"programme_id": pid})
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 5)
        self.assertEqual(rig.refused("find_tag", {"programme_id": pid, "query": "folk"}), "over_cap")
        self.assertEqual(rig.refused("raise_call_cap", {"programme_id": pid, "cap": 9}), "wrong_actor")
        self.assertEqual(rig.stored(pid)["budget"]["cap"], 5)


class ActivityLog(unittest.TestCase):
    def test_each_call_is_logged_with_its_actor_and_outcome_and_only_ids_in_its_args(self):
        rig = Rig()
        pid = rig.planned()
        entity = rig.stored(pid)["candidates"][0]["entityId"]
        draft = rig.ok("draft_inquiry", {"programme_id": pid, "entity_id": entity})["draft_id"]
        rig.ok("approve_inquiry", {"programme_id": pid, "draft_id": draft, "sender_name": "Maya Okafor",
                                   "revision": rig.stored(pid)["revision"]}, "person")
        rig.ok("drop_act", {"programme_id": pid, "entity_id": entity, "reason": "a private reason"}, "person")
        log = rig.activity()
        self.assertTrue(all(e["outcome"] and e["actor"] in ("agent", "person") for e in log))
        text = json.dumps(log)
        for private in ("Maya Okafor", "a private reason", "The Lantern Room", "Milk Carton", "Alvvays"):
            self.assertNotIn(private, text)
        approved = [e for e in log if e["tool"] == "approve_inquiry"][0]
        self.assertEqual((approved["actor"], approved["outcome"]), ("person", "ok"))
        self.assertEqual(set(approved["args"]), {"programme_id", "draft_id"})

    def test_a_plan_a_person_started_logs_its_calls_as_the_agent_and_the_start_as_the_person(self):
        rig = Rig()
        pid = rig.ready()
        rig.ok("plan_acts", {"programme_id": pid}, "person")
        mine = [(e["actor"], e["tool"]) for e in rig.activity()
                if e["tool"] in ("plan_acts", "check_trend", "insights")]
        self.assertEqual(mine.count(("person", "plan_acts")), 1)
        self.assertIn(("agent", "check_trend"), mine)
        self.assertIn(("agent", "insights"), mine)
        self.assertEqual({actor for actor, tool in mine if tool != "plan_acts"}, {"agent"})
        steps = [s for s in rig.stored(pid)["trace"] if s["tool"] in ("plan_acts", "check_trend")]
        trace_actors = {s["actor"] for s in steps}
        self.assertEqual(trace_actors, {"agent"})

    def test_the_revision_rises_with_every_call_and_change(self):
        rig = Rig()
        pid = rig.open({"label": "A room", "nights": ["Tue"], "anchors": ["The Milk Carton Kids", "Alvvays"]})
        seen = [rig.stored(pid)["revision"]]
        rig.search(pid)
        seen.append(rig.stored(pid)["revision"])
        self.assertTrue(all(b > a for a, b in zip(seen, seen[1:])), seen)


if __name__ == "__main__":
    unittest.main()
