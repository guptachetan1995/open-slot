import unittest

from open_slot import providers
from tests.rig import Rig, scenario, serve_fake
from tests.support import Stub, fake_key
from tests.webrig import Browser, Site


def live_site(stub, **changes):
    """A site on the live provider talking to `stub`, with the synthetic provider as its fallback."""
    return Site(engine=lambda ledger: providers.LiveProvider(fake_key(), ledger, stub.base_url),
                fallback=providers.FakeProvider(), key=fake_key(), provider="live", **changes)


class TheModeIsNamedByWhatTheServerRuns(unittest.TestCase):
    def test_a_synthetic_server_is_synthetic_whatever_is_left_of_a_ceiling(self):
        self.assertEqual(Rig().runtime.mode(), "synthetic")

    def test_a_live_server_is_live_until_its_ceiling_is_used_up_and_then_spent(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, daily_live_calls=4)
            self.assertEqual(rig.runtime.mode(), "live")
            pid = rig.open(scenario("lantern-room"))
            rig.search(pid)
            self.assertEqual(rig.runtime.mode(), "spent")
            self.assertEqual(rig.ledger.daily_used(), 4)

    def test_the_mode_comes_back_to_live_when_the_utc_day_turns_over(self):
        now = [1_800_000_000.0]
        with Stub(serve_fake) as stub:
            changes = {"key": fake_key(), "provider": "live", "daily_live_calls": 1}
            ledger = providers.Ledger(1, 48, 0, clock=lambda: now[0])
            provider = providers.LiveProvider(fake_key(), ledger, stub.base_url)
            rig = Rig(provider, ledger, providers.FakeProvider(), **changes)
            pid = rig.open(scenario("lantern-room"))
            rig.ok("find_anchor", {"programme_id": pid, "name": "Alvvays"})
            self.assertEqual(rig.runtime.mode(), "spent")
            now[0] += 86400
            self.assertEqual(rig.runtime.mode(), "live")
            self.assertEqual(rig.ok("open_programme", scenario("campus-board"))["data"], "live")


class AProgrammeKeepsTheProviderItWasOpenedOn(unittest.TestCase):
    def test_a_programme_opened_while_the_ceiling_is_spent_runs_on_synthetic_data_and_says_so(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, providers.FakeProvider(), daily_live_calls=0)
            opened = rig.ok("open_programme", scenario("lantern-room"))
            self.assertEqual(opened["data"], "synthetic")
            pid = opened["programme_id"]
            self.assertEqual(rig.stored(pid)["provider"], "fake")
            rig.confirm_top(pid)
            result = rig.ok("plan_acts", {"programme_id": pid})
            self.assertEqual(result["stopped_because"], "enough_ready")
            self.assertEqual(rig.ledger.daily_used(), 0)
            self.assertEqual(stub.requests, [])
            self.assertEqual({c["provider"] for c in rig.runtime.doc["calls"]}, {"fake"})
            self.assertEqual({s["cache"] for s in rig.stored(pid)["trace"] if s["kind"] == "call"}, {"fake"})
            shown = rig.ok("qloo_budget", {"programme_id": pid})
            self.assertEqual((shown["mode"], shown["programme"]["data"]), ("spent", "synthetic"))

    def test_a_programme_opened_before_the_ceiling_ran_out_never_moves_to_synthetic_data(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, providers.FakeProvider(), daily_live_calls=6)
            opened = rig.ok("open_programme", scenario("lantern-room"))
            self.assertEqual(opened["data"], "live")
            pid = opened["programme_id"]
            rig.confirm_top(pid)
            self.assertEqual(rig.runtime.mode(), "live")
            result = rig.ok("plan_acts", {"programme_id": pid}, "person")
            self.assertEqual(result["stopped_because"], "ceiling")
            self.assertEqual(result["calls"]["spent_by_this_plan"], 2)
            self.assertEqual(rig.runtime.mode(), "spent")
            self.assertEqual(rig.refused("find_tag", {"programme_id": pid, "query": "stand-up"}),
                             "daily_allowance_spent")
            self.assertEqual(rig.stored(pid)["provider"], "live")
            self.assertEqual({c["provider"] for c in rig.runtime.doc["calls"]}, {"live"})
            self.assertEqual(len(stub.requests), 6)

    def test_without_a_fallback_a_spent_ceiling_refuses_and_nothing_turns_synthetic(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, daily_live_calls=0)
            opened = rig.ok("open_programme", scenario("lantern-room"))
            self.assertEqual(opened["data"], "live")
            self.assertEqual(rig.refused("find_anchor", {"programme_id": opened["programme_id"],
                                                         "name": "Alvvays"}), "daily_allowance_spent")

    def test_a_synthetic_server_opens_synthetic_programmes_and_says_so(self):
        rig = Rig()
        self.assertEqual(rig.ok("open_programme", scenario("lantern-room"))["data"], "synthetic")


class TheStateTheBannerReadsFrom(unittest.TestCase):
    def test_the_state_carries_the_mode_and_each_programmes_provider(self):
        with Stub(serve_fake) as stub, live_site(stub, daily_live_calls=5) as site:
            person = Browser(site).open()
            shown = person.state()
            self.assertEqual((shown["server"]["provider"], shown["server"]["mode"], shown["server"]["key"]),
                             ("live", "live", "set"))
            pid = person.ok("open_programme", shown["sample"])["programme_id"]
            self.assertEqual(person.programme()["provider"], "live")
            for anchor in person.programme()["anchors"]:
                person.ok("find_anchor", {"programme_id": pid, "name": anchor["name"]})
            self.assertEqual(person.state()["server"]["mode"], "live")
            person.ok("find_tag", {"programme_id": pid, "query": "stand-up"})
            self.assertEqual(person.state()["server"]["mode"], "spent")
            second = person.ok("open_programme", scenario("campus-board"))
            self.assertEqual(second["data"], "synthetic")
            programmes = person.state()["workspace"]["programmes"]
            self.assertEqual([p["provider"] for p in programmes], ["live", "fake"])
            self.assertEqual(person.state()["allowances"]["daily"]["used"], 5)

    def test_a_synthetic_server_says_synthetic_and_whether_a_key_is_set(self):
        with Site() as site:
            shown = Browser(site).open().state()
            self.assertEqual((shown["server"]["mode"], shown["server"]["key"]), ("synthetic", "missing"))
        with Site(key=fake_key()) as site:
            self.assertEqual(Browser(site).open().state()["server"]["key"], "set")


if __name__ == "__main__":
    unittest.main()
