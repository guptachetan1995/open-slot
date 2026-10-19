import json
import unittest
from unittest import mock

from open_slot import pins, planner
from open_slot.store import Refusal
from tests.rig import GONE, TAG, THIN, Rig, scenario, serve_fake
from tests.support import Stub, fake_key

NAMES_READY_IN_A = ["Hollowmere Radio", "Odile Thackeray", "The Gallowglass Hour", "Lindenwick Trio"]


def calls(trace):
    return [s for s in trace if s["kind"] == "call"]


def paths(trace):
    return [(s["tool"], s["request"]["path"]) for s in calls(trace)]


def plan(rig, programme_id, **args):
    return rig.ok("plan_acts", {"programme_id": programme_id, **args})


class ScenarioA(unittest.TestCase):
    """The Lantern Room: 3 nights, 4 acts. The plan counted 11 calls on the assumption that the comedy
    tag is pinned; it is not, so the plan looks it up once and the count is 12."""

    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.ready()
        self.result = plan(self.rig, self.pid)

    def test_the_counts_after_the_four_searches_four_wide_calls_four_trend_checks_and_a_tag_lookup(self):
        calls_ = self.result["calls"]
        self.assertEqual((calls_["spent_by_this_plan"], calls_["spent_in_total"], calls_["cap"]), (8, 12, 16))
        self.assertEqual(calls_["not_spent"], 8)
        self.assertEqual(self.result["stopped_because"], "enough_ready")
        self.assertEqual(self.result["stopped_text"],
                         "stopped: 4 candidates are ready for 3 nights; 8 calls not spent")

    def test_the_path_one_wide_call_per_kind_then_trends_in_rank_order(self):
        self.assertEqual(paths(self.rig.stored(self.pid)["trace"]),
                         [("find_anchor", "/search")] * 4 + [
                             ("plan_acts", "/v2/insights"), ("plan_acts", "/v2/insights"),
                             ("find_tag", "/v2/tags"), ("plan_acts", "/v2/insights")]
                         + [("check_trend", "/v2/trending")] * 4)

    def test_the_four_ready_and_the_pile_set_aside_with_no_call(self):
        self.assertEqual(self.result["counts"], {"ready": 4, "watch": 8, "fading": 0, "weak_link": 14,
                                                 "outside_band": 12, "dropped": 0})
        self.assertEqual([c["name"] for c in self.result["board"][:4]], NAMES_READY_IN_A)
        self.assertEqual(len(self.result["board"]), 8)
        self.assertEqual(len(self.result["also_connected"]), 4)
        self.assertEqual(len(self.result["set_aside"]), 26)
        self.assertTrue(all(s["reason"] for s in self.result["set_aside"]))

    def test_no_lens_comes_up_short_so_there_is_no_range_filtered_call(self):
        for step in calls(self.result["trace"]):
            self.assertNotIn("filter.popularity.min", step["request"]["params"])

    def test_the_wide_calls_send_the_documented_parameters_and_no_popularity_filter(self):
        wide = [s for s in calls(self.result["trace"]) if s["request"]["path"] == "/v2/insights"]
        self.assertEqual(len(wide), 3)
        for step in wide:
            params = step["request"]["params"]
            self.assertEqual((params["take"], params["feature.explainability"], params["bias.trends"]),
                             ("50", "true", "medium"))
            self.assertEqual(params["signal.interests.entities"], params["filter.exclude.entities"])
            self.assertEqual(len(params["signal.interests.entities"].split(",")), 4)
        self.assertEqual([s["request"]["params"]["filter.type"] for s in wide],
                         ["urn:entity:artist", "urn:entity:podcast", "urn:entity:person"])
        self.assertEqual(wide[2]["request"]["params"]["filter.tags"], TAG)
        self.assertNotIn("filter.tags", wide[0]["request"]["params"])

    def test_every_call_says_why_and_the_planners_calls_are_the_agents(self):
        for step in self.result["trace"]:
            self.assertTrue(step["because"])
            self.assertEqual(step["actor"], "agent")
        trends = [s for s in calls(self.result["trace"]) if s["tool"] == "check_trend"]
        self.assertEqual({s["because"] for s in trends} <= {
            "connected to 4 of 4 acts, in range, and no trend yet",
            "connected to 3 of 4 acts, in range, and no trend yet"}, True)
        self.assertEqual(self.result["trace"][-1]["kind"], "stop")
        self.assertEqual(self.result["trace"][-1]["code"], "enough_ready")

    def test_the_triage_line_says_what_was_sorted_with_no_call(self):
        triage = next(s for s in self.result["trace"] if s["because"].startswith("triage"))
        self.assertEqual(triage["because"], "triage, no call: 38 candidates; 12 outside your room's range, "
                                            "14 connected to fewer than 2 acts of yours, 12 qualify")

    def test_the_trace_opens_with_the_rooms_range_and_the_rule_that_gave_it(self):
        self.assertEqual(self.result["trace"][0]["because"],
                         "your room's range is Qloo popularity 0.40 to 0.76, from your 4 confirmed acts "
                         "(this app's rule: lowest minus 0.15, highest plus 0.05)")

    def test_the_trace_the_plan_returns_is_the_tail_of_the_stored_one(self):
        stored = self.rig.stored(self.pid)["trace"]
        self.assertEqual(self.result["trace"], stored[4:])
        self.assertEqual([s["step"] for s in stored], list(range(1, len(stored) + 1)))
        spent = [s["spent"] for s in calls(stored)]
        self.assertEqual(spent, sorted(spent))
        self.assertEqual(spent[-1], 12)

    def test_the_budget_is_stored_with_the_programme(self):
        self.assertEqual(self.rig.stored(self.pid)["budget"],
                         {"cap": 16, "spent": 12, "saved": 8, "stoppedBecause": "enough_ready"})
        self.assertEqual(self.rig.stored(self.pid)["range"]["source"], "anchors")

    def test_the_board_is_stored_in_rank_order_with_the_ready_first(self):
        held = self.rig.stored(self.pid)["candidates"]
        self.assertEqual([c["name"] for c in held[:4]], NAMES_READY_IN_A)
        self.assertEqual([c["status"] for c in held[:5]], ["ready"] * 4 + ["watch"])


class WithTheTagPinnedTheCountsAreTheSpecs(unittest.TestCase):
    def test_a_documented_tag_makes_a_11_and_b_6_calls(self):
        with mock.patch.dict(pins.LENS_DEFAULTS["comedy"], {"tag": TAG}):
            rig = Rig()
            pid = rig.planned()
            self.assertEqual(rig.stored(pid)["budget"]["spent"], 11)
            self.assertNotIn("find_tag", [s["tool"] for s in rig.stored(pid)["trace"]])
            other = Rig()
            pid = other.planned(scenario("campus-board"))
            self.assertEqual(other.stored(pid)["budget"]["spent"], 6)

    def test_a_tag_the_agent_looked_up_itself_costs_the_same_call_once(self):
        rig = Rig()
        pid = rig.ready()
        found = rig.ok("find_tag", {"programme_id": pid, "query": "stand-up"})
        self.assertEqual([t["id"] for t in found["tags"]], [TAG])
        result = plan(rig, pid)
        self.assertEqual(result["calls"]["spent_in_total"], 12)
        self.assertEqual([t for t, _ in paths(rig.stored(pid)["trace"])].count("find_tag"), 1)

    def test_a_tag_given_to_the_plan_is_used_and_no_lookup_is_made(self):
        rig = Rig()
        pid = rig.ready()
        rig.ok("find_tag", {"programme_id": pid, "query": "stand-up"})
        before = rig.stored(pid)["budget"]["spent"]
        result = plan(rig, pid, lens_tags={"comedy": TAG})
        self.assertEqual(result["calls"]["spent_by_this_plan"], 7)
        self.assertEqual(rig.stored(pid)["budget"]["spent"], before + 7)


class ScenarioB(unittest.TestCase):
    def test_one_lens_three_ready_after_three_trend_checks_and_three_not_spent(self):
        rig = Rig()
        pid = rig.ready(scenario("campus-board"))
        result = plan(rig, pid)
        self.assertEqual((result["calls"]["spent_in_total"], result["calls"]["spent_by_this_plan"]), (7, 5))
        self.assertEqual(result["calls"]["not_spent"], 3)
        self.assertEqual(result["counts"]["ready"], 3)
        self.assertEqual(result["counts"]["outside_band"] + result["counts"]["weak_link"], 14)
        self.assertEqual(result["stopped_text"],
                         "stopped: 3 candidates are ready for 2 nights; 3 calls not spent")


class ScenarioC(unittest.TestCase):
    """A thin lens: the unfiltered 50 are spent above the room, so only a filtered call finds acts."""

    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.ready(THIN)
        self.result = plan(self.rig, self.pid)

    def test_step_2b_is_one_extra_call_and_the_three_ready_come_from_it(self):
        self.assertEqual(self.result["calls"]["spent_in_total"], 7)
        self.assertEqual(self.result["counts"]["ready"], 3)
        self.assertEqual(paths(self.rig.stored(self.pid)["trace"]),
                         [("find_anchor", "/search")] * 2 + [("plan_acts", "/v2/insights")] * 2
                         + [("check_trend", "/v2/trending")] * 3)

    def test_the_filtered_call_carries_the_rooms_range_and_the_first_does_not(self):
        first, second = [s for s in calls(self.result["trace"]) if s["request"]["path"] == "/v2/insights"]
        self.assertNotIn("filter.popularity.min", first["request"]["params"])
        self.assertEqual((second["request"]["params"]["filter.popularity.min"],
                          second["request"]["params"]["filter.popularity.max"]), ("0.3", "0.6"))
        self.assertIn("only a filtered call can find the ones in reach", second["because"])
        self.assertIn("only 1 connected acts in range for 2 open nights (3 needed)", second["because"])

    def test_the_boundary_exactly_nights_plus_one_qualifiers_is_not_short(self):
        rig = Rig()
        pid = rig.ready({**scenario("lantern-room"), "nights": ["a", "b", "c"]})
        self.assertNotIn("filter.popularity.min", json.dumps(plan(rig, pid)["trace"]))
        rig = Rig()
        pid = rig.ready({**scenario("lantern-room"), "nights": ["a", "b", "c", "d"]})
        result = plan(rig, pid)
        filtered = [s for s in calls(result["trace"]) if "filter.popularity.min" in s["request"]["params"]]
        self.assertEqual([s["request"]["params"]["filter.type"] for s in filtered],
                         ["urn:entity:artist", "urn:entity:podcast", "urn:entity:person"])


class TheStopRules(unittest.TestCase):
    def test_the_programmes_cap_stops_the_plan_with_what_it_has(self):
        rig = Rig(max_calls=8)
        pid = rig.ready()
        result = plan(rig, pid)
        self.assertEqual((result["stopped_because"], result["calls"]["spent_in_total"]), ("cap", 8))
        self.assertEqual(result["counts"]["ready"], 0)
        self.assertEqual(result["counts"]["watch"], 12)
        self.assertEqual(result["stopped_text"],
                         "stopped: the programme's cap of 8 live calls is reached; 12 trend checks not run")
        self.assertEqual(rig.stored(pid)["budget"]["stoppedBecause"], "cap")

    def test_a_cap_reached_in_the_middle_of_the_wide_pass_keeps_the_kinds_already_read(self):
        rig = Rig(max_calls=6)
        pid = rig.ready()
        result = plan(rig, pid)
        self.assertEqual(result["stopped_because"], "cap")
        self.assertEqual({c["lens"] for c in rig.stored(pid)["candidates"]}, {"music", "podcast"})

    def test_max_calls_limits_one_plan_without_touching_the_cap(self):
        rig = Rig()
        pid = rig.ready()
        result = plan(rig, pid, max_calls=5)
        self.assertEqual((result["stopped_because"], result["calls"]["spent_by_this_plan"]), ("cap", 5))
        self.assertEqual(rig.stored(pid)["budget"]["cap"], 16)
        again = plan(rig, pid)
        self.assertEqual(again["stopped_because"], "enough_ready")

    def test_a_cap_already_spent_stops_a_plan_before_any_call_and_changes_nothing_but_the_trace(self):
        rig = Rig(max_calls=4)
        pid = rig.ready()
        result = plan(rig, pid)
        self.assertEqual((result["stopped_because"], result["calls"]["spent_by_this_plan"]), ("cap", 0))
        self.assertEqual(rig.stored(pid)["candidates"], [])

    def test_every_candidate_that_qualifies_having_been_checked_is_the_exhausted_stop(self):
        rig = Rig()
        campus = {**scenario("campus-board"), "nights": [f"Night {n}" for n in range(1, 7)]}
        pid = rig.ready(campus)
        result = plan(rig, pid)
        self.assertEqual(result["stopped_because"], "shortlist_exhausted")
        self.assertEqual(result["calls"]["not_spent"], 0)
        held = rig.stored(pid)["candidates"]
        reach = [c for c in held if c["status"] in ("ready", "watch", "fading")]
        self.assertTrue(all(c["trend"] is not None for c in reach))

    def test_with_trend_checks_off_nothing_is_ready_and_only_the_wide_calls_run(self):
        with mock.patch.object(planner, "TREND_CHECKS", False):
            rig = Rig()
            pid = rig.ready()
            result = plan(rig, pid)
            self.assertEqual(result["stopped_because"], "trends_off")
            self.assertEqual((result["counts"]["ready"], result["counts"]["watch"]), (0, 12))
            self.assertEqual(result["calls"]["spent_in_total"], 8)
            self.assertEqual(result["calls"]["not_spent"], 0)
            other = Rig()
            pid = other.ready(scenario("campus-board"))
            result = plan(other, pid)
            counts = result["counts"]
            self.assertEqual((counts["ready"], counts["watch"], result["calls"]["spent_in_total"]), (0, 6, 4))
            self.assertNotIn("check_trend", [s["tool"] for s in other.stored(pid)["trace"]])


class WhoStartedThePlanDecidesWhoseAllowance(unittest.TestCase):
    def test_a_plan_the_agent_started_stops_when_the_agents_allowance_is_spent(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, agent_calls_per_day=6)
            pid = rig.ready()
            result = plan(rig, pid)
        self.assertEqual(result["stopped_because"], "agent_allowance")
        self.assertEqual(rig.ledger.agent_used(rig.workspace), 6)
        self.assertEqual(result["calls"]["spent_in_total"], 6)
        self.assertIn("allowance", result["stopped_text"])

    def test_the_same_plan_started_by_the_person_goes_on_and_costs_the_agent_nothing(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, agent_calls_per_day=0)
            pid = rig.open(scenario("lantern-room"))
            rig.search(pid, actor="person")
            for name in [a["name"] for a in rig.stored(pid)["anchors"]]:
                held = rig.stored(pid)["anchors"]
                top = next(a for a in held if a["name"] == name)["candidates"][0]["entityId"]
                rig.ok("confirm_anchor", {"programme_id": pid, "name": name, "entity_id": top}, "person")
            result = plan(rig, pid)
            person = rig.call("plan_acts", {"programme_id": pid}, "person")
        self.assertEqual(result["stopped_because"], "agent_allowance")
        self.assertEqual(person["result"]["stopped_because"], "enough_ready")
        self.assertEqual(rig.ledger.agent_used(rig.workspace), 0)

    def test_opening_another_programme_does_not_reset_the_agents_allowance(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, agent_calls_per_day=5, max_calls=16)
            first = rig.open(scenario("lantern-room"))
            rig.search(first)
            second = rig.open(scenario("campus-board"))
            self.assertEqual(rig.stored(second)["budget"]["spent"], 0)
            names = [a["name"] for a in rig.stored(second)["anchors"]]
            self.assertTrue(rig.call("find_anchor", {"programme_id": second, "name": names[0]})["ok"])
            self.assertEqual(rig.refused("find_anchor", {"programme_id": second, "name": names[1]}),
                             "agent_allowance_spent")
        self.assertEqual(len(stub.requests), 5)

    def test_the_servers_daily_ceiling_stops_a_plan_whoever_started_it(self):
        for actor in ("agent", "person"):
            with Stub(serve_fake) as stub:
                rig = Rig.live(stub, daily_live_calls=7)
                pid = rig.open(scenario("lantern-room"))
                rig.search(pid, actor="person")
                for anchor in rig.stored(pid)["anchors"]:
                    rig.ok("confirm_anchor", {"programme_id": pid, "name": anchor["name"],
                                              "entity_id": anchor["candidates"][0]["entityId"]}, "person")
                result = rig.ok("plan_acts", {"programme_id": pid}, actor)
            self.assertEqual(result["stopped_because"], "ceiling", actor)
            self.assertEqual(rig.ledger.daily_used(), 7)
            self.assertEqual(len(stub.requests), 7)

    def test_a_call_another_workspace_took_the_last_of_is_the_same_stop_not_an_error(self):
        for code, stop in (("daily_allowance_spent", "ceiling"),
                           ("agent_allowance_spent", "agent_allowance")):
            with Stub(serve_fake) as stub:
                rig = Rig.live(stub)
                pid = rig.ready()
                provider, sent = rig.runtime.provider, []
                real = provider.fetch

                def fetch(request, origin, real=real, sent=sent, code=code):
                    if len(sent) == 3:
                        raise Refusal(code, "taken between the check and the call")
                    sent.append(request.path)
                    return real(request, origin)

                provider.fetch = fetch
                result = rig.ok("plan_acts", {"programme_id": pid})
            self.assertEqual(result["stopped_because"], stop, code)
            self.assertEqual(result["calls"]["spent_by_this_plan"], 3)

    def test_a_cached_plan_costs_no_live_call_and_the_trace_says_cache(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub)
            pid = rig.planned()
            sent = len(stub.requests)
            second = rig.open(scenario("lantern-room"))
            rig.confirm_top(second)
            result = plan(rig, second)
        self.assertEqual(len(stub.requests), sent)
        self.assertEqual(result["calls"]["spent_in_total"], 0)
        self.assertTrue(all(s["cache"] == "hit" for s in calls(rig.stored(second)["trace"])))
        self.assertEqual(result["stopped_because"], "enough_ready")
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 12)


class WhenQloo(unittest.TestCase):
    @staticmethod
    def insights(request, kind):
        return request["path"] == "/v2/insights" and request["query"]["filter.type"] == f"urn:entity:{kind}"

    def answer(self, broken):
        def serve(request):
            if broken(request):
                return broken(request)
            return serve_fake(request)
        return serve

    def test_refuses_twice_running_after_its_retries_the_plan_stops_and_never_falls_back(self):
        serve = self.answer(lambda r: (429, {"message": "slow down"}, {})
                            if r["path"] == "/v2/insights" else None)
        with Stub(serve) as stub:
            rig = Rig.live(stub)
            pid = rig.ready()
            result = plan(rig, pid)
        self.assertEqual(result["stopped_because"], "qloo_refused")
        self.assertEqual(result["calls"]["spent_in_total"], 4 + 3 + 3)
        self.assertEqual(rig.stored(pid)["candidates"], [])
        self.assertEqual(rig.sleeps, [1.0, 3.0, 1.0, 3.0])
        self.assertTrue(all(s["provider"] == "live" for s in rig.runtime.doc["calls"]))
        self.assertIn("nothing falls back to synthetic data", result["stopped_text"])

    def test_refuses_once_the_kind_is_left_out_and_the_plan_goes_on(self):
        serve = self.answer(lambda r: (503, {"message": "x"}, {}) if self.insights(r, "artist") else None)
        with Stub(serve) as stub:
            rig = Rig.live(stub)
            pid = rig.ready()
            result = plan(rig, pid)
        self.assertEqual(result["stopped_because"], "enough_ready")
        self.assertEqual({c["lens"] for c in rig.stored(pid)["candidates"]}, {"podcast", "comedy"})
        skipped = [s for s in result["trace"] if s["kind"] == "skip"]
        self.assertEqual(len(skipped), 1)
        self.assertIn("music", skipped[0]["because"])

    def test_rejects_the_key_the_plan_is_refused_with_qloo_auth_and_the_call_is_still_counted(self):
        serve = self.answer(lambda r: (401, {"message": "No API key found in request"}, {}))
        with Stub(serve) as stub:
            rig = Rig.live(stub)
            pid = rig.open(scenario("lantern-room"))
            envelope = rig.call("find_anchor", {"programme_id": pid, "name": "Alvvays"})
        self.assertEqual(envelope["outcome"], "qloo_auth")
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 1)
        self.assertEqual(rig.stored(pid)["trace"][-1]["outcome"], "qloo_auth")

    def test_does_not_support_a_kind_that_kind_is_left_out_and_the_plan_goes_on(self):
        serve = self.answer(lambda r: (403, {"message": "unsupported"}, {})
                            if self.insights(r, "podcast") else None)
        with Stub(serve) as stub:
            rig = Rig.live(stub)
            result = plan(rig, rig.ready())
        self.assertIn("podcast: Qloo could not answer (qloo_unsupported_type)",
                      [s["because"] for s in result["trace"] if s["kind"] == "skip"][0])
        self.assertEqual(result["stopped_because"], "enough_ready")

    def test_has_no_trend_for_a_candidate_it_stays_watch_and_is_not_asked_again(self):
        serve = self.answer(lambda r: (200, {"results": {"trends": []}}, {})
                            if r["path"] == "/v2/trending" else None)
        with Stub(serve) as stub:
            rig = Rig.live(stub, max_calls=40)
            pid = rig.ready()
            result = plan(rig, pid)
        self.assertEqual(result["stopped_because"], "shortlist_exhausted")
        self.assertEqual(result["counts"]["ready"], 0)
        self.assertEqual(result["counts"]["watch"], 12)
        trends = [s for s in calls(rig.stored(pid)["trace"]) if s["tool"] == "check_trend"]
        self.assertEqual(len(trends), 12)
        self.assertTrue(all(s["outcome"] == "qloo_no_results" for s in trends))
        self.assertTrue(all(c["trend"]["state"] == "unknown" for c in rig.stored(pid)["candidates"]
                            if c["status"] == "watch"))

    def test_sends_an_answer_without_explanation_scores_the_plan_fails_loudly(self):
        def serve(request):
            status, body, headers = serve_fake(request)
            if request["path"] == "/v2/insights":
                for entity in body["results"]["entities"]:
                    entity.pop("query", None)
            return status, body, headers

        with Stub(serve) as stub:
            rig = Rig.live(stub)
            pid = rig.ready()
            envelope = rig.call("plan_acts", {"programme_id": pid})
        self.assertEqual(envelope["outcome"], "qloo_unknown_shape")
        self.assertIn("explainability", envelope["error"]["message"])


class TheGateBeforeAPlan(unittest.TestCase):
    def test_a_fresh_programme_needs_anchors(self):
        rig = Rig()
        pid = rig.open(scenario("lantern-room"))
        self.assertEqual(rig.refused("plan_acts", {"programme_id": pid}), "needs_anchors")
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 0)

    def test_found_but_unconfirmed_acts_are_anchor_unconfirmed_and_the_agent_cannot_confirm_them(self):
        rig = Rig()
        pid = rig.open(scenario("lantern-room"))
        rig.search(pid)
        self.assertEqual(rig.refused("plan_acts", {"programme_id": pid}), "anchor_unconfirmed")
        self.assertEqual(rig.refused("plan_acts", {"programme_id": pid}, "person"), "anchor_unconfirmed")
        first = rig.stored(pid)["anchors"][0]
        rig.ok("confirm_anchor", {"programme_id": pid, "name": first["name"],
                                  "entity_id": first["candidates"][0]["entityId"]}, "person")
        self.assertEqual(rig.refused("plan_acts", {"programme_id": pid}), "anchor_unconfirmed")
        self.assertEqual(rig.stored(pid)["candidates"], [])
        self.assertEqual(rig.stored(pid)["budget"]["spent"], 4)

    def test_one_confirmed_act_is_not_enough_even_when_nothing_else_was_found(self):
        rig = Rig()
        pid = rig.open({"label": "A room", "nights": ["Tue"], "anchors": ["Alvvays", "Nobody Known"]})
        found = rig.search(pid)
        self.assertEqual(found["Nobody Known"], [])
        rig.ok("confirm_anchor", {"programme_id": pid, "name": "Alvvays",
                                  "entity_id": found["Alvvays"][0]["entityId"]}, "person")
        self.assertEqual(rig.refused("plan_acts", {"programme_id": pid}), "needs_anchors")

    def test_an_act_qloo_has_nothing_for_is_left_out_and_only_confirmed_ids_are_sent(self):
        rig = Rig()
        names = scenario("lantern-room")["anchors"][:3] + ["Nobody Known"]
        pid = rig.ready({**scenario("lantern-room"), "anchors": names})
        result = plan(rig, pid)
        confirmed = {a["confirmed"] for a in rig.stored(pid)["anchors"] if a["confirmed"]}
        for step in calls(result["trace"]):
            if step["request"]["path"] == "/v2/insights":
                self.assertEqual(set(step["request"]["params"]["signal.interests.entities"].split(",")),
                                 confirmed)
        self.assertEqual(result["stopped_because"], "enough_ready")

    def test_a_tag_the_programme_does_not_know_is_unknown_tag_and_a_lens_it_lacks_is_refused(self):
        rig = Rig()
        pid = rig.ready()
        for tag in ("urn:tag:made:up", 5, None):
            self.assertEqual(rig.refused("plan_acts", {"programme_id": pid, "lens_tags": {"comedy": tag}}),
                             "unknown_tag", tag)
        rig.ok("find_tag", {"programme_id": pid, "query": "stand-up"})
        self.assertEqual(rig.refused("plan_acts", {"programme_id": pid, "lens_tags": {"movies": TAG}}),
                         "bad_args")
        self.assertEqual(rig.stored(pid)["candidates"], [])

    def test_a_band_replaces_the_range_and_the_trace_shows_it(self):
        rig = Rig()
        pid = rig.ready()
        result = plan(rig, pid, band={"min": 0, "max": 1})
        self.assertEqual((result["range"]["min"], result["range"]["max"], result["range"]["source"]),
                         (0, 1, "moved"))
        self.assertEqual(result["range"]["movedBy"], "agent")
        self.assertEqual(result["counts"]["outside_band"], 0)
        by_person = rig.ok("plan_acts", {"programme_id": pid, "band": {"min": 0.3, "max": 0.8}}, "person")
        self.assertEqual(by_person["range"]["movedBy"], "person")
        self.assertEqual(by_person["trace"][0]["because"],
                         "your room's range is Qloo popularity 0.30 to 0.80, set by the person")

    def test_a_band_that_is_not_two_percentiles_in_order_is_refused(self):
        rig = Rig()
        pid = rig.ready()
        for band in ({"min": 0.9, "max": 0.2}, {"min": -1, "max": 0.5}, {"min": 0.1, "max": 2},
                     {"min": 0.1}, {"min": "a", "max": "b"}, {"lo": 0.1, "hi": 0.9}):
            self.assertEqual(rig.refused("plan_acts", {"programme_id": pid, "band": band}), "bad_args", band)
        self.assertEqual(rig.refused("plan_acts", {"programme_id": pid, "max_calls": 0}), "bad_args")


class RunningAPlanAgain(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(max_calls=40)
        self.pid = self.rig.planned()

    def test_a_drop_survives_and_the_plan_looks_for_another_ready_act(self):
        first = self.rig.stored(self.pid)["candidates"][0]
        self.rig.ok("drop_act", {"programme_id": self.pid, "entity_id": first["entityId"], "reason": "no"},
                    "person")
        result = plan(self.rig, self.pid)
        held = self.rig.held(self.pid, first["name"])
        self.assertEqual((held["status"], held["dropped"]), ("dropped", "no"))
        self.assertEqual(result["counts"]["ready"], 4)
        self.assertEqual(result["stopped_because"], "enough_ready")
        self.assertTrue(any(s["because"].startswith("music: only 3 connected acts in range")
                            for s in result["trace"]))
        self.assertGreater(result["calls"]["spent_by_this_plan"], 4)

    def test_trends_already_found_are_kept_so_a_second_plan_makes_only_the_wide_calls(self):
        result = plan(self.rig, self.pid)
        self.assertEqual(result["calls"]["spent_by_this_plan"], 3)
        self.assertEqual([c["name"] for c in result["board"][:4]], NAMES_READY_IN_A)
        self.assertEqual(result["stopped_because"], "enough_ready")

    def test_proposals_are_reset_by_a_new_plan_and_an_assignment_stays(self):
        self.rig.ok("suggest_slots", {"programme_id": self.pid})
        ready = self.rig.stored(self.pid)["nights"][0]["proposed"]
        self.rig.ok("assign_slot", {"programme_id": self.pid, "night_id": "n1", "entity_id": ready,
                                    "revision": self.rig.stored(self.pid)["revision"]}, "person")
        plan(self.rig, self.pid)
        nights = self.rig.stored(self.pid)["nights"]
        self.assertEqual((nights[0]["state"], nights[0]["assigned"]), ("assigned", ready))
        self.assertEqual([n["state"] for n in nights[1:]], ["open", "open"])

    def test_an_act_a_night_or_a_draft_refers_to_stays_even_if_the_new_pass_does_not_return_it(self):
        held = self.rig.stored(self.pid)
        gone = {**held["candidates"][0], "entityId": GONE, "name": "Gone Act"}
        held["candidates"].append(gone)
        held["nights"][2].update(state="assigned", assigned=gone["entityId"])
        plan(self.rig, self.pid)
        self.assertIn("Gone Act", [c["name"] for c in self.rig.stored(self.pid)["candidates"]])


class TheEvidence(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.pid = self.rig.planned()
        self.top = self.rig.stored(self.pid)["candidates"][0]
        self.card = self.rig.ok("explain_act", {"programme_id": self.pid, "entity_id": self.top["entityId"]})

    def test_every_figure_says_whose_it_is(self):
        self.assertIn("Qloo", self.card["popularity"]["source"])
        self.assertIn("this app's", self.card["popularity"]["room_range"]["source"])
        self.assertTrue(all("Qloo" in link["source"] for link in self.card["links"]))
        self.assertIn("this app's rule", self.card["trend"]["source"])
        self.assertIn("Qloo's weekly rows", self.card["trend"]["source"])
        self.assertIn("0.1", self.card["link_rule"])

    def test_the_card_says_why_it_was_placed_and_combines_nothing_into_a_rating(self):
        self.assertEqual(self.card["ranked_by"], "links")
        self.assertIn("Qloo's explanation scores", self.card["ranked_by_text"])
        self.assertEqual(self.card["status_text"], "Connected, in range, trend not falling")
        for key in self.card:
            self.assertNotIn(key, ("score", "rating", "rank_score", "total"))

    def test_the_links_are_qloos_scores_for_the_acts_that_reach_the_threshold(self):
        scores = [link["score"] for link in self.card["links"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(s >= 0.1 for s in scores))
        self.assertEqual(len(self.card["links"]), 4)

    def test_explaining_calls_nothing(self):
        before = self.rig.stored(self.pid)["budget"]["spent"]
        self.rig.ok("explain_act", {"programme_id": self.pid, "entity_id": self.top["entityId"]})
        self.assertEqual(self.rig.stored(self.pid)["budget"]["spent"], before)
        self.assertEqual(self.rig.refused("explain_act", {"programme_id": self.pid, "entity_id": "nope"}),
                         "unknown_entity")


class TheKeyAndTheRepositoryNeverSeeTheRoom(unittest.TestCase):
    def test_the_label_the_nights_and_the_capacity_never_reach_qloo(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub)
            args = {**scenario("lantern-room"), "label": "Zorbulon Hall", "capacity_note": "777 seats",
                    "nights": ["Zed 1 Nov", "Zed 2 Nov", "Zed 3 Nov"]}
            pid = rig.ready(args)
            plan(rig, pid)
            draft = rig.ok("draft_inquiry", {"programme_id": pid,
                                             "entity_id": rig.stored(pid)["candidates"][0]["entityId"]})
        self.assertIn("Zorbulon Hall", draft["text"])
        sent = json.dumps(stub.requests)
        for private in ("Zorbulon", "777", "Zed", "seats"):
            self.assertNotIn(private, sent)
        self.assertTrue(all(r["headers"]["x-api-key"] == fake_key() for r in stub.requests))


if __name__ == "__main__":
    unittest.main()
