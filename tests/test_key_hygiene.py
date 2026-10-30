import copy
import json
import re
import secrets
import unittest
import urllib.error
from unittest import mock

from open_slot import config, probe, qloo, store, transport
from tests.rig import Rig, scenario, serve_fake
from tests.support import ROOT, Stub, fake_key
from tests.test_no_qloo_data import text_of, tracked_files

PACKAGE = sorted((ROOT / "open_slot").glob("*.py"))
REQUEST = qloo.insights(qloo.ARTIST, ["00000000-0000-4000-8000-000000000101"])


def source(path):
    return path.read_text(encoding="utf-8")


class OneModuleHandlesTheKey(unittest.TestCase):
    def test_only_transport_touches_the_network(self):
        for path in PACKAGE:
            if path.name == "transport.py":
                continue
            text = source(path)
            for word in ("urllib.request", "http.client", "import socket", "requests"):
                self.assertNotIn(word, text, f"{path.name} reaches for the network")

    def test_only_transport_names_the_key_header(self):
        for path in PACKAGE:
            if path.name != "transport.py":
                self.assertNotIn("X-Api-Key", source(path), path.name)
                self.assertNotIn("x-api-key", source(path).lower(), path.name)

    def test_only_config_reads_the_environment(self):
        for path in PACKAGE:
            if path.name != "config.py":
                text = source(path)
                for word in ("os.environ", "getenv", "environ["):
                    self.assertNotIn(word, text, f"{path.name} reads the environment")

    def test_the_key_is_never_a_default_argument_or_a_module_constant(self):
        for path in PACKAGE:
            self.assertFalse(re.search(r"^\s*(KEY|API_KEY|QLOO_KEY)\s*=", source(path), re.M), path.name)


class NeverInAnError(unittest.TestCase):
    def test_a_connection_error_whose_text_carries_the_key_becomes_a_fixed_message(self):
        key = fake_key()
        for error in (OSError(f"connection reset {key}"), urllib.error.URLError(f"unreachable {key}"),
                      ValueError(f"Invalid header value {key!r}"), RuntimeError(key)):
            with mock.patch.object(transport._opener, "open", side_effect=error):
                with self.assertRaises(transport.TransportError) as caught:
                    transport.send("https://hackathon.api.qloo.com", REQUEST, key)
            raised = caught.exception
            self.assertNotIn(key, str(raised))
            self.assertNotIn(key, repr(raised))
            self.assertIn(str(raised), transport.FIXED_MESSAGES.values())
            self.assertTrue(raised.__suppress_context__)
            self.assertIsNone(raised.__cause__)

    def test_a_timeout_whose_text_carries_the_key_becomes_a_fixed_message(self):
        key = fake_key()
        with mock.patch.object(transport._opener, "open", side_effect=TimeoutError(key)):
            with self.assertRaises(transport.TransportError) as caught:
                transport.send("https://hackathon.api.qloo.com", REQUEST, key)
        self.assertEqual(str(caught.exception), transport.FIXED_MESSAGES["timeout"])

    def test_a_401_that_quotes_the_key_maps_to_a_message_that_does_not(self):
        key = fake_key()
        with Stub(lambda r: (401, {"message": f"bad key {key}"}, {})) as stub:
            reply = transport.send(stub.base_url, REQUEST, key)
        outcome = qloo.outcome(reply.status, qloo.INSIGHTS)
        self.assertEqual(outcome, "qloo_auth")
        self.assertNotIn(key, qloo.OUTCOME_MESSAGES[outcome])

    def test_no_fixed_message_names_a_url_or_an_address(self):
        for message in list(transport.FIXED_MESSAGES.values()) + list(qloo.OUTCOME_MESSAGES.values()):
            self.assertNotIn("http", message)
            self.assertNotRegex(message, r"\d+\.\d+\.\d+\.\d+")

    def test_a_request_never_holds_the_key(self):
        key = fake_key()
        self.assertNotIn(key, REQUEST.url("https://hackathon.api.qloo.com"))
        self.assertNotIn(key, REQUEST.cache_key())
        self.assertNotIn(key, repr(REQUEST))

    def test_a_config_error_and_the_config_repr_never_show_the_key(self):
        key = fake_key()
        settings = config.load(environ={"QLOO_API_KEY": key}, cwd="/nonexistent")
        self.assertNotIn(key, repr(settings))
        self.assertNotIn(key, str(settings.allowed_hosts))
        self.assertEqual(settings.key_status, "set")


class TheProbeKeepsItOut(unittest.TestCase):
    def test_its_output_never_shows_the_key_even_when_the_server_echoes_it(self):
        key = fake_key()

        def echo(request):
            return 200, {"results": {"entities": [], "echo": request["headers"]["x-api-key"]},
                         request["headers"]["x-api-key"]: 1}, {}

        with Stub(echo) as stub:
            report = probe.run(key, ["a", "b", "c"], base_url=stub.base_url)
        for text in (probe.format_report(report), probe.render_notes(report)):
            self.assertNotIn(key, text)


def hostile(marker, status=None, where=lambda request: True):
    """Serves the synthetic fixtures, but echoes the key header and a marker into every place a
    reader does not look, and answers `status` with a body that quotes both."""

    def serve(request):
        key = request["headers"]["x-api-key"]
        if status is not None and where(request):
            return status, {"message": f"bad key {key} {marker}", "detail": {"echo": key, marker: 1}}, {
                "X-Debug": key}
        code, body, headers = serve_fake(request)
        if code == 200:
            body = copy.deepcopy(body)
            results = body["results"]
            rows = results if isinstance(results, list) else next(iter(results.values()))
            for row in rows:
                row["note"] = f"{marker} {key}"
            body["echo"] = key
        return code, body, headers

    return serve


class ThroughTheWholeApp(unittest.TestCase):
    """The key, a workspace's id and its agent token, and what Qloo said, through every path."""

    def setUp(self):
        self.key = fake_key()
        self.marker = "zq-" + secrets.token_hex(6)

    def drive(self, rig, actor="agent"):
        envelopes = [rig.call("open_programme", scenario("lantern-room"), actor)]
        pid = envelopes[0]["result"]["programme_id"]
        for name in scenario("lantern-room")["anchors"]:
            envelopes.append(rig.call("find_anchor", {"programme_id": pid, "name": name}, actor))
        for anchor in rig.stored(pid)["anchors"]:
            if anchor["candidates"]:
                envelopes.append(rig.call("confirm_anchor", {
                    "programme_id": pid, "name": anchor["name"],
                    "entity_id": anchor["candidates"][0]["entityId"]}, "person"))
        envelopes.append(rig.call("plan_acts", {"programme_id": pid}, actor))
        envelopes.append(rig.call("qloo_budget", {"programme_id": pid}, actor))
        envelopes.append(rig.call("get_programme", {"programme_id": pid}, actor))
        return envelopes

    def everything(self, rig, envelopes):
        return "\n".join([
            json.dumps(rig.runtime.doc), json.dumps(envelopes), repr(rig.runtime.provider),
            repr(rig.runtime), repr(rig.settings), json.dumps(rig.ledger.snapshot(rig.workspace)),
            repr(list(rig.ledger._cache)), repr(rig.ledger._attempts),
        ])

    def test_a_successful_live_run_with_an_echoing_server_keeps_the_key_and_the_unread_fields_out(self):
        with Stub(hostile(self.marker)) as stub:
            rig = Rig.live(stub)
            envelopes = self.drive(rig)
        self.assertTrue(envelopes[-3]["ok"])
        text = self.everything(rig, envelopes)
        self.assertNotIn(self.key, text)
        self.assertNotIn(self.marker, text)
        self.assertNotIn(stub.base_url, text)

    def test_every_error_status_whose_body_quotes_the_key_is_mapped_to_a_fixed_message(self):
        for status in (400, 401, 403, 404, 429, 500, 503):
            with Stub(hostile(self.marker, status)) as stub:
                rig = Rig.live(stub)
                envelopes = self.drive(rig)
            text = self.everything(rig, envelopes)
            self.assertNotIn(self.key, text, status)
            self.assertNotIn(self.marker, text, status)
            self.assertNotIn(stub.base_url, text, status)
            answered = [e for e in envelopes if e["outcome"].startswith("qloo_")]
            self.assertTrue(answered, status)
            for envelope in answered:
                if not envelope["ok"]:
                    self.assertEqual(envelope["error"]["message"], qloo.OUTCOME_MESSAGES[envelope["outcome"]])

    def test_a_connection_failure_that_names_the_key_and_the_address_leaves_neither(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub)
            dead = stub.base_url
        rig.runtime.provider.base_url = dead
        with mock.patch.object(transport._opener, "open",
                               side_effect=OSError(f"connect to {dead} with {self.key} failed")):
            envelopes = self.drive(rig)
        text = self.everything(rig, envelopes)
        self.assertNotIn(self.key, text)
        self.assertNotIn(dead, text)
        self.assertNotIn(dead.rsplit(":", 1)[1], text.replace('"ms"', ""))
        self.assertEqual(envelopes[1]["error"]["message"], qloo.OUTCOME_MESSAGES["qloo_unavailable"])

    def test_an_answer_the_reader_does_not_know_is_named_by_its_path_only(self):
        def serve(request):
            return 200, {"results": {"surprise": [self.key, self.marker]}, "echo": self.key}, {}

        with Stub(serve) as stub:
            rig = Rig.live(stub)
            pid = rig.open(scenario("lantern-room"))
            envelope = rig.call("find_anchor", {"programme_id": pid, "name": "Alvvays"})
        self.assertEqual(envelope["outcome"], "qloo_unknown_shape")
        text = self.everything(rig, [envelope])
        self.assertNotIn(self.key, text)
        self.assertNotIn(self.marker, text)

    def test_the_workspace_id_and_the_agent_token_reach_no_log_no_trace_and_no_envelope(self):
        with Stub(hostile(self.marker)) as stub:
            rig = Rig.live(stub)
            envelopes = self.drive(rig)
        secret_token = store.workspace(rig.runtime.doc, rig.workspace)["agentToken"]
        found = rig.runtime.doc["workspaces"][0]
        shown = json.dumps([rig.runtime.doc["calls"], found["activityLog"],
                            [p["trace"] for p in found["programmes"]], envelopes])
        for secret in (rig.workspace, secret_token, rig.workspace.removeprefix("ws_")):
            self.assertNotIn(secret, shown)
        self.assertTrue(all(len(call["workspace"]) == 10 for call in rig.runtime.doc["calls"]))

    def test_a_call_log_entry_holds_the_documented_fields_and_never_a_name_or_a_query_value(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub)
            rig.planned()
        entry = rig.runtime.doc["calls"][0]
        self.assertEqual(set(entry), {"ts", "provider", "path", "cache", "ms", "status", "attempts",
                                      "startedBy", "workspace"})
        text = json.dumps(rig.runtime.doc["calls"])
        for name in scenario("lantern-room")["anchors"] + ["Hollowmere", "urn:entity", "urn:tag"]:
            self.assertNotIn(name, text)

    def test_the_stub_saw_the_key_only_in_the_header_on_every_call_of_a_whole_plan(self):
        with Stub(serve_fake) as stub:
            rig = Rig.live(stub)
            rig.planned()
        self.assertEqual(len(stub.requests), 12)
        for request in stub.requests:
            self.assertEqual(request["headers"]["x-api-key"], self.key)
            self.assertNotIn(self.key, request["raw_query"])
            self.assertNotIn(self.key, request["path"])
            self.assertEqual(request["method"], "GET")
            self.assertTrue(set(request["query"]) <= (qloo.ALLOWED[request["path"]].get(
                request["query"].get("filter.type")) or qloo.ALLOWED[request["path"]][None]))

    def test_running_the_app_leaves_no_key_and_no_qloo_answer_in_the_tree(self):
        with Stub(hostile(self.marker)) as stub:
            rig = Rig.live(stub)
            self.drive(rig)
        for path in tracked_files():
            if path.name == "verify.sh":
                continue
            text = text_of(path)
            self.assertNotIn(self.key, text, path.relative_to(ROOT))
            self.assertNotIn(self.marker, text, path.relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
