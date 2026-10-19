import threading
import time
import unittest

from open_slot import invoke, providers, qloo, store
from tests.rig import Rig, scenario, serve_fake
from tests.support import Stub
from tests.webrig import Browser, Site


def workspaces_on(rig, count):
    return [rig.workspace] + [rig.runtime.new_workspace() for _ in range(count - 1)]


def ready(rig, workspace, args=None):
    """A programme in `workspace` with every act found and confirmed by a person."""
    def call(tool, tool_args, actor="agent"):
        return invoke.invoke(rig.runtime, workspace, tool, tool_args, actor)

    opened = call("open_programme", args or scenario("lantern-room"))["result"]
    pid = opened["programme_id"]
    for name in opened["anchors"]:
        found = call("find_anchor", {"programme_id": pid, "name": name}, "person")["result"]
        call("confirm_anchor", {"programme_id": pid, "name": name,
                                "entity_id": found["candidates"][0]["entityId"]}, "person")
    return pid


def together(jobs):
    """Runs each job on its own thread, all released at once; returns their results in order."""
    results = [None] * len(jobs)
    start = threading.Barrier(len(jobs))

    def run(index, job):
        start.wait(10)
        results[index] = job()

    threads = [threading.Thread(target=run, args=(i, job), daemon=True) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive(), "a job did not finish"
    return results


class OneWorkspacesPlanDoesNotMakeAnotherWait(unittest.TestCase):
    def test_two_plans_reach_qloo_at_the_same_time(self):
        """The stub answers the first two Insights requests only once both have arrived. Were one
        plan to hold a lock the other needs, the second request would never come."""
        meeting = threading.Barrier(2)
        seen = {"arrived": 0, "broken": False}
        guard = threading.Lock()

        def slow(request):
            if request["path"] == qloo.INSIGHTS:
                with guard:
                    seen["arrived"] += 1
                    first_two = seen["arrived"] <= 2
                if first_two:
                    try:
                        meeting.wait(5)
                    except threading.BrokenBarrierError:
                        seen["broken"] = True
            return serve_fake(request)

        with Stub(slow) as stub:
            rig = Rig.live(stub, cache_ttl_s=0)
            first, second = workspaces_on(rig, 2)
            programmes = [ready(rig, first), ready(rig, second)]
            results = together([
                lambda: invoke.invoke(rig.runtime, first, "plan_acts", {"programme_id": programmes[0]},
                                      "agent"),
                lambda: invoke.invoke(rig.runtime, second, "plan_acts", {"programme_id": programmes[1]},
                                      "agent"),
            ])
        self.assertFalse(seen["broken"], "the second plan was kept waiting by the first")
        for envelope in results:
            self.assertTrue(envelope["ok"], envelope)
            self.assertEqual(envelope["result"]["stopped_because"], "enough_ready")

    def test_another_workspace_opens_reads_and_runs_while_one_plan_is_inside_a_slow_call(self):
        held, release = threading.Event(), threading.Event()

        class Slow(providers.FakeProvider):
            def fetch(self, request, origin=None):
                if request.path == qloo.INSIGHTS and not held.is_set():
                    held.set()
                    release.wait(10)
                return super().fetch(request, origin)

        with Site(engine=Slow()) as site:
            slow, quick = Browser(site).open(), Browser(site).open()
            pid = slow.sample()
            slow.confirm_all(pid)
            running = []
            plan = threading.Thread(target=lambda: running.append(
                slow.invoke("plan_acts", {"programme_id": pid})), daemon=True)
            plan.start()
            self.assertTrue(held.wait(10))
            answered = []
            other = threading.Thread(target=lambda: answered.append((
                quick.state()["workspace"]["programmes"],
                quick.sample(),
                quick.get("/api/calls").status,
            )), daemon=True)
            other.start()
            other.join(5)
            self.assertFalse(other.is_alive(), "the second workspace waited for the first one's plan")
            self.assertEqual(answered[0][0], [])
            self.assertEqual(answered[0][2], 200)
            self.assertTrue(plan.is_alive(), "the first plan is still inside its slow call")
            release.set()
            plan.join(10)
            self.assertTrue(running[0]["ok"])

    def test_a_workspaces_own_requests_still_run_one_at_a_time(self):
        inside, overlap = [0], [0]
        guard = threading.Lock()

        class Counting(providers.FakeProvider):
            def fetch(self, request, origin=None):
                with guard:
                    inside[0] += 1
                    overlap[0] = max(overlap[0], inside[0])
                time.sleep(0.02)
                with guard:
                    inside[0] -= 1
                return super().fetch(request, origin)

        rig = Rig(Counting())
        pid = rig.ready()
        together([lambda: rig.call("plan_acts", {"programme_id": pid}) for _ in range(4)])
        self.assertEqual(overlap[0], 1)


class TheLimitsStayExactUnderThreads(unittest.TestCase):
    def test_the_servers_ceiling_is_never_passed_when_two_workspaces_plan_at_once(self):
        ceiling = 15
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, cache_ttl_s=0, daily_live_calls=ceiling)
            first, second = workspaces_on(rig, 2)
            programmes = [ready(rig, first), ready(rig, second)]
            setup = len(stub.requests)
            results = together([
                lambda: invoke.invoke(rig.runtime, first, "plan_acts", {"programme_id": programmes[0]},
                                      "person"),
                lambda: invoke.invoke(rig.runtime, second, "plan_acts", {"programme_id": programmes[1]},
                                      "person"),
            ])
        self.assertEqual(setup, 8)
        self.assertEqual(len(stub.requests), ceiling)
        self.assertEqual(rig.ledger.daily_used(), ceiling)
        spent = [store.programme(rig.runtime.doc, workspace, pid)["budget"]["spent"]
                 for workspace, pid in zip((first, second), programmes)]
        self.assertEqual(sum(spent), ceiling)
        self.assertIn("ceiling", [envelope["result"]["stopped_because"] for envelope in results])

    def test_the_agents_allowance_is_counted_for_each_workspace_on_its_own(self):
        allowance = 6
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, cache_ttl_s=0, agent_calls_per_day=allowance)
            first, second = workspaces_on(rig, 2)
            programmes = [ready(rig, first), ready(rig, second)]
            results = together([
                lambda: invoke.invoke(rig.runtime, first, "plan_acts", {"programme_id": programmes[0]},
                                      "agent"),
                lambda: invoke.invoke(rig.runtime, second, "plan_acts", {"programme_id": programmes[1]},
                                      "agent"),
            ])
        for workspace, envelope in zip((first, second), results):
            self.assertEqual(rig.ledger.agent_used(workspace), allowance)
            self.assertEqual(envelope["result"]["stopped_because"], "agent_allowance")
            self.assertEqual(envelope["result"]["calls"]["spent_by_this_plan"], allowance)

    def test_a_programmes_cap_holds_when_one_workspace_asks_from_several_threads(self):
        cap = 7
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub, cache_ttl_s=0, max_calls=cap)
            pid = rig.ready()
            together([lambda: rig.call("plan_acts", {"programme_id": pid}, "person") for _ in range(4)])
            spent = rig.stored(pid)["budget"]["spent"]
        self.assertEqual(spent, cap)
        self.assertEqual(len(stub.requests), cap)
        self.assertEqual(rig.ledger.daily_used(), cap)

    def test_many_threads_asking_the_ledger_at_once_are_admitted_exactly_up_to_the_limit(self):
        ledger = providers.Ledger(daily_limit=25, agent_limit=10, cache_ttl=0)
        admitted = []

        def ask(owner):
            def job():
                count = 0
                for _ in range(10):
                    try:
                        ledger.admit(owner, "agent")
                        count += 1
                    except store.Refusal:
                        pass
                return count
            return job

        admitted = together([ask(f"ws_{n % 4}") for n in range(8)])
        self.assertEqual(sum(admitted), 25)
        self.assertEqual(ledger.daily_used(), 25)
        self.assertTrue(all(ledger.agent_used(f"ws_{n}") <= 10 for n in range(4)))


class TheWorkspaceLocksAreForgottenWithTheirWorkspaces(unittest.TestCase):
    def test_a_pruned_workspace_loses_its_lock_and_a_live_one_keeps_it(self):
        rig = Rig()
        kept = rig.runtime.workspace_lock(rig.workspace)
        rig.runtime.workspace_lock(rig.runtime.new_workspace())
        self.assertEqual(len(rig.runtime._locks), 2)
        rig.runtime.prune(ttl_s=3600)
        self.assertEqual(len(rig.runtime._locks), 2)
        rig.runtime.prune(ttl_s=0)
        self.assertEqual(rig.runtime._locks, {})
        self.assertIsNot(rig.runtime.workspace_lock(rig.workspace), kept)

    def test_one_lock_per_workspace_and_the_same_one_each_time(self):
        rig = Rig()
        other = rig.runtime.new_workspace()
        self.assertIs(rig.runtime.workspace_lock(rig.workspace), rig.runtime.workspace_lock(rig.workspace))
        self.assertIsNot(rig.runtime.workspace_lock(rig.workspace), rig.runtime.workspace_lock(other))


if __name__ == "__main__":
    unittest.main()
