import contextlib
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from open_slot import __main__ as cli
from open_slot import providers, server, transport
from tests.rig import serve_fake
from tests.support import ROOT, Stub, fake_key

LANTERN = str(ROOT / "scenarios" / "lantern-room.json")
CAMPUS = str(ROOT / "scenarios" / "campus-board.json")


class Cli(unittest.TestCase):
    """Runs main() from an empty directory with an environment the test supplies, so no `.env` and no
    real key can be read, and with the cache directory pointed at a temporary one."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.previous = os.getcwd()
        os.chdir(self.folder.name)
        self.addCleanup(os.chdir, self.previous)
        self.cache = Path(self.folder.name) / "cache"
        patch = mock.patch.object(cli, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def run_main(self, *argv, environ=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), environ={} if environ is None else environ)
        return code, out.getvalue(), err.getvalue()


class PlanOnTheFakeProvider(Cli):
    def test_scenario_a_prints_its_trace_and_the_calls_spent_and_not_spent(self):
        code, out, err = self.run_main("plan", LANTERN, "--provider", "fake")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("The Lantern Room (fictional) (synthetic sample data, Qloo is not being called)", out)
        self.assertIn("calls: 12 of 16 spent in all (8 by this plan), 8 not spent; stopped: enough_ready",
                      out)
        self.assertIn("set aside: 26 (12 outside the range, 14 with too few links)", out)
        self.assertIn("stopped: 4 candidates are ready for 3 nights; 8 calls not spent", out)
        self.assertIn("room's range: Qloo popularity 0.40 to 0.76", out)

    def test_the_trace_starts_at_the_four_searches_and_numbers_every_step(self):
        _, out, _ = self.run_main("plan", LANTERN, "--provider", "fake")
        lines = out.split("trace:\n")[1].split("\n\ncalls:")[0].split("\n")
        self.assertEqual([line.split()[0] for line in lines], [str(n) for n in range(1, 16)])
        self.assertEqual(sum("find_anchor (person)" in line for line in lines), 4)
        self.assertEqual(sum("check_trend (agent)" in line for line in lines), 4)
        self.assertTrue(all("[fake]" in line for line in lines if " call " in line))
        self.assertIn("/v2/insights podcast 4 id(s): podcast: one wide call", out)
        self.assertIn("/v2/tags \"stand-up\"", out)

    def test_the_board_lists_the_four_ready_acts_first(self):
        _, out, _ = self.run_main("plan", LANTERN, "--provider", "fake")
        board = out.split("board, best first:\n")[1].split("\nalso connected")[0].split("\n")
        self.assertEqual([line.split()[1] for line in board[:2]], ["Hollowmere", "Odile"])
        self.assertEqual([("ready" in line) for line in board], [True] * 4 + [False] * 4)

    def test_the_second_sample_programme_is_two_nights_one_lens(self):
        code, out, _ = self.run_main("plan", CAMPUS, "--provider", "fake")
        self.assertEqual(code, 0)
        self.assertIn("calls: 7 of 16 spent in all (5 by this plan), 3 not spent", out)
        self.assertIn("stopped: 3 candidates are ready for 2 nights; 3 calls not spent", out)

    def test_the_fake_is_the_default_with_no_key_and_nothing_is_written_to_disk(self):
        code, out, _ = self.run_main("plan", LANTERN)
        self.assertEqual(code, 0)
        self.assertIn("synthetic sample data", out)
        self.assertFalse(self.cache.exists())

    def test_as_the_person_each_top_match_is_confirmed_at_the_terminal_and_said_so(self):
        _, out, _ = self.run_main("plan", LANTERN, "--provider", "fake")
        self.assertEqual(out.count("confirmed by you at this terminal"), 4)
        self.assertIn("acting as the person", out)

    def test_as_the_agent_the_plan_is_refused_because_only_a_person_can_confirm(self):
        code, out, err = self.run_main("plan", LANTERN, "--provider", "fake", "--as", "agent")
        self.assertEqual(code, 1)
        self.assertIn("refused: anchor_unconfirmed", err)
        self.assertIn("only a person can say which match is the act meant", err)
        self.assertNotIn("confirmed by you", out)
        self.assertNotIn("trace:", out)
        self.assertEqual(out.count("candidate(s)"), 4)

    def test_a_scenario_that_is_not_json_or_has_an_unknown_field_is_refused_cleanly(self):
        bad = Path(self.folder.name) / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        code, out, err = self.run_main("plan", str(bad), "--provider", "fake")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cannot be read as JSON", err)
        extra = Path(self.folder.name) / "extra.json"
        extra.write_text(json.dumps({**json.loads(Path(LANTERN).read_text()), "status": "booked"}))
        code, out, err = self.run_main("plan", str(extra), "--provider", "fake")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("refused: smuggled_field", err)
        missing = str(Path(self.folder.name) / "missing.json")
        self.assertEqual(self.run_main("plan", missing, "--provider", "fake")[0], 2)

    def test_the_command_needs_a_subcommand_and_knows_only_two_providers_and_two_actors(self):
        wrong = ([], ["plan"], ["plan", LANTERN, "--provider", "mock"], ["plan", LANTERN, "--as", "root"])
        for argv in wrong:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                cli.main(argv, environ={})
            self.assertEqual(caught.exception.code, 2)


class PlanOnTheLiveProvider(Cli):
    def test_with_no_key_it_refuses_with_key_missing_before_any_call_or_file(self):
        with mock.patch.object(transport, "send", side_effect=AssertionError("network")), \
                mock.patch.object(providers, "send", side_effect=AssertionError("network")):
            code, out, err = self.run_main("plan", LANTERN, "--provider", "live")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("key: missing", err)
        self.assertFalse(self.cache.exists())

    def test_a_configured_live_provider_with_no_key_is_refused_the_same_way(self):
        code, out, err = self.run_main("plan", LANTERN, environ={"OPEN_SLOT_PROVIDER": "live"})
        self.assertEqual((code, out), (2, ""))
        self.assertIn("key: missing", err)

    def test_with_a_key_it_calls_the_stub_caches_privately_and_never_prints_the_key(self):
        key = fake_key()
        with Stub(serve_fake) as stub:
            def to_stub(base_url, request, secret, timeout=15.0):
                return transport.send(stub.base_url, request, secret, timeout)

            with mock.patch.object(providers, "send", to_stub):
                code, out, err = self.run_main("plan", LANTERN, environ={"QLOO_API_KEY": key})
                again = self.run_main("plan", LANTERN, environ={"QLOO_API_KEY": key})
        self.assertEqual(code, 0)
        self.assertIn("live: Qloo is being called", out)
        self.assertIn("calls: 12 of 16 spent in all (8 by this plan)", out)
        self.assertEqual(len(stub.requests), 12)
        self.assertEqual(again[0], 0)
        self.assertIn("calls: 0 of 16 spent in all (0 by this plan)", again[1])
        self.assertIn("[hit]", again[1])
        for shown in (out, err, again[1], again[2]):
            self.assertNotIn(key, shown)
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), ["cache.json", "ledger.json"])
        for path in self.cache.iterdir():
            self.assertNotIn(key, path.read_text(encoding="utf-8"))

    def test_the_budget_afterwards_counts_the_calls_made_from_this_directory(self):
        key = fake_key()
        with Stub(serve_fake) as stub:
            def to_stub(base_url, request, secret, timeout=15.0):
                return transport.send(stub.base_url, request, secret, timeout)

            with mock.patch.object(providers, "send", to_stub):
                self.run_main("plan", LANTERN, environ={"QLOO_API_KEY": key})
        code, out, _ = self.run_main("budget", environ={"QLOO_API_KEY": key})
        self.assertEqual(code, 0)
        self.assertRegex(out, r"server ceiling for \d{4}-\d\d-\d\d \(UTC\): 12 of 600 live calls made from")
        self.assertIn("cache: 12 responses kept for up to 86400 seconds", out)

    def test_a_key_the_cli_cannot_reach_qloo_with_is_a_named_refusal_with_a_fixed_message(self):
        key = fake_key()
        with mock.patch.object(providers, "send", side_effect=transport.TransportError("connection")):
            code, out, err = self.run_main("plan", LANTERN, "--provider", "live",
                                           environ={"QLOO_API_KEY": key})
        self.assertEqual(code, 1)
        self.assertIn("refused: qloo_unavailable: Qloo is not answering.", err)
        self.assertNotIn(key, out + err)


class Budget(Cli):
    def test_it_prints_the_limits_and_the_key_status_and_never_the_key(self):
        code, out, err = self.run_main("budget")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("key: missing", out)
        self.assertIn("provider: fake (synthetic sample data, Qloo is not being called)", out)
        self.assertIn("per-programme cap: 16 live calls; only a person can raise it, to 40", out)
        self.assertIn("agent allowance: 48 live calls in any 24 hours for each workspace", out)
        self.assertRegex(out, r"server ceiling for \d{4}-\d\d-\d\d \(UTC\): 0 of 600 live calls")
        self.assertIn("cache: 0 responses", out)

    def test_a_key_in_the_environment_shows_as_set_and_the_settings_show_as_configured(self):
        key = fake_key()
        code, out, _ = self.run_main("budget", environ={
            "QLOO_API_KEY": key, "OPEN_SLOT_MAX_CALLS": "20", "OPEN_SLOT_DAILY_LIVE_CALLS": "90"})
        self.assertEqual(code, 0)
        self.assertIn("key: set", out)
        self.assertIn("provider: live (live: Qloo is being called)", out)
        self.assertIn("per-programme cap: 20", out)
        self.assertIn("of 90 live calls", out)
        self.assertNotIn(key, out)

    def test_it_creates_nothing_on_disk(self):
        self.run_main("budget")
        self.assertFalse(self.cache.exists())

    def test_a_setting_that_is_wrong_exits_with_status_2_naming_the_setting_and_not_the_key(self):
        key = fake_key()
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            cli.main(["budget"], environ={"QLOO_API_KEY": key, "OPEN_SLOT_MAX_CALLS": "99"})
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("OPEN_SLOT_MAX_CALLS", err.getvalue())
        self.assertNotIn(key, err.getvalue())


class Serve(Cli):
    """`serve` with serve_forever replaced by one request to the real, bound server."""

    def serve(self, *argv, environ=None, bind=None):
        seen = []
        real = server.make_server

        def free_port(app, address, port):
            return real(app, bind or address, 0)

        def one_request(httpd, *args, **kwargs):
            port, answers = httpd.server_address[1], []

            def ask():
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/healthz")
                answers.append(connection.getresponse().status)
                connection.close()

            client = threading.Thread(target=ask)
            client.start()
            httpd.handle_request()
            client.join()
            seen.append((port, answers[0], httpd.app.sample, httpd.app.runtime.provider.name,
                         httpd.app.runtime.fallback))
            raise KeyboardInterrupt

        with mock.patch.object(server, "make_server", free_port), \
                mock.patch.object(server.Server, "serve_forever", autospec=True, side_effect=one_request):
            result = self.run_main("serve", *argv, environ=environ)
        return result, seen

    def test_it_serves_the_sample_on_the_synthetic_provider_and_prints_the_address_to_open(self):
        (code, out, err), seen = self.serve("--provider", "fake")
        self.assertEqual((code, err), (0, ""))
        port, status, sample, provider, _ = seen[0]
        self.assertEqual((status, provider, sample["label"]), (200, "fake", "The Lantern Room (fictional)"))
        self.assertEqual(out.splitlines()[0], "open-slot: synthetic sample data, Qloo is not being called")
        self.assertEqual(out.splitlines()[1],
                         f"open-slot: open http://127.0.0.1:{port}/ (Ctrl-C stops the server)")
        self.assertEqual(out.splitlines()[2], "open-slot: stopped")
        self.assertFalse(self.cache.exists())

    def test_with_no_key_and_nothing_else_it_is_synthetic_and_never_silently_live(self):
        (code, out, _), seen = self.serve()
        self.assertEqual((code, seen[0][3]), (0, "fake"))
        self.assertIn("synthetic sample data", out)

    def test_a_live_server_holds_the_synthetic_provider_as_its_fallback_and_a_synthetic_one_does_not(self):
        key = fake_key()
        (code, _, err), seen = self.serve(environ={"QLOO_API_KEY": key})
        self.assertEqual((code, seen[0][3], seen[0][4].name), (0, "live", "fake"))
        self.assertNotIn(key, err)
        (code, _, _), seen = self.serve("--provider", "fake")
        self.assertEqual((code, seen[0][3], seen[0][4]), (0, "fake", None))

    def test_a_key_in_the_environment_does_not_stop_the_fake_being_asked_for(self):
        key = fake_key()
        (code, out, err), seen = self.serve("--provider", "fake", environ={"QLOO_API_KEY": key})
        self.assertEqual((code, seen[0][3]), (0, "fake"))
        self.assertNotIn(key, out + err)

    def test_live_with_no_key_is_refused_before_anything_is_bound(self):
        with mock.patch.object(server, "make_server", side_effect=AssertionError("bound")):
            for argv, environ in ((("--provider", "live"), {}), ((), {"OPEN_SLOT_PROVIDER": "live"})):
                code, out, err = self.run_main("serve", *argv, environ=environ)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("key: missing", err)

    def test_a_host_bind_names_the_hosts_and_says_tls_is_outside(self):
        environ = {"OPEN_SLOT_BIND": "0.0.0.0", "OPEN_SLOT_ALLOWED_HOSTS": "demo.example", "PORT": "10000"}
        with mock.patch.object(server, "make_server", side_effect=OSError("taken")):
            code, out, err = self.run_main("serve", "--provider", "fake", environ=environ)
        self.assertEqual(code, 2)
        self.assertIn("cannot listen on 0.0.0.0 port 10000", err)
        (code, out, _), seen = self.serve("--provider", "fake", environ=environ, bind="127.0.0.1")
        self.assertEqual(code, 0)
        self.assertIn("listening on 0.0.0.0:", out)
        self.assertIn("for these hosts: demo.example; TLS must be terminated outside", out)

    def test_a_host_bind_with_no_allowed_host_does_not_start_and_names_the_setting(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            cli.main(["serve"], environ={"OPEN_SLOT_BIND": "0.0.0.0"})
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("OPEN_SLOT_ALLOWED_HOSTS", err.getvalue())

    def test_a_port_that_is_taken_is_a_clean_refusal(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            code, out, err = self.run_main("serve", "--provider", "fake",
                                           environ={"OPEN_SLOT_PORT": str(taken.getsockname()[1])})
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cannot listen on 127.0.0.1 port", err)
        self.assertNotIn("Traceback", err)


class TheDefaults(unittest.TestCase):
    def test_the_cache_directory_is_the_entrys_git_ignored_one(self):
        self.assertEqual(cli.CACHE_DIR, ROOT / ".cache")
        self.assertIn(".cache/", (ROOT / ".gitignore").read_text(encoding="utf-8").split())


if __name__ == "__main__":
    unittest.main()
