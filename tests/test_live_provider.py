import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from open_slot import config, providers, qloo
from open_slot.store import Refusal
from tests.support import Stub, fake_key

KIDS = "00000000-0000-4000-8000-00000000a001"
BAND = "00000000-0000-4000-8000-00000000a002"
FAKE = providers.FakeProvider()
NOTHING = {"results": {"entities": []}}
INSIGHTS = qloo.insights(qloo.ARTIST, [KIDS, BAND], take=5)
OTHER = qloo.insights(qloo.PODCAST, [KIDS, BAND], take=5)
FIRST = providers.Origin("ws_1", "agent", 16)
SECOND = providers.Origin("ws_2", "agent", 16)
PERSON = providers.Origin("ws_1", "person", 16)


def sequence(*statuses):
    queue = list(statuses)

    def answer(request):
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        return status, NOTHING if status == 200 else {"message": "not this time"}, {}

    return answer


def build(stub, daily=600, agent=48, ttl=86400, clock=time.time, directory=None):
    ledger = providers.Ledger(daily, agent, ttl, clock=clock, directory=directory)
    sleeps = []
    return providers.LiveProvider(fake_key(), ledger, stub.base_url, sleep=sleeps.append), ledger, sleeps


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class TheFakeCostsOneAndSaysSo(unittest.TestCase):
    def test_a_fake_answer_is_labelled_fake_and_costs_one_call_of_the_programmes_cap(self):
        reply = FAKE.fetch(qloo.search("Alvvays"), providers.Origin("ws_1", "agent", 16))
        self.assertEqual((reply.cache, reply.cost, reply.error, reply.status), ("fake", 1, None, 200))


class WithNoKey(unittest.TestCase):
    def test_every_call_is_refused_no_key_and_nothing_falls_back_to_the_fake(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            ledger = providers.Ledger(600, 48, 86400)
            provider = providers.LiveProvider(None, ledger, stub.base_url)
            with self.assertRaises(Refusal) as caught:
                provider.fetch(INSIGHTS, FIRST)
        self.assertEqual(caught.exception.code, "no_key")
        self.assertEqual(stub.requests, [])
        self.assertEqual(ledger.daily_used(), 0)

    def test_build_with_no_key_still_builds_a_live_provider_that_refuses(self):
        settings = config.load(environ={}, cwd="/nonexistent")
        provider, _ = providers.build(settings, "live")
        self.assertEqual(provider.name, "live")
        with self.assertRaises(Refusal) as caught:
            provider.fetch(INSIGHTS, FIRST)
        self.assertEqual(caught.exception.code, "no_key")

    def test_build_picks_the_fake_by_the_settings_or_by_name(self):
        settings = config.load(environ={}, cwd="/nonexistent")
        self.assertEqual(providers.build(settings)[0].name, "fake")
        keyed = config.load(environ={"QLOO_API_KEY": fake_key()}, cwd="/nonexistent")
        self.assertEqual(providers.build(keyed)[0].name, "live")
        self.assertEqual(providers.build(keyed, "fake")[0].name, "fake")


class OneAttempt(unittest.TestCase):
    def test_a_miss_sends_one_get_with_the_key_in_the_header_only(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub)
            reply = provider.fetch(INSIGHTS, FIRST)
        (seen,) = stub.requests
        self.assertEqual(seen["headers"]["x-api-key"], fake_key())
        self.assertNotIn(fake_key(), seen["raw_query"])
        self.assertEqual((reply.status, reply.cache, reply.cost, reply.error), (200, "miss", 1, None))
        self.assertEqual(reply.body, NOTHING)
        self.assertEqual(ledger.daily_used(), 1)

    def test_neither_the_provider_nor_the_ledger_shows_the_key(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub)
            provider.fetch(INSIGHTS, FIRST)
        for shown in (repr(provider), str(provider), json.dumps(ledger.snapshot("ws_1")), repr(INSIGHTS)):
            self.assertNotIn(fake_key(), shown)
        self.assertEqual(repr(provider), "LiveProvider(key set)")

    def test_a_connection_failure_comes_back_as_a_fixed_kind_not_the_librarys_text(self):
        with Stub(lambda r: (200, {}, {})) as stub:
            dead = stub.base_url
        ledger = providers.Ledger(600, 48, 86400)
        provider = providers.LiveProvider(fake_key(), ledger, dead, timeout=2, sleep=lambda s: None)
        reply = provider.fetch(INSIGHTS, FIRST)
        self.assertEqual((reply.status, reply.body, reply.error, reply.cost), (0, None, "connection", 1))
        self.assertEqual(ledger.daily_used(), 1)

    def test_a_redirect_is_not_followed_and_the_key_is_not_replayed(self):
        with Stub(lambda r: (200, {}, {})) as elsewhere:
            with Stub(lambda r: (302, b"", {"Location": elsewhere.base_url + "/stolen"})) as stub:
                provider, _, sleeps = build(stub)
                reply = provider.fetch(INSIGHTS, FIRST)
        self.assertEqual((reply.status, reply.cost), (302, 1))
        self.assertEqual(elsewhere.requests, [])
        self.assertEqual(sleeps, [])


class TheCache(unittest.TestCase):
    def test_the_same_request_a_second_time_is_a_free_hit_with_no_second_call(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub)
            provider.fetch(INSIGHTS, FIRST)
            again = provider.fetch(INSIGHTS, SECOND)
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual((again.cache, again.cost, again.ms, again.body), ("hit", 0, 0, NOTHING))
        self.assertEqual((ledger.hits, ledger.daily_used()), (1, 1))

    def test_a_hit_costs_the_agent_nothing_even_with_its_allowance_spent(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub, agent=1)
            provider.fetch(INSIGHTS, FIRST)
            self.assertEqual(provider.fetch(INSIGHTS, FIRST).cache, "hit")

    def test_a_different_request_is_a_miss(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, _, _ = build(stub)
            provider.fetch(INSIGHTS, FIRST)
            self.assertEqual(provider.fetch(OTHER, FIRST).cache, "miss")
        self.assertEqual(len(stub.requests), 2)

    def test_the_cache_key_holds_the_path_and_the_parameters_and_never_the_key(self):
        self.assertTrue(INSIGHTS.cache_key().startswith("/v2/insights?"))
        self.assertNotIn(fake_key(), INSIGHTS.cache_key())
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub)
            provider.fetch(INSIGHTS, FIRST)
        self.assertNotIn(fake_key(), repr(ledger._cache))

    def test_an_empty_search_is_cached_but_an_error_is_not(self):
        with Stub(lambda r: (404, {"message": "No results"}, {})) as stub:
            provider, _, _ = build(stub)
            for _ in range(2):
                provider.fetch(qloo.search("zzz"), FIRST)
        self.assertEqual(len(stub.requests), 1)
        with Stub(lambda r: (401, {"message": "no"}, {})) as stub:
            provider, _, _ = build(stub)
            for _ in range(2):
                provider.fetch(INSIGHTS, FIRST)
        self.assertEqual(len(stub.requests), 2)

    def test_an_entry_expires_after_the_ttl(self):
        clock = Clock()
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, _, _ = build(stub, ttl=100, clock=clock)
            provider.fetch(INSIGHTS, FIRST)
            clock.now += 99
            self.assertEqual(provider.fetch(INSIGHTS, FIRST).cache, "hit")
            clock.now += 2
            self.assertEqual(provider.fetch(INSIGHTS, FIRST).cache, "miss")

    def test_a_zero_ttl_turns_the_cache_off(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, _, _ = build(stub, ttl=0)
            provider.fetch(INSIGHTS, FIRST)
            provider.fetch(INSIGHTS, FIRST)
        self.assertEqual(len(stub.requests), 2)

    def test_the_cache_is_bounded(self):
        ledger = providers.Ledger(600, 48, 86400)
        for number in range(providers.CACHE_ENTRIES_MAX + 20):
            ledger.remember(f"key{number}", 200, {})
        self.assertEqual(ledger.snapshot()["cache"]["entries"], providers.CACHE_ENTRIES_MAX)


class BoundedRetry(unittest.TestCase):
    def test_two_429s_then_success_is_three_attempts_each_counted_after_one_and_three_seconds(self):
        with Stub(sequence(429, 429, 200)) as stub:
            provider, ledger, sleeps = build(stub)
            reply = provider.fetch(INSIGHTS, FIRST)
        self.assertEqual((reply.status, reply.cost), (200, 3))
        self.assertEqual(len(stub.requests), 3)
        self.assertEqual(sleeps, [1.0, 3.0])
        self.assertEqual(ledger.daily_used(), 3)
        self.assertEqual(ledger.agent_used("ws_1"), 3)

    def test_a_5xx_that_never_clears_stops_after_two_retries_and_is_not_cached(self):
        with Stub(sequence(503)) as stub:
            provider, ledger, sleeps = build(stub)
            reply = provider.fetch(INSIGHTS, FIRST)
            self.assertEqual(provider.fetch(INSIGHTS, FIRST).cost, 3)
        self.assertEqual((reply.status, reply.cost, reply.cache), (503, 3, "miss"))
        self.assertEqual(len(stub.requests), 6)
        self.assertEqual(sleeps, [1.0, 3.0, 1.0, 3.0])
        self.assertEqual(ledger.daily_used(), 6)

    def test_a_4xx_other_than_429_is_not_retried(self):
        for status in (400, 401, 403, 404):
            with Stub(sequence(status)) as stub:
                provider, _, sleeps = build(stub)
                reply = provider.fetch(INSIGHTS, FIRST)
            self.assertEqual((reply.status, reply.cost, len(stub.requests), sleeps), (status, 1, 1, []))

    def test_a_retry_never_passes_what_the_programmes_cap_leaves(self):
        with Stub(sequence(429)) as stub:
            provider, ledger, sleeps = build(stub)
            reply = provider.fetch(INSIGHTS, providers.Origin("ws_1", "agent", 2))
        self.assertEqual((reply.cost, len(stub.requests), sleeps), (2, 2, [1.0]))
        with Stub(sequence(429)) as stub:
            provider, ledger, sleeps = build(stub)
            reply = provider.fetch(INSIGHTS, providers.Origin("ws_1", "agent", 1))
        self.assertEqual((reply.cost, len(stub.requests), sleeps), (1, 1, []))

    def test_a_transport_failure_is_not_retried(self):
        with Stub(lambda r: (200, {}, {})) as stub:
            dead = stub.base_url
        sleeps = []
        provider = providers.LiveProvider(fake_key(), providers.Ledger(600, 48, 86400), dead, timeout=2,
                                          sleep=sleeps.append)
        self.assertEqual(provider.fetch(INSIGHTS, FIRST).cost, 1)
        self.assertEqual(sleeps, [])


class TheThreeLimits(unittest.TestCase):
    def test_the_agents_allowance_refuses_its_next_live_call_and_sends_nothing(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub, agent=2)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=1), FIRST)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=2), FIRST)
            with self.assertRaises(Refusal) as caught:
                provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=3), FIRST)
        self.assertEqual(caught.exception.code, "agent_allowance_spent")
        self.assertEqual(len(stub.requests), 2)
        self.assertEqual((ledger.agent_used("ws_1"), ledger.agent_left("ws_1")), (2, 0))

    def test_the_allowance_counts_by_who_started_the_action_and_per_workspace(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub, agent=1)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=1), FIRST)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=2), PERSON)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=3), SECOND)
            with self.assertRaises(Refusal):
                provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=4), FIRST)
        self.assertEqual(ledger.agent_used("ws_1"), 1)
        self.assertEqual(ledger.agent_used("ws_2"), 1)
        self.assertEqual(ledger.daily_used(), 3)

    def test_the_allowance_is_a_rolling_24_hours(self):
        clock = Clock()
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub, agent=1, clock=clock)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=1), FIRST)
            clock.now += providers.DAY_S - 1
            self.assertEqual(ledger.agent_left("ws_1"), 0)
            clock.now += 2
            self.assertEqual(ledger.agent_left("ws_1"), 1)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=2), FIRST)

    def test_the_ceiling_refuses_anyone_including_a_person(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub, daily=2)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=1), PERSON)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=2), FIRST)
            for who in (PERSON, FIRST, SECOND):
                with self.assertRaises(Refusal) as caught:
                    provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=3), who)
                self.assertEqual(caught.exception.code, "daily_allowance_spent")
        self.assertEqual(len(stub.requests), 2)
        self.assertEqual(ledger.daily_left(), 0)

    def test_the_agents_allowance_is_checked_before_the_ceiling(self):
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, _, _ = build(stub, daily=1, agent=1)
            provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=1), FIRST)
            with self.assertRaises(Refusal) as caught:
                provider.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND], take=2), FIRST)
        self.assertEqual(caught.exception.code, "agent_allowance_spent")

    def test_the_ceiling_is_per_utc_day(self):
        clock = Clock(1_800_000_000.0)
        day = providers.Ledger._day(clock.now)
        with Stub(lambda r: (200, NOTHING, {})) as stub:
            provider, ledger, _ = build(stub, daily=1, clock=clock)
            provider.fetch(INSIGHTS, PERSON)
            self.assertEqual(ledger.snapshot()["daily"], {"limit": 1, "used": 1, "day": day.isoformat()})
            clock.now += 2 * providers.DAY_S
            self.assertEqual(ledger.daily_left(), 1)
            provider.fetch(OTHER, PERSON)

    def test_an_allowance_reached_in_the_middle_of_a_retry_ends_the_retries_without_raising(self):
        with Stub(sequence(429)) as stub:
            provider, ledger, sleeps = build(stub, agent=1)
            reply = provider.fetch(INSIGHTS, FIRST)
        self.assertEqual((reply.status, reply.cost, len(stub.requests)), (429, 1, 1))

    def test_a_ceiling_reached_in_the_middle_of_a_retry_is_not_passed_either(self):
        with Stub(sequence(500)) as stub:
            provider, ledger, _ = build(stub, daily=2)
            reply = provider.fetch(INSIGHTS, FIRST)
        self.assertEqual((reply.cost, len(stub.requests), ledger.daily_left()), (2, 2, 0))


class TheOptionalFiles(unittest.TestCase):
    def test_a_ledger_with_a_directory_writes_nothing_until_a_call_is_made(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / ".cache"
            providers.Ledger(600, 48, 86400, directory=folder)
            self.assertFalse(folder.exists())

    def test_the_files_hold_attempt_times_and_responses_only_and_are_private(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / ".cache"
            with Stub(lambda r: (200, NOTHING, {})) as stub:
                provider, ledger, _ = build(stub, directory=folder)
                provider.fetch(INSIGHTS, FIRST)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["cache.json", "ledger.json"])
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
            text = ""
            for path in folder.iterdir():
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                text += path.read_text(encoding="utf-8")
            for secret in (fake_key(), "ws_1", FIRST.workspace, "agent"):
                self.assertNotIn(secret, text)
            self.assertNotIn("api.qloo.com", text)

    def test_a_new_ledger_on_the_same_directory_remembers_the_day_and_the_cache(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / ".cache"
            with Stub(lambda r: (200, NOTHING, {})) as stub:
                provider, _, _ = build(stub, directory=folder)
                provider.fetch(INSIGHTS, FIRST)
                again, ledger, _ = build(stub, daily=1, directory=folder)
                self.assertEqual(ledger.daily_used(), 1)
                self.assertEqual(again.fetch(INSIGHTS, SECOND).cache, "hit")
                with self.assertRaises(Refusal) as caught:
                    again.fetch(OTHER, SECOND)
            self.assertEqual(caught.exception.code, "daily_allowance_spent")
            self.assertEqual(len(stub.requests), 1)
            self.assertEqual(ledger.agent_used("ws_1"), 0)

    def test_expired_entries_are_not_loaded(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / ".cache"
            with Stub(lambda r: (200, NOTHING, {})) as stub:
                build(stub, ttl=100, clock=clock, directory=folder)[0].fetch(INSIGHTS, FIRST)
                clock.now += providers.DAY_S + 1
                _, ledger, _ = build(stub, ttl=100, clock=clock, directory=folder)
        self.assertEqual((ledger.daily_used(), ledger.snapshot()["cache"]["entries"]), (0, 0))

    def test_a_damaged_file_is_read_as_empty(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent)
            (folder / "ledger.json").write_text("not json", encoding="utf-8")
            (folder / "cache.json").write_text("[1, 2", encoding="utf-8")
            ledger = providers.Ledger(600, 48, 86400, directory=folder)
        self.assertEqual((ledger.daily_used(), ledger.snapshot()["cache"]["entries"]), (0, 0))


class TheWorkspaceLabel(unittest.TestCase):
    def test_a_label_is_stable_short_and_does_not_contain_the_id(self):
        label = providers.workspace_label("ws_secret_cookie_value")
        self.assertEqual(label, providers.workspace_label("ws_secret_cookie_value"))
        self.assertNotEqual(label, providers.workspace_label("ws_another"))
        self.assertEqual(len(label), 10)
        self.assertNotIn("secret", label)


if __name__ == "__main__":
    unittest.main()
