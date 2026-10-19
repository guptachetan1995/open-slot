import json
import threading
import unittest

from open_slot import providers, qloo
from tests.rig import scenario, serve_fake
from tests.support import Stub, fake_key
from tests.webrig import HUMAN, Browser, Site


def ready(person):
    """A workspace whose sample programme is confirmed and planned, the way the page leaves it."""
    pid = person.sample()
    person.confirm_all(pid)
    person.ok("plan_acts", {"programme_id": pid})
    return pid


def board(person):
    return person.programme()["view"]["board"]


class TheBoardOfNights(unittest.TestCase):
    def test_the_agent_proposes_the_person_assigns_and_no_state_is_ever_booked(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            self.assertEqual({n["state"] for n in person.programme()["nights"]}, {"open"})
            proposal = person.ok("suggest_slots", {"programme_id": pid})
            shown = person.programme()["nights"]
            self.assertEqual([n["state"] for n in shown], ["proposed"] * 3)
            self.assertEqual([n["proposed"] for n in shown], [n["proposed"] for n in proposal["nights"]])
            self.assertTrue(all(n["assigned"] is None for n in shown))
            first = shown[0]
            assigned = person.ok("assign_slot", {"programme_id": pid, "night_id": first["id"],
                                                 "entity_id": first["proposed"],
                                                 "revision": person.programme()["revision"]})
            self.assertEqual((assigned["state"], assigned["assigned"]), ("assigned", first["proposed"]))
            states = {n["state"] for n in person.programme()["nights"]}
            self.assertEqual(states, {"assigned", "proposed"})
            self.assertNotIn("booked", json.dumps(person.state()).lower())

    def test_a_night_can_be_given_any_candidate_on_the_board_and_cleared_again(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            second = board(person)[1]["entity_id"]
            night = person.programme()["nights"][0]["id"]
            person.ok("assign_slot", {"programme_id": pid, "night_id": night, "entity_id": second,
                                      "revision": person.programme()["revision"]})
            self.assertEqual(person.programme()["nights"][0]["assigned"], second)
            other = board(person)[2]["entity_id"]
            taken = person.invoke("assign_slot", {"programme_id": pid, "night_id": night, "entity_id": other,
                                                  "revision": person.programme()["revision"]})
            self.assertEqual(taken["outcome"], "slot_taken")
            person.ok("assign_slot", {"programme_id": pid, "night_id": night, "entity_id": None,
                                      "revision": person.programme()["revision"]})
            self.assertEqual(person.programme()["nights"][0]["state"], "open")

    def test_an_assignment_made_on_an_old_reading_is_refused_and_works_after_reading_again(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            night = person.programme()["nights"][0]["id"]
            entity = board(person)[0]["entity_id"]
            seen = person.programme()["revision"]
            person.ok("suggest_slots", {"programme_id": pid})
            stale = person.invoke("assign_slot", {"programme_id": pid, "night_id": night, "entity_id": entity,
                                                  "revision": seen})
            self.assertEqual(stale["outcome"], "stale_revision")
            person.ok("assign_slot", {"programme_id": pid, "night_id": night, "entity_id": entity,
                                      "revision": person.programme()["revision"]})

    def test_a_person_can_drop_an_act_with_a_reason_and_bring_it_back(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            entity = board(person)[3]["entity_id"]
            person.ok("drop_act", {"programme_id": pid, "entity_id": entity, "reason": "Played here in May"})
            view = person.programme()["view"]
            self.assertNotIn(entity, [c["entity_id"] for c in view["board"]])
            dropped = next(item for item in view["set_aside"] if item["entity_id"] == entity)
            self.assertEqual((dropped["status"], dropped["reason"]),
                             ("dropped", "Dropped by you: Played here in May"))
            empty = person.invoke("drop_act", {"programme_id": pid, "entity_id": entity, "reason": ""})
            self.assertEqual(empty["outcome"], "bad_args")
            person.ok("drop_act", {"programme_id": pid, "entity_id": entity, "undo": True})
            self.assertIn(entity, [c["entity_id"] for c in board(person)])


class DraftsAndApproval(unittest.TestCase):
    def test_a_draft_reads_draft_until_the_person_names_the_sender_and_approves_it(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            entity = board(person)[0]["entity_id"]
            draft = person.ok("draft_inquiry", {"programme_id": pid, "entity_id": entity})
            self.assertTrue(draft["text"].startswith("DRAFT. Not approved. Do not send."))
            self.assertIn("[sender name needed]", draft["text"])
            stored = person.programme()["inquiries"][0]
            self.assertEqual((stored["state"], stored["approvedBy"]), ("draft", None))
            self.assertEqual(person.get(f"/api/inquiry/{pid}-{draft['draft_id']}.txt").status, 404)
            revision = person.programme()["revision"]
            for sender in (None, "", "   ", "x" * 61):
                refused = person.invoke("approve_inquiry", {
                    "programme_id": pid, "draft_id": draft["draft_id"], "sender_name": sender,
                    "revision": revision})
                self.assertEqual(refused["outcome"], "sender_missing", repr(sender))
            self.assertEqual(person.programme()["inquiries"][0]["state"], "draft")
            approved = person.ok("approve_inquiry", {"programme_id": pid, "draft_id": draft["draft_id"],
                                                     "sender_name": "Maya", "revision": revision})
            self.assertNotIn("DRAFT", approved["text"])
            self.assertTrue(approved["text"].endswith("\nMaya"))
            saved = person.get(f"/api/inquiry/{pid}-{draft['draft_id']}.txt")
            self.assertEqual((saved.status, saved.text), (200, approved["text"] + "\n"))
            self.assertIn("No offer is made by this message.", approved["text"])

    def test_an_approved_inquiry_is_frozen_and_a_later_plan_cannot_change_it(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            entity = board(person)[0]["entity_id"]
            draft = person.ok("draft_inquiry", {"programme_id": pid, "entity_id": entity})["draft_id"]
            person.ok("approve_inquiry", {"programme_id": pid, "draft_id": draft, "sender_name": "Maya",
                                          "revision": person.programme()["revision"]})
            before = person.programme()["inquiries"][0]
            person.ok("plan_acts", {"programme_id": pid})
            person.ok("drop_act", {"programme_id": pid, "entity_id": board(person)[1]["entity_id"],
                                   "reason": "Not this season"})
            self.assertEqual(person.programme()["inquiries"][0], before)
            again = person.invoke("approve_inquiry", {"programme_id": pid, "draft_id": draft,
                                                      "sender_name": "Someone else",
                                                      "revision": person.programme()["revision"]})
            self.assertEqual(again["outcome"], "bad_args")

    def test_the_agent_can_draft_but_never_approve_by_any_road_the_page_offers(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            entity = board(person)[0]["entity_id"]
            as_agent = Browser(site, extra={"X-Open-Slot-As": "agent"})
            as_agent.cookie = person.cookie
            drafted = as_agent.invoke("draft_inquiry", {"programme_id": pid, "entity_id": entity})
            self.assertTrue(drafted["ok"])
            draft = drafted["result"]["draft_id"]
            args = {"programme_id": pid, "draft_id": draft, "sender_name": "Maya",
                    "revision": person.programme()["revision"]}
            self.assertEqual(as_agent.invoke("approve_inquiry", args)["outcome"], "not_a_tool")
            unlabelled = Browser(site, labelled=False)
            unlabelled.cookie = person.cookie
            self.assertEqual(unlabelled.invoke("approve_inquiry", args)["outcome"], "wrong_actor")
            address = person.agent_path()
            reply = site.post(f"{address}/invoke", {"tool": "approve_inquiry", "args": args})
            self.assertEqual(reply.json()["outcome"], "not_a_tool")
            self.assertEqual(person.programme()["inquiries"][0]["state"], "draft")
            self.assertEqual(person.invoke("approve_inquiry", args)["ok"], True)


class TheAgentConsole(unittest.TestCase):
    def test_each_human_verb_run_as_the_agent_from_the_console_is_not_a_tool_and_changes_nothing(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            console = Browser(site, extra={"X-Open-Slot-As": "agent"})
            console.cookie = person.cookie
            before = json.dumps(person.programme(), sort_keys=True)
            for verb in HUMAN:
                refused = console.invoke(verb, {"programme_id": pid})
                self.assertEqual((refused["ok"], refused["outcome"]), (False, "not_a_tool"), verb)
            self.assertEqual(json.dumps(person.programme(), sort_keys=True), before)
            names = {tool["name"] for tool in person.get("/api/tools").json()["tools"]}
            self.assertFalse(names & set(HUMAN))

    def test_a_tool_the_console_runs_is_logged_as_the_agents_and_the_registry_answers(self):
        with Site() as site:
            person = Browser(site).open()
            pid = ready(person)
            console = Browser(site, extra={"X-Open-Slot-As": "agent"})
            console.cookie = person.cookie
            budget = console.ok("qloo_budget", {"programme_id": pid})
            self.assertEqual((budget["mode"], budget["programme"]["data"]), ("synthetic", "synthetic"))
            console.ok("suggest_slots", {"programme_id": pid})
            actors = {entry["tool"]: entry["actor"] for entry in person.state()["workspace"]["activityLog"]}
            self.assertEqual(actors["suggest_slots"], "agent")
            self.assertEqual(actors["plan_acts"], "person")


class EveryStateTheDesignPromises(unittest.TestCase):
    def test_a_name_qloo_does_not_know_is_nothing_found_never_a_guess(self):
        with Site() as site:
            person = Browser(site).open()
            opened = person.ok("open_programme", {"label": "Test Hall", "nights": ["Fri 6 Nov"],
                                                  "anchors": ["Zzyzx Quartet", "No Such Podcast"]})
            found = person.invoke("find_anchor", {"programme_id": opened["programme_id"],
                                                  "name": "Zzyzx Quartet"})
            self.assertEqual((found["ok"], found["outcome"], found["result"]["candidates"]),
                             (True, "qloo_no_results", []))
            shown = person.programme()
            self.assertEqual(shown["anchors"][0]["candidates"], [])
            step = shown["trace"][-1]
            self.assertEqual((step["tool"], step["outcome"], step["request"]["params"]["query"]),
                             ("find_anchor", "qloo_no_results", "Zzyzx Quartet"))
            plan = person.invoke("plan_acts", {"programme_id": opened["programme_id"]})
            self.assertEqual(plan["outcome"], "needs_anchors")

    def test_an_ambiguous_name_shows_every_match_and_confirms_none_of_them(self):
        with Site() as site:
            person = Browser(site).open()
            person.sample()
            first = person.programme()["anchors"][0]
            self.assertEqual(len(first["candidates"]), 2)
            self.assertIsNone(first["confirmed"])
            self.assertEqual(person.invoke("plan_acts", {"programme_id": "prog_001"})["outcome"],
                             "anchor_unconfirmed")

    def test_a_cap_reached_stops_the_plan_and_only_the_person_raises_it_up_to_forty(self):
        with Site(max_calls=8) as site:
            person = Browser(site).open()
            pid = person.sample()
            person.confirm_all(pid)
            plan = person.ok("plan_acts", {"programme_id": pid})
            self.assertEqual(plan["stopped_because"], "cap")
            budget = person.programme()["budget"]
            self.assertEqual((budget["spent"], budget["cap"]), (8, 8))
            console = Browser(site, extra={"X-Open-Slot-As": "agent"})
            console.cookie = person.cookie
            self.assertEqual(console.invoke("raise_call_cap", {"programme_id": pid, "cap": 16})["outcome"],
                             "not_a_tool")
            for cap in (8, 7, 41):
                refused = person.invoke("raise_call_cap", {"programme_id": pid, "cap": cap})
                self.assertEqual(refused["outcome"], "bad_args", cap)
            self.assertEqual(person.ok("raise_call_cap", {"programme_id": pid, "cap": 16})["cap"], 16)
            again = person.ok("plan_acts", {"programme_id": pid})
            self.assertEqual(again["stopped_because"], "enough_ready")

    def test_a_plan_with_nothing_in_range_is_an_empty_board_and_every_reason_is_listed(self):
        with Site() as site:
            person = Browser(site).open()
            pid = person.sample()
            person.confirm_all(pid)
            person.ok("plan_acts", {"programme_id": pid, "band": {"min": 0.99, "max": 1.0}})
            view = person.programme()["view"]
            self.assertEqual((view["board"], view["also_connected"]), ([], []))
            self.assertTrue(view["set_aside"])
            self.assertTrue(all(item["reason"] for item in view["set_aside"]))
            self.assertEqual(person.ok("suggest_slots", {"programme_id": pid})["backups"], [])

    def test_a_qloo_error_is_a_named_stop_and_never_synthetic_data(self):
        def broken(request):
            if request["path"] == qloo.INSIGHTS:
                return 503, {"success": False}, {}
            return serve_fake(request)

        with Stub(broken) as stub:
            def engine(ledger):
                return providers.LiveProvider(fake_key(), ledger, stub.base_url, sleep=lambda seconds: None)

            with Site(engine=engine, key=fake_key(), provider="live") as site:
                person = Browser(site).open()
                pid = person.sample()
                person.confirm_all(pid)
                plan = person.ok("plan_acts", {"programme_id": pid})
                self.assertEqual(plan["stopped_because"], "qloo_refused")
                shown = person.programme()
                self.assertEqual(shown["candidates"], [])
                outcomes = {step["outcome"] for step in shown["trace"] if step["kind"] == "call"}
                self.assertIn("qloo_unavailable", outcomes)
                self.assertEqual({c["provider"] for c in site.runtime.doc["calls"]}, {"live"})
                self.assertIn("nothing falls back", shown["trace"][-1]["because"])

    def test_a_browser_that_sends_no_label_can_search_and_plan_but_cannot_confirm_or_approve(self):
        with Site() as site:
            unlabelled = Browser(site, labelled=False).open()
            shown = unlabelled.state()
            self.assertEqual((shown["actor"], shown["actorWhy"]), ("agent", "no_fetch_metadata"))
            pid = unlabelled.sample()
            first = unlabelled.programme()["anchors"][0]
            refused = unlabelled.invoke("confirm_anchor", {"programme_id": pid, "name": first["name"],
                                                          "entity_id": first["candidates"][0]["entityId"]})
            self.assertEqual(refused["outcome"], "wrong_actor")
            self.assertIsNone(unlabelled.programme()["anchors"][0]["confirmed"])
            self.assertIsNone(unlabelled.state()["agentPath"])


class WatchingAPlanFill(unittest.TestCase):
    def test_a_workspace_with_no_programme_has_nothing_to_watch(self):
        with Site() as site:
            person = Browser(site).open()
            self.assertIsNone(person.get("/api/calls").json()["programme"])

    def test_the_call_strip_and_the_trace_grow_while_a_plan_runs_and_name_no_act(self):
        reached, release = threading.Event(), threading.Event()

        class Gated(providers.FakeProvider):
            def fetch(self, request, origin=None):
                if request.path == qloo.TRENDING:
                    reached.set()
                    release.wait(10)
                return super().fetch(request, origin)

        with Site(engine=Gated()) as site:
            person = Browser(site).open()
            pid = person.sample()
            person.confirm_all(pid)
            running = []
            plan = threading.Thread(target=lambda: running.append(person.invoke(
                "plan_acts", {"programme_id": pid})), daemon=True)
            plan.start()
            self.assertTrue(reached.wait(10))
            watched = person.get("/api/calls").json()["programme"]
            self.assertEqual(watched["id"], pid)
            kinds = [step["kind"] for step in watched["steps"]]
            self.assertIn("note", kinds)
            self.assertGreaterEqual(watched["budget"]["spent"], 8)
            for step in watched["steps"]:
                self.assertNotIn("params", step.get("request", {}))
                self.assertNotIn("entityId", step)
                self.assertTrue(step["because"])
            text = json.dumps(watched)
            for name in ("Milk Carton", "Alvvays", "Bamford", "Hodgman", "Birchwater", "Cinderfield"):
                self.assertNotIn(name, text)
            release.set()
            plan.join(10)
            final = person.get("/api/calls").json()["programme"]
            self.assertGreater(len(final["steps"]), len(watched["steps"]))
            self.assertEqual(final["steps"][-1]["kind"], "stop")


class TheWholeGoldenPath(unittest.TestCase):
    """Section 9.1 of the plan, one step at a time, as the page's own calls."""

    def test_sample_confirm_run_cards_board_draft_approve_and_the_agents_refusal(self):
        with Site() as site:
            person = Browser(site).open()
            self.assertEqual(person.state()["sample"], scenario("lantern-room"))
            pid = person.sample()
            self.assertEqual(len(person.programme()["anchors"]), 4)
            self.assertTrue(all(a["confirmed"] is None for a in person.programme()["anchors"]))
            person.confirm_all(pid)
            planned = person.ok("plan_acts", {"programme_id": pid})
            calls = planned["calls"]
            self.assertEqual((planned["stopped_because"], calls["spent_in_total"], calls["cap"],
                              calls["not_spent"]), ("enough_ready", 12, 16, 8))
            card = board(person)[0]
            for key in ("links", "popularity", "trend", "tags", "ranked_by_text", "status_text"):
                self.assertTrue(card[key] is not None, key)
            self.assertTrue(person.programme()["view"]["set_aside"])
            person.ok("suggest_slots", {"programme_id": pid})
            night = person.programme()["nights"][0]
            person.ok("assign_slot", {"programme_id": pid, "night_id": night["id"],
                                      "entity_id": night["proposed"],
                                      "revision": person.programme()["revision"]})
            draft = person.ok("draft_inquiry", {"programme_id": pid, "entity_id": night["proposed"],
                                                "night_ids": [night["id"]]})
            self.assertIn("DRAFT", draft["text"])
            approved = person.ok("approve_inquiry", {"programme_id": pid, "draft_id": draft["draft_id"],
                                                     "sender_name": "Maya (fictional)",
                                                     "revision": person.programme()["revision"]})
            self.assertNotIn("DRAFT", approved["text"])
            console = Browser(site, extra={"X-Open-Slot-As": "agent"})
            console.cookie = person.cookie
            refusal = console.invoke("approve_inquiry", {"programme_id": pid, "draft_id": draft["draft_id"],
                                                         "sender_name": "Maya", "revision": 0})
            self.assertEqual(refusal["outcome"], "not_a_tool")
            people = [e for e in person.state()["workspace"]["activityLog"] if e["actor"] == "person"]
            self.assertEqual({e["tool"] for e in people} & set(HUMAN),
                             {"confirm_anchor", "assign_slot", "approve_inquiry"})
            agent = [e for e in person.state()["workspace"]["activityLog"] if e["actor"] == "agent"]
            self.assertTrue(all(e["tool"] not in HUMAN or e["outcome"] == "not_a_tool" for e in agent))


if __name__ == "__main__":
    unittest.main()
