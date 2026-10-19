import base64
import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest import mock

from open_slot import __main__ as cli
from open_slot import mcp, tools
from open_slot import providers
from tests.rig import Rig, scenario, serve_fake
from tests.support import ROOT, Stub, fake_key
from tests.webrig import HUMAN, Browser, Site

NIL = "0" * 32


def message(method, params=None, id=1):
    body = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        body["id"] = id
    if params is not None:
        body["params"] = params
    return body


def rpc(rig, body, protocol=None):
    status, answer = mcp.handle(rig.runtime, rig.workspace, json.dumps(body).encode("utf-8"), protocol)
    return status, answer


def human_calls(pid, anchor):
    """One well-formed call to each human-only verb, which no tool list offers."""
    return {
        "confirm_anchor": {"programme_id": pid, "name": anchor["name"],
                           "entity_id": anchor["candidates"][0]["entityId"]},
        "assign_slot": {"programme_id": pid, "night_id": "n1", "entity_id": None, "revision": 99},
        "approve_inquiry": {"programme_id": pid, "draft_id": "d1", "sender_name": "Maya", "revision": 99},
        "drop_act": {"programme_id": pid, "entity_id": "x", "reason": "no"},
        "raise_call_cap": {"programme_id": pid, "cap": 30},
    }


def shaped_error(test, answer, code=None, outcome=None, request_id=None):
    test.assertEqual(answer["jsonrpc"], "2.0")
    test.assertEqual(answer["id"], request_id)
    test.assertNotIn("result", answer)
    test.assertIsInstance(answer["error"]["code"], int)
    test.assertIsInstance(answer["error"]["message"], str)
    test.assertTrue(answer["error"]["message"])
    test.assertNotIn("Traceback", json.dumps(answer))
    if code is not None:
        test.assertEqual(answer["error"]["code"], code)
    if outcome is not None:
        test.assertEqual(answer["error"]["data"], {"outcome": outcome})


class Handshake(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def test_initialize_answers_with_the_revision_asked_for_when_it_is_one_it_knows(self):
        for version in mcp.VERSIONS:
            asked = message("initialize", {"protocolVersion": version, "capabilities": {}})
            status, answer = rpc(self.rig, asked)
            self.assertEqual((status, answer["result"]["protocolVersion"]), (200, version))

    def test_a_revision_it_does_not_know_is_answered_with_the_newest_it_does(self):
        for version in ("1999-01-01", "2099-12-31", "latest"):
            _, answer = rpc(self.rig, message("initialize", {"protocolVersion": version}))
            self.assertEqual(answer["result"]["protocolVersion"], mcp.VERSIONS[0])

    def test_initialize_offers_tools_only_and_says_what_it_is_without_naming_a_verb(self):
        _, answer = rpc(self.rig, message("initialize", {"protocolVersion": mcp.VERSIONS[0]}))
        result = answer["result"]
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(result["serverInfo"]["name"], "open-slot")
        self.assertIn("a person", result["instructions"])
        for verb in HUMAN:
            self.assertNotIn(verb, json.dumps(result))

    def test_initialize_needs_a_revision_as_text(self):
        for params in ({}, {"protocolVersion": 20250326}, {"protocolVersion": None}):
            status, answer = rpc(self.rig, message("initialize", params))
            self.assertEqual(status, 200)
            shaped_error(self, answer, mcp.INVALID_PARAMS, request_id=1)

    def test_a_notification_and_a_clients_reply_need_no_answer(self):
        self.assertEqual(rpc(self.rig, message("notifications/initialized", id=None)), (202, None))
        unknown = message("notifications/nothing-known", {"x": 1}, id=None)
        self.assertEqual(rpc(self.rig, unknown), (202, None))
        self.assertEqual(rpc(self.rig, {"jsonrpc": "2.0", "id": 5, "result": {}}), (202, None))
        self.assertEqual(rpc(self.rig, {"jsonrpc": "2.0", "id": "a", "error": {"code": 1, "message": "x"}}),
                         (202, None))

    def test_ping_answers_an_empty_result_and_the_id_comes_back_as_sent(self):
        for request_id in (0, 7, "abc", "7"):
            status, answer = rpc(self.rig, message("ping", id=request_id))
            self.assertEqual((status, answer), (200, {"jsonrpc": "2.0", "id": request_id, "result": {}}))
            self.assertEqual(type(answer["id"]), type(request_id))


class TheToolList(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        _, answer = rpc(self.rig, message("tools/list"))
        self.listed = answer["result"]["tools"]

    def test_it_is_the_registry_with_its_descriptions_schemas_and_annotations(self):
        self.assertEqual(self.listed, json.loads(json.dumps(tools.listing())))
        self.assertEqual([entry["name"] for entry in self.listed], list(tools.REGISTRY))
        for entry in self.listed:
            self.assertGreater(len(entry["description"]), 80, entry["name"])
            self.assertIn("NOT", entry["description"], entry["name"])
            self.assertEqual(entry["inputSchema"]["type"], "object")
            self.assertEqual(sorted(entry["annotations"]),
                             ["destructiveHint", "idempotentHint", "openWorldHint", "readOnlyHint"])

    def test_the_hints_say_which_tools_read_which_reach_qloo_and_that_none_deletes(self):
        hints = {entry["name"]: entry["annotations"] for entry in self.listed}
        self.assertEqual({name for name, hint in hints.items() if hint["readOnlyHint"]},
                         {"explain_act", "get_programme", "qloo_budget"})
        self.assertEqual({name for name, hint in hints.items() if hint["openWorldHint"]},
                         {"find_anchor", "find_tag", "plan_acts", "check_trend"})
        self.assertFalse(any(hint["destructiveHint"] for hint in hints.values()))

    def test_no_verb_is_listed_or_so_much_as_named_anywhere_in_the_answer(self):
        for verb in HUMAN:
            self.assertNotIn(verb, json.dumps(self.listed))

    def test_the_list_needs_no_handshake_first(self):
        self.assertEqual(len(self.listed), 10)


class TheCalls(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def call(self, name, arguments=None, request_id=3):
        body = message("tools/call", {"name": name, "arguments": arguments} if arguments is not None
                       else {"name": name}, request_id)
        return rpc(self.rig, body)

    def text(self, answer):
        return json.loads(answer["result"]["content"][0]["text"])

    def test_a_tool_runs_through_invoke_and_comes_back_as_json_text(self):
        status, answer = self.call("open_programme", scenario("lantern-room"))
        self.assertEqual(status, 200)
        self.assertEqual(answer["id"], 3)
        self.assertFalse(answer["result"]["isError"])
        self.assertEqual(answer["result"]["content"][0]["type"], "text")
        opened = self.text(answer)
        self.assertEqual(opened["programme_id"], "prog_001")
        read = self.call("get_programme", {"programme_id": "prog_001"})[1]
        self.assertEqual(self.text(read), self.rig.programme("prog_001"))

    def test_every_call_is_the_agents_and_is_logged_so(self):
        self.call("open_programme", scenario("lantern-room"))
        self.call("qloo_budget", {})
        self.call("approve_inquiry", {})
        self.assertEqual([(entry["actor"], entry["tool"], entry["outcome"]) for entry in self.rig.activity()],
                         [("agent", "open_programme", "ok"), ("agent", "qloo_budget", "ok"),
                          ("agent", "approve_inquiry", "not_a_tool")])

    def test_a_refusal_is_a_tool_error_the_agent_can_read_and_correct(self):
        self.call("open_programme", scenario("lantern-room"))
        self.call("find_anchor", {"programme_id": "prog_001", "name": "Alvvays"})
        _, answer = self.call("plan_acts", {"programme_id": "prog_001"})
        self.assertTrue(answer["result"]["isError"])
        refused = self.text(answer)
        self.assertEqual(refused["outcome"], "anchor_unconfirmed")
        self.assertIn("only a person can say", refused["error"]["message"])
        self.assertEqual(self.text(self.call("find_anchor", {"programme_id": "prog_001", "name": "Alvvays",
                                                             "status": "confirmed"})[1])["outcome"],
                         "smuggled_field")
        self.assertEqual(self.text(self.call("get_programme")[1])["outcome"], "bad_args")
        self.assertEqual(self.text(self.call("get_programme", {"programme_id": "prog_009"})[1])["outcome"],
                         "unknown_programme")
        self.assertEqual(self.text(self.call("get_programme", ["x"])[1])["outcome"], "bad_args")

    def test_each_human_verb_is_an_unknown_tool_and_nothing_runs(self):
        pid = self.rig.ready()
        before = self.rig.stored(pid)
        calls = human_calls(pid, before["anchors"][0])
        self.assertEqual(set(calls), set(HUMAN))
        for verb, arguments in calls.items():
            for sent in (arguments, None, {}):
                status, answer = self.call(verb, sent, request_id="verb")
                self.assertEqual(status, 200)
                shaped_error(self, answer, mcp.INVALID_PARAMS, "not_a_tool", "verb")
        self.assertEqual(self.rig.stored(pid), before)

    def test_a_name_that_is_no_tool_is_the_same_unknown_tool_and_a_non_name_is_bad_params(self):
        for name in ("nothing", "", " plan_acts", "PLAN_ACTS", "plan_acts;confirm_anchor", "__class__"):
            shaped_error(self, self.call(name)[1], mcp.INVALID_PARAMS, "not_a_tool", 3)
        for name in (None, 7, ["plan_acts"], {"a": 1}):
            _, answer = rpc(self.rig, message("tools/call", {"name": name}))
            shaped_error(self, answer, mcp.INVALID_PARAMS, request_id=1)
            self.assertNotIn("data", answer["error"])
        _, answer = rpc(self.rig, message("tools/call", {}))
        shaped_error(self, answer, mcp.INVALID_PARAMS, request_id=1)

    def test_the_answer_never_echoes_what_was_sent(self):
        shaped_error(self, self.call("<script>alert(1)</script>")[1], mcp.INVALID_PARAMS, "not_a_tool", 3)
        _, answer = rpc(self.rig, message("<script>alert(2)</script>"))
        self.assertNotIn("script", json.dumps(answer))


class TheProtocolErrors(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def raw(self, body, protocol=None):
        return mcp.handle(self.rig.runtime, self.rig.workspace, body, protocol)

    def test_a_body_that_is_not_json_is_a_parse_error(self):
        for body in (b"", b"{", b"nope", b"\xff\xfe", b"[" * 30000 + b"]" * 30000):
            status, answer = self.raw(body)
            self.assertEqual(status, 400)
            shaped_error(self, answer, mcp.PARSE_ERROR)

    def test_a_message_that_is_not_a_json_rpc_request_is_an_invalid_request(self):
        bodies = (
            b"[]", b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]', b"7", b'"ping"', b"null", b"{}",
            b'{"jsonrpc":"1.0","id":1,"method":"ping"}', b'{"id":1,"method":"ping"}',
            b'{"jsonrpc":"2.0","id":1}', b'{"jsonrpc":"2.0","id":1,"method":7}',
            b'{"jsonrpc":"2.0","id":null,"method":"ping"}', b'{"jsonrpc":"2.0","id":true,"method":"ping"}',
            b'{"jsonrpc":"2.0","id":[1],"method":"ping"}', b'{"jsonrpc":"2.0","id":1.5,"method":"ping"}',
        )
        for body in bodies:
            status, answer = self.raw(body)
            self.assertEqual(status, 400, body)
            shaped_error(self, answer, mcp.INVALID_REQUEST)

    def test_an_unknown_method_is_not_found_with_its_id_and_params_must_be_an_object(self):
        status, answer = self.raw(json.dumps(message("resources/list", id="r1")).encode())
        self.assertEqual(status, 200)
        shaped_error(self, answer, mcp.METHOD_NOT_FOUND, request_id="r1")
        for params in ([], "x", 3, None):
            status, answer = self.raw(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping",
                                                  "params": params}).encode())
            self.assertEqual(status, 200)
            shaped_error(self, answer, mcp.INVALID_PARAMS, request_id=2)

    def test_the_protocol_version_header_is_checked_only_when_sent(self):
        body = json.dumps(message("ping")).encode()
        for version in mcp.VERSIONS:
            self.assertEqual(self.raw(body, version)[0], 200)
        self.assertEqual(self.raw(body, None)[0], 200)
        for version in ("", "1999-01-01", "2025-06-18, 2025-03-26", "latest"):
            status, answer = self.raw(body, version)
            self.assertEqual(status, 400)
            shaped_error(self, answer, mcp.INVALID_REQUEST, "bad_protocol_version")


class Client:
    """What an MCP client does over HTTP at the agent address."""

    def __init__(self, site, address, headers=None):
        self.site, self.path, self.headers = site, address + "/mcp", headers or {}

    def send(self, body, headers=None):
        return self.site.post(self.path, body, {**self.headers, **(headers or {})})

    def call(self, method, params=None, request_id=1):
        reply = self.send(message(method, params, request_id))
        assert reply.status == 200, reply.text
        return reply.json()

    def tool(self, name, arguments):
        answer = self.call("tools/call", {"name": name, "arguments": arguments})
        return answer["result"]["isError"], json.loads(answer["result"]["content"][0]["text"])


class OverTheAgentAddress(unittest.TestCase):
    def test_a_client_connects_lists_the_tools_and_is_refused_a_human_verb(self):
        with Site() as site:
            person = Browser(site).open()
            client = Client(site, person.agent_path())
            init = site.post(client.path, message("initialize", {"protocolVersion": mcp.VERSIONS[1],
                                                                  "capabilities": {}, "clientInfo": {}}))
            self.assertEqual(init.status, 200)
            self.assertTrue(init.headers["content-type"].startswith("application/json"))
            self.assertEqual(init.json()["result"]["protocolVersion"], mcp.VERSIONS[1])
            ready = client.send(message("notifications/initialized", id=None))
            self.assertEqual((ready.status, ready.body), (202, b""))
            listed = client.call("tools/list")["result"]["tools"]
            self.assertEqual([entry["name"] for entry in listed], list(tools.REGISTRY))
            refused = client.call("tools/call", {"name": "approve_inquiry", "arguments": {
                "programme_id": "prog_001", "draft_id": "d1", "sender_name": "Maya", "revision": 1}})
            shaped_error(self, refused, mcp.INVALID_PARAMS, "not_a_tool", 1)

    def test_nothing_a_client_sends_makes_it_the_person(self):
        with Site() as site:
            person = Browser(site).open()
            pid = person.sample()
            anchor = person.programme()["anchors"][0]
            sent = {"name": "confirm_anchor", "arguments": {
                "programme_id": pid, "name": anchor["name"],
                "entity_id": anchor["candidates"][0]["entityId"]}}
            wearing = (
                {},
                {"Cookie": person.cookie},
                {"Cookie": person.cookie, "Sec-Fetch-Site": "same-origin", "Origin": site.origin},
                {"Cookie": person.cookie, "Sec-Fetch-Site": "same-origin", "X-Open-Slot-As": "person"},
                {"Sec-Fetch-Site": "same-origin"},
            )
            for headers in wearing:
                answer = Client(site, person.agent_path(), headers).call("tools/call", sent)
                shaped_error(self, answer, mcp.INVALID_PARAMS, "not_a_tool", 1)
            self.assertIsNone(person.programme()["anchors"][0]["confirmed"])
            self.assertTrue(person.invoke(sent["name"], sent["arguments"])["ok"], "the page's click works")

    def test_the_agent_and_the_person_work_one_programme_through_one_surface(self):
        with Site() as site:
            person = Browser(site).open()
            client = Client(site, person.agent_path())
            failed, opened = client.tool("open_programme", person.state()["sample"])
            self.assertFalse(failed)
            pid = opened["programme_id"]
            for name in opened["anchors"]:
                self.assertFalse(client.tool("find_anchor", {"programme_id": pid, "name": name})[0])
            self.assertEqual(len(person.programme()["anchors"]), 4, "the page sees what the agent did")
            failed, refused = client.tool("plan_acts", {"programme_id": pid})
            self.assertEqual((failed, refused["outcome"]), (True, "anchor_unconfirmed"))
            person.confirm_all(pid)
            failed, planned = client.tool("plan_acts", {"programme_id": pid})
            self.assertFalse(failed)
            self.assertEqual(planned["stopped_because"], "enough_ready")
            self.assertEqual(planned["board"], person.programme()["view"]["board"])
            who = [(entry["actor"], entry["tool"]) for entry in person.state()["workspace"]["activityLog"]]
            self.assertIn(("person", "confirm_anchor"), who)
            self.assertIn(("agent", "plan_acts"), who)
            self.assertNotIn(("agent", "confirm_anchor"), who)

    def test_the_agents_allowance_binds_it_here_and_the_persons_own_click_still_runs(self):
        key = fake_key()
        with Stub(serve_fake) as stub:
            def engine(ledger):
                return providers.LiveProvider(key, ledger, stub.base_url, sleep=lambda seconds: None)

            with Site(engine=engine, key=key, provider="live", agent_calls_per_day=2) as site:
                person = Browser(site).open()
                client = Client(site, person.agent_path())
                _, opened = client.tool("open_programme", person.state()["sample"])
                pid, names = opened["programme_id"], opened["anchors"]
                for name in names[:2]:
                    self.assertFalse(client.tool("find_anchor", {"programme_id": pid, "name": name})[0])
                failed, refused = client.tool("find_anchor", {"programme_id": pid, "name": names[2]})
                self.assertEqual((failed, refused["outcome"]), (True, "agent_allowance_spent"))
                self.assertEqual(len(stub.requests), 2)
                found = person.ok("find_anchor", {"programme_id": pid, "name": names[2]})
                self.assertTrue(found["candidates"])
                self.assertEqual(len(stub.requests), 3)


class EveryErrorIsAJsonRpcError(unittest.TestCase):
    def check(self, test, reply, status, outcome):
        test.assertEqual(reply.status, status, reply.text)
        self.assertTrue(reply.headers["content-type"].startswith("application/json"))
        shaped_error(test, reply.json(), mcp.SERVER_ERROR, outcome)

    def test_each_refusal_before_the_handler_is_one_with_the_http_status_it_has_elsewhere(self):
        with Site(extra_hosts=("demo.example",)) as site:
            person = Browser(site).open()
            path = person.agent_path()
            client = Client(site, path)
            ping = message("ping")
            self.check(self, site.post(f"/agent/{NIL}/mcp", ping), 404, "no_workspace")
            self.check(self, client.send(ping, {"Host": "evil.example"}), 403, "bad_host")
            self.check(self, client.send(ping, {"Origin": "https://evil.example"}), 403, "bad_origin")
            self.check(self, client.send(ping, {"Content-Type": "text/plain"}), 415, "bad_content_type")
            self.check(self, client.send(b"x" * 70000), 413, "too_large")
            for method in ("GET", "DELETE", "PUT", "OPTIONS"):
                reply = site.request(method, path + "/mcp")
                self.check(self, reply, 405, "method_not_allowed")
                self.assertEqual(reply.headers["allow"], "POST")
            self.assertEqual([client.send(ping).status for _ in range(28)], [200] * 28)
            limited = client.send(ping)
            self.check(self, limited, 429, "rate_limited")
            self.assertIn("retry-after", limited.headers)

    def test_a_malformed_message_over_http_is_the_handlers_400_with_a_json_rpc_body(self):
        with Site() as site:
            client = Client(site, Browser(site).open().agent_path())
            bodies = ((b"{", mcp.PARSE_ERROR), (b"[]", mcp.INVALID_REQUEST), (b"{}", mcp.INVALID_REQUEST))
            for body, code in bodies:
                reply = client.send(body)
                self.assertEqual(reply.status, 400)
                shaped_error(self, reply.json(), code)
            reply = client.send(message("ping"), {"MCP-Protocol-Version": "1999-01-01"})
            self.assertEqual(reply.status, 400)
            shaped_error(self, reply.json(), mcp.INVALID_REQUEST, "bad_protocol_version")
            known = {"MCP-Protocol-Version": mcp.VERSIONS[1]}
            self.assertEqual(client.send(message("ping"), known).status, 200)

    def test_an_unexpected_failure_is_a_fixed_json_rpc_error_with_no_trace(self):
        with Site() as site:
            client = Client(site, Browser(site).open().agent_path())
            errors = io.StringIO()
            with mock.patch.object(mcp, "handle", side_effect=KeyError("secret-value")), \
                    contextlib.redirect_stderr(errors):
                reply = client.send(message("ping"))
            self.check(self, reply, 500, "internal")
            self.assertNotIn("secret", reply.text)
            self.assertEqual(errors.getvalue(), "open-slot: internal error (KeyError)\n")


SAME = object()
CLIENT_META = {
    mcp.VERSION_KEY: mcp.MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1"},
    mcp.CAPABILITIES_KEY: {},
}
UNSERVED = ("2025-03-26", "2024-11-05", "2026-10-18")


def modern_body(method, params=None, request_id=1, meta=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method,
            "params": {**(params or {}), "_meta": CLIENT_META if meta is None else meta}}


def modern(rig, body, protocol=mcp.MODERN, method=SAME, name=SAME):
    """A modern request as mcp.handle sees it. The Mcp-Method and Mcp-Name headers are what the body
    says unless a test says otherwise, as an HTTP header is always text."""
    if method is SAME:
        method = body["method"]
    if name is SAME:
        name = body["params"].get("name")
    return mcp.handle(rig.runtime, rig.workspace, json.dumps(body).encode("utf-8"), protocol, method, name)


class ModernDiscover(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def test_it_names_every_revision_it_serves_and_offers_tools_only(self):
        status, answer = modern(self.rig, modern_body("server/discover", request_id="d1"))
        self.assertEqual((status, answer["id"]), (200, "d1"))
        result = answer["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], ["2026-07-28", "2025-11-25", "2025-06-18"])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["_meta"][mcp.SERVER_INFO_KEY], {"name": "open-slot", "version": "0.1"})
        self.assertIn("a person", result["instructions"])
        for verb in HUMAN:
            self.assertNotIn(verb, json.dumps(result))

    def test_it_says_how_long_the_answer_may_be_cached_and_for_whom(self):
        result = modern(self.rig, modern_body("server/discover"))[1]["result"]
        self.assertIsInstance(result["ttlMs"], int)
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertIn(result["cacheScope"], ("public", "private"))

    def test_a_revision_it_cannot_serve_in_full_is_not_claimed_anywhere(self):
        listed = modern(self.rig, modern_body("server/discover"))[1]["result"]["supportedVersions"]
        for version in UNSERVED:
            self.assertNotIn(version, listed)
            self.assertNotIn(version, mcp.VERSIONS)
            _, answer = rpc(self.rig, message("initialize", {"protocolVersion": version}))
            self.assertEqual(answer["result"]["protocolVersion"], mcp.VERSIONS[0])
            status, answer = rpc(self.rig, message("ping"), version)
            self.assertEqual(status, 400)
            shaped_error(self, answer, mcp.INVALID_REQUEST, "bad_protocol_version")


class ModernTools(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def call(self, name, arguments=None, request_id=3):
        params = {"name": name} if arguments is None else {"name": name, "arguments": arguments}
        return modern(self.rig, modern_body("tools/call", params, request_id))

    def text(self, answer):
        return json.loads(answer["result"]["content"][0]["text"])

    def test_the_list_is_the_registry_with_its_descriptions_and_annotations(self):
        status, answer = modern(self.rig, modern_body("tools/list"))
        self.assertEqual(status, 200)
        result = answer["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["tools"], json.loads(json.dumps(tools.listing())))
        self.assertEqual([entry["name"] for entry in result["tools"]], list(tools.REGISTRY))
        for entry in result["tools"]:
            self.assertGreater(len(entry["description"]), 80, entry["name"])
            self.assertEqual(entry["inputSchema"]["type"], "object")
            self.assertEqual(sorted(entry["annotations"]),
                             ["destructiveHint", "idempotentHint", "openWorldHint", "readOnlyHint"])
        self.assertIsInstance(result["ttlMs"], int)
        self.assertIn(result["cacheScope"], ("public", "private"))
        self.assertEqual(result["_meta"][mcp.SERVER_INFO_KEY]["name"], "open-slot")
        for verb in HUMAN:
            self.assertNotIn(verb, json.dumps(result["tools"]))

    def test_the_list_is_the_one_a_legacy_client_gets(self):
        legacy = rpc(self.rig, message("tools/list"))[1]["result"]["tools"]
        self.assertEqual(modern(self.rig, modern_body("tools/list"))[1]["result"]["tools"], legacy)

    def test_an_allowed_tool_runs_through_invoke_as_the_agent_and_comes_back_twice_over(self):
        status, answer = self.call("open_programme", scenario("lantern-room"))
        self.assertEqual((status, answer["id"]), (200, 3))
        result = answer["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(self.text(answer), result["structuredContent"])
        self.assertEqual(result["structuredContent"]["programme_id"], "prog_001")
        read = self.call("get_programme", {"programme_id": "prog_001"})[1]
        self.assertEqual([(entry["actor"], entry["tool"], entry["outcome"]) for entry in self.rig.activity()],
                         [("agent", "open_programme", "ok"), ("agent", "get_programme", "ok")])
        self.assertEqual(read["result"]["structuredContent"], self.rig.programme("prog_001"))

    def test_a_refusal_is_a_tool_error_the_agent_can_read(self):
        self.call("open_programme", scenario("lantern-room"))
        self.call("find_anchor", {"programme_id": "prog_001", "name": "Alvvays"})
        status, answer = self.call("plan_acts", {"programme_id": "prog_001"})
        self.assertEqual(status, 200)
        self.assertTrue(answer["result"]["isError"])
        self.assertEqual(answer["result"]["resultType"], "complete")
        self.assertEqual(self.text(answer)["outcome"], "anchor_unconfirmed")
        self.assertEqual(answer["result"]["structuredContent"], self.text(answer))

    def test_each_human_verb_is_an_unknown_tool_and_nothing_changes(self):
        pid = self.rig.ready()
        before = self.rig.stored(pid)
        calls = human_calls(pid, before["anchors"][0])
        self.assertEqual(set(calls), set(HUMAN))
        for verb, arguments in calls.items():
            for sent in (arguments, None, {}):
                status, answer = self.call(verb, sent, request_id="verb")
                self.assertEqual(status, 200)
                shaped_error(self, answer, mcp.INVALID_PARAMS, "not_a_tool", "verb")
        self.assertEqual(self.rig.stored(pid), before)
        refused = [entry for entry in self.rig.activity() if entry["outcome"] == "not_a_tool"]
        self.assertEqual(sorted({entry["tool"] for entry in refused}), sorted(HUMAN))
        self.assertEqual({entry["actor"] for entry in refused}, {"agent"})

    def test_a_name_that_is_no_tool_is_the_same_unknown_tool_and_never_echoed(self):
        for name in ("nothing", "PLAN_ACTS", "__class__", "<script>alert(1)</script>"):
            status, answer = self.call(name)
            self.assertEqual(status, 200)
            shaped_error(self, answer, mcp.INVALID_PARAMS, "not_a_tool", 3)
            self.assertNotIn("script", json.dumps(answer))


class ModernHeaderRules(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.listing = modern_body("tools/list")
        self.call = modern_body("tools/call", {"name": "qloo_budget", "arguments": {}})

    def refused(self, answer_pair, code=mcp.HEADER_MISMATCH):
        status, answer = answer_pair
        self.assertEqual(status, 400)
        shaped_error(self, answer, code, request_id=1)
        return answer

    def test_a_header_that_disagrees_with_the_body_is_a_header_mismatch(self):
        cases = (
            modern(self.rig, self.listing, method="tools/call"),
            modern(self.rig, self.listing, method="TOOLS/LIST"),
            modern(self.rig, self.listing, method=None),
            modern(self.rig, self.call, name="plan_acts"),
            modern(self.rig, self.call, name=None),
            modern(self.rig, self.call, name=""),
            modern(self.rig, self.listing, protocol=None),
            modern(self.rig, modern_body("tools/list", meta={**CLIENT_META, mcp.VERSION_KEY: "2027-01-01"})),
        )
        for pair in cases:
            self.refused(pair)

    def test_nothing_is_run_when_a_header_disagrees(self):
        self.rig.ready()
        before = self.rig.runtime.doc
        call = modern_body("tools/call", {"name": "qloo_budget", "arguments": {}})
        self.refused(modern(self.rig, call, name="get_programme"))
        self.assertIs(self.rig.runtime.doc, before)

    def test_the_answer_never_echoes_a_header(self):
        answer = self.refused(modern(self.rig, self.call, name="<script>evil</script>"))
        self.assertNotIn("evil", json.dumps(answer))
        answer = self.refused(modern(self.rig, self.listing, method="evil/method"))
        self.assertNotIn("evil", json.dumps(answer))

    def test_a_name_in_the_base64_sentinel_form_is_decoded_before_it_is_compared(self):
        encoded = "=?base64?" + base64.b64encode(b"qloo_budget").decode("ascii") + "?="
        status, answer = modern(self.rig, self.call, name=encoded)
        self.assertEqual(status, 200)
        self.assertFalse(answer["result"]["isError"])
        wrong = "=?base64?" + base64.b64encode(b"plan_acts").decode("ascii") + "?="
        self.refused(modern(self.rig, self.call, name=wrong))
        self.refused(modern(self.rig, self.call, name="=?base64?%%%?="))
        self.refused(modern(self.rig, self.call, name="=?base64?/w==?="))

    def test_the_name_header_is_checked_only_for_a_call(self):
        status, _ = modern(self.rig, self.listing, name="anything")
        self.assertEqual(status, 200)

    def test_a_request_without_what_every_request_carries_is_invalid_params(self):
        cases = (
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": []},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": []}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
                mcp.VERSION_KEY: mcp.MODERN}}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
                mcp.CAPABILITIES_KEY: {}}}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
                mcp.VERSION_KEY: mcp.MODERN, mcp.CAPABILITIES_KEY: "none"}}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
                mcp.VERSION_KEY: 20260728, mcp.CAPABILITIES_KEY: {}}}},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
                mcp.VERSION_KEY: None, mcp.CAPABILITIES_KEY: {}}}},
        )
        for body in cases:
            self.refused(modern(self.rig, body, method="tools/list", name=None), mcp.INVALID_PARAMS)


class ModernVersionRules(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def test_a_version_it_does_not_serve_gets_the_error_that_lists_the_ones_it_does(self):
        for version in ("2027-01-01", "2026-10-18", "1900-01-01", "latest", "2025-11-25", "2025-03-26"):
            body = modern_body("tools/list", meta={**CLIENT_META, mcp.VERSION_KEY: version})
            status, answer = modern(self.rig, body, protocol=version)
            self.assertEqual(status, 400, version)
            shaped_error(self, answer, mcp.UNSUPPORTED_VERSION, request_id=1)
            self.assertEqual(answer["error"]["data"], {
                "supported": ["2026-07-28", "2025-11-25", "2025-06-18"], "requested": version})

    def test_the_version_comes_before_the_rules_of_a_version_it_does_not_serve(self):
        body = modern_body("tools/call", {"name": "qloo_budget"}, meta={mcp.VERSION_KEY: "2027-01-01"})
        status, answer = modern(self.rig, body, protocol="2027-01-01", method="other", name=None)
        self.assertEqual(status, 400)
        shaped_error(self, answer, mcp.UNSUPPORTED_VERSION, request_id=1)

    def test_a_version_it_does_not_serve_in_the_header_alone_is_not_a_modern_request(self):
        status, answer = rpc(self.rig, message("tools/list"), "2027-01-01")
        self.assertEqual(status, 400)
        shaped_error(self, answer, mcp.INVALID_REQUEST, "bad_protocol_version")


class ModernEnvelope(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def test_a_method_it_does_not_implement_is_404_with_method_not_found(self):
        for method in ("resources/list", "resources/read", "prompts/list", "prompts/get",
                       "subscriptions/listen", "ping", "initialize", "logging/setLevel",
                       "completion/complete", "tasks/get", "server/nothing", "<script>x</script>"):
            body = modern_body(method, request_id="m1")
            status, answer = modern(self.rig, body)
            self.assertEqual(status, 404, method)
            shaped_error(self, answer, mcp.METHOD_NOT_FOUND, request_id="m1")
            self.assertNotIn("data", answer["error"])
            self.assertNotIn("script", json.dumps(answer))

    def test_a_method_is_looked_up_only_after_the_headers_are_checked(self):
        status, answer = modern(self.rig, modern_body("resources/list"), method="tools/list")
        self.assertEqual(status, 400)
        shaped_error(self, answer, mcp.HEADER_MISMATCH, request_id=1)

    def test_a_notification_is_accepted_and_needs_no_answer(self):
        header_only = message("notifications/initialized", id=None)
        self.assertEqual(modern(self.rig, header_only, method=None, name=None), (202, None))
        meta_only = {"jsonrpc": "2.0", "method": "notifications/anything", "params": {"_meta": CLIENT_META}}
        self.assertEqual(modern(self.rig, meta_only, protocol=None, method=None, name=None), (202, None))

    def test_one_request_or_notification_is_all_a_post_may_carry(self):
        bodies = (
            [modern_body("tools/list")], [], {"jsonrpc": "2.0", "id": 1, "result": {}},
            {"jsonrpc": "1.0", "id": 1, "method": "tools/list"}, {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "id": 1, "method": 7}, 7, "tools/list", None,
        )
        for body in bodies:
            status, answer = modern(self.rig, body, method="tools/list", name=None)
            self.assertEqual(status, 400, body)
            shaped_error(self, answer, mcp.INVALID_REQUEST)

    def test_an_id_that_is_not_a_string_or_an_integer_is_an_invalid_request(self):
        for request_id in (None, True, 1.5, [1], {"a": 1}):
            body = {**modern_body("tools/list"), "id": request_id}
            status, answer = modern(self.rig, body)
            self.assertEqual(status, 400, request_id)
            shaped_error(self, answer, mcp.INVALID_REQUEST)

    def test_a_body_that_is_not_json_is_a_parse_error(self):
        status, answer = mcp.handle(self.rig.runtime, self.rig.workspace, b"{", mcp.MODERN, "tools/list")
        self.assertEqual(status, 400)
        shaped_error(self, answer, mcp.PARSE_ERROR)


class OneEndpointBothEras(unittest.TestCase):
    META_HEADERS = {"Accept": "application/json, text/event-stream"}

    def modern(self, site, address, method, params=None, request_id=1, **headers):
        body = modern_body(method, params, request_id)
        sent = {"MCP-Protocol-Version": mcp.MODERN, "Mcp-Method": method, **self.META_HEADERS}
        if method == "tools/call":
            sent["Mcp-Name"] = params["name"]
        return site.post(address + "/mcp", body, {**sent, **headers})

    def test_a_modern_client_needs_no_handshake_and_no_session(self):
        with Site() as site:
            address = Browser(site).open().agent_path()
            discovered = self.modern(site, address, "server/discover")
            self.assertEqual(discovered.status, 200)
            self.assertTrue(discovered.headers["content-type"].startswith("application/json"))
            self.assertNotIn("mcp-session-id", discovered.headers)
            result = discovered.json()["result"]
            self.assertEqual(result["supportedVersions"], ["2026-07-28", "2025-11-25", "2025-06-18"])
            listed = self.modern(site, address, "tools/list")
            self.assertEqual([entry["name"] for entry in listed.json()["result"]["tools"]],
                             list(tools.REGISTRY))
            opened = self.modern(site, address, "tools/call", {"name": "open_programme",
                                                               "arguments": scenario("lantern-room")})
            self.assertFalse(opened.json()["result"]["isError"])
            self.assertEqual(opened.json()["result"]["structuredContent"]["programme_id"], "prog_001")
            self.assertNotIn("mcp-session-id", opened.headers)

    def test_a_stray_session_id_is_ignored_and_never_echoed(self):
        with Site() as site:
            address = Browser(site).open().agent_path()
            plain = self.modern(site, address, "tools/list")
            for stray in ("abc", "session-from-an-older-client", ""):
                with_id = self.modern(site, address, "tools/list", **{"Mcp-Session-Id": stray})
                self.assertEqual((with_id.status, with_id.json()), (200, plain.json()))
                self.assertNotIn("mcp-session-id", with_id.headers)
            ready = site.post(address + "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                              {"MCP-Protocol-Version": mcp.MODERN, "Mcp-Session-Id": "abc"})
            self.assertEqual((ready.status, ready.body), (202, b""))
            self.assertNotIn("mcp-session-id", ready.headers)

    def test_get_and_delete_are_405_on_a_modern_request_too(self):
        with Site() as site:
            address = Browser(site).open().agent_path()
            for method in ("GET", "DELETE"):
                reply = site.request(method, address + "/mcp", None, {
                    "MCP-Protocol-Version": mcp.MODERN, "Accept": "text/event-stream",
                    "Mcp-Session-Id": "abc", "Last-Event-ID": "7"})
                self.assertEqual(reply.status, 405)
                self.assertEqual(reply.headers["allow"], "POST")
                self.assertNotIn("mcp-session-id", reply.headers)
                shaped_error(self, reply.json(), mcp.SERVER_ERROR, "method_not_allowed")

    def test_a_modern_client_that_probes_an_unknown_method_gets_an_error_it_can_recognise(self):
        with Site() as site:
            address = Browser(site).open().agent_path()
            for method in ("resources/list", "ping", "initialize", "subscriptions/listen"):
                reply = self.modern(site, address, method, request_id="probe")
                self.assertEqual(reply.status, 404, method)
                self.assertTrue(reply.headers["content-type"].startswith("application/json"))
                answer = reply.json()
                shaped_error(self, answer, mcp.METHOD_NOT_FOUND, request_id="probe")
                self.assertNotIn("outcome", json.dumps(answer))
            wrong = self.modern(site, address, "tools/list", **{"MCP-Protocol-Version": "2027-01-01"})
            self.assertEqual(wrong.status, 400)
            self.assertEqual(wrong.json()["error"]["code"], mcp.HEADER_MISMATCH)

    def test_a_modern_request_for_a_version_it_does_not_serve_is_told_which_it_does(self):
        with Site() as site:
            address = Browser(site).open().agent_path()
            body = modern_body("server/discover", meta={**CLIENT_META, mcp.VERSION_KEY: "2027-01-01"})
            reply = site.post(address + "/mcp", body, {
                "MCP-Protocol-Version": "2027-01-01", "Mcp-Method": "server/discover"})
            self.assertEqual(reply.status, 400)
            self.assertEqual(reply.json()["error"]["code"], mcp.UNSUPPORTED_VERSION)
            self.assertEqual(reply.json()["error"]["data"]["supported"][0], mcp.MODERN)

    def test_a_modern_request_is_the_agent_whatever_else_it_sends_and_a_bad_origin_is_refused(self):
        with Site() as site:
            person = Browser(site).open()
            pid = person.sample()
            anchor = person.programme()["anchors"][0]
            before = person.programme()
            address = person.agent_path()
            args = human_calls(pid, anchor)["confirm_anchor"]
            for headers in ({}, {"Cookie": person.cookie},
                            {"Cookie": person.cookie, "Sec-Fetch-Site": "same-origin", "Origin": site.origin,
                             "X-Open-Slot-As": "person"}):
                reply = self.modern(site, address, "tools/call",
                                    {"name": "confirm_anchor", "arguments": args}, **headers)
                self.assertEqual(reply.status, 200)
                shaped_error(self, reply.json(), mcp.INVALID_PARAMS, "not_a_tool", 1)
            self.assertEqual(person.programme(), before)
            refused = self.modern(site, address, "tools/list", Origin="https://evil.example")
            self.assertEqual(refused.status, 403)
            shaped_error(self, refused.json(), mcp.SERVER_ERROR, "bad_origin")

    def test_one_client_can_use_either_era_in_turn_and_gets_the_same_tools(self):
        with Site() as site:
            address = Browser(site).open().agent_path()
            first = self.modern(site, address, "tools/list").json()["result"]["tools"]
            legacy = Client(site, address)
            init = site.post(legacy.path, message("initialize", {"protocolVersion": mcp.VERSIONS[0],
                                                                  "capabilities": {}, "clientInfo": {}}))
            self.assertEqual(init.json()["result"]["protocolVersion"], mcp.VERSIONS[0])
            self.assertNotIn("mcp-session-id", init.headers)
            self.assertEqual(legacy.send(message("notifications/initialized", id=None)).status, 202)
            self.assertEqual(legacy.call("tools/list")["result"]["tools"], first)
            again = self.modern(site, address, "tools/list").json()["result"]["tools"]
            self.assertEqual(again, first)

    def test_a_legacy_client_stays_legacy_whatever_it_puts_in_meta(self):
        with Site() as site:
            address = Browser(site).open().agent_path()
            legacy = Client(site, address, {"MCP-Protocol-Version": mcp.VERSIONS[0]})
            listed = legacy.call("tools/list", {"_meta": {"progressToken": "p1"}})["result"]
            self.assertEqual(sorted(listed), ["tools"])
            called = legacy.call("tools/call", {"name": "qloo_budget", "arguments": {},
                                                "_meta": {"progressToken": "p2"}})["result"]
            self.assertEqual(sorted(called), ["content", "isError"])
            self.assertNotIn("resultType", called)
            self.assertEqual(legacy.call("ping")["result"], {})
            unknown = legacy.send(message("server/discover"))
            self.assertEqual((unknown.status, unknown.json()["error"]["code"]), (200, mcp.METHOD_NOT_FOUND))


TOKEN = "0123456789abcdef0123456789abcdef"


class TheEndpointOfAnAddress(unittest.TestCase):
    def test_an_agent_address_with_or_without_mcp_and_slashes_names_one_endpoint(self):
        for origin in ("https://demo.example", "https://demo.example:8443", "http://127.0.0.1:8760",
                       "http://localhost:3000", "http://[::1]:8760"):
            for tail in ("", "/", "/mcp", "/mcp/"):
                self.assertEqual(mcp.agent_endpoint(f" {origin}/agent/{TOKEN}{tail}\n"),
                                 f"{origin}/agent/{TOKEN}/mcp")

    def test_anything_else_is_refused_without_repeating_the_address(self):
        bad = (
            f"http://demo.example/agent/{TOKEN}", f"http://127.0.0.1.evil.example/agent/{TOKEN}",
            f"ftp://demo.example/agent/{TOKEN}", f"https://demo.example/agent/{TOKEN[:-1]}",
            f"https://demo.example/agent/{TOKEN.upper()}", f"https://demo.example/agent/{TOKEN}/tools",
            f"https://demo.example/agent/{TOKEN}/mcp/extra", f"https://demo.example/agent/{TOKEN}?x=1",
            f"https://demo.example/agent/{TOKEN}#top", f"https://user@demo.example/agent/{TOKEN}",
            f"https://demo.example/{TOKEN}", "https://demo.example", f"/agent/{TOKEN}", "", "   ",
        )
        for address in bad:
            with self.assertRaises(ValueError, msg=address) as caught:
                mcp.agent_endpoint(address)
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertNotIn("demo.example", str(caught.exception))


class StdioRelay(unittest.TestCase):
    """The relay against a real server on a loopback port: whatever the address answers, the relay
    writes, one line each, and what it cannot forward it answers with a JSON-RPC error."""

    def setUp(self):
        self.site = Site()
        self.site.__enter__()
        self.addCleanup(self.site.__exit__, None, None, None)
        self.person = Browser(self.site).open()
        self.path = self.person.agent_path()
        self.endpoint = mcp.agent_endpoint(self.site.origin + self.path)

    def run_relay(self, *messages, endpoint=None):
        wire = b"".join(m if isinstance(m, bytes) else json.dumps(m).encode("utf-8") + b"\n"
                        for m in messages)
        out, err = io.BytesIO(), io.StringIO()
        code = mcp.relay(endpoint or self.endpoint, io.BytesIO(wire), out, err)
        raw = out.getvalue()
        self.assertEqual(raw.count(b"\n"), len(raw.splitlines()), "an answer holds a newline")
        self.assertTrue(raw == b"" or raw.endswith(b"\n"))
        return code, [json.loads(line) for line in raw.splitlines()], err.getvalue()

    def legacy_start(self):
        return [message("initialize", {"protocolVersion": mcp.VERSIONS[0], "capabilities": {},
                                       "clientInfo": {"name": "stdio-test", "version": "1"}}),
                message("notifications/initialized", id=None)]

    def test_a_legacy_session_over_stdio_initializes_lists_the_tools_and_calls_one(self):
        code, answers, err = self.run_relay(
            *self.legacy_start(), message("ping", id=2), message("tools/list", id=3),
            message("tools/call", {"name": "qloo_budget", "arguments": {}}, id=4))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual([a["id"] for a in answers], [1, 2, 3, 4])
        self.assertEqual(answers[0]["result"]["protocolVersion"], mcp.VERSIONS[0])
        self.assertEqual(answers[1]["result"], {})
        self.assertEqual([t["name"] for t in answers[2]["result"]["tools"]], list(tools.REGISTRY))
        self.assertFalse(answers[3]["result"]["isError"])
        self.assertIn("agent_allowance", json.loads(answers[3]["result"]["content"][0]["text"]))

    def test_a_modern_session_over_stdio_needs_no_handshake(self):
        _, answers, err = self.run_relay(
            modern_body("server/discover", request_id=1), modern_body("tools/list", request_id=2),
            modern_body("tools/call", {"name": "open_programme", "arguments": scenario("lantern-room")},
                        request_id=3))
        self.assertEqual(err, "")
        self.assertEqual(answers[0]["result"]["supportedVersions"], list(mcp.SUPPORTED))
        self.assertEqual([t["name"] for t in answers[1]["result"]["tools"]], list(tools.REGISTRY))
        self.assertEqual(answers[2]["result"]["resultType"], "complete")
        self.assertEqual(answers[2]["result"]["structuredContent"]["programme_id"], "prog_001")

    def test_the_relay_gives_the_answers_the_endpoint_gives(self):
        client = Client(self.site, self.path)
        _, answers, _ = self.run_relay(message("tools/list", id=7))
        self.assertEqual(answers, [client.call("tools/list", request_id=7)])

    def test_a_human_verb_is_refused_in_both_eras_and_changes_nothing(self):
        pid = self.person.sample()
        anchor = self.person.programme()["anchors"][0]
        before = self.person.programme()
        for verb, arguments in human_calls(pid, anchor).items():
            _, legacy, _ = self.run_relay(*self.legacy_start(),
                                          message("tools/call", {"name": verb, "arguments": arguments}, id=9))
            shaped_error(self, legacy[1], mcp.INVALID_PARAMS, "not_a_tool", 9)
            _, modern_answers, _ = self.run_relay(
                modern_body("tools/call", {"name": verb, "arguments": arguments}, request_id=9))
            shaped_error(self, modern_answers[0], mcp.INVALID_PARAMS, "not_a_tool", 9)
        self.assertEqual(self.person.programme(), before)

    def test_a_refusal_the_agent_can_correct_is_a_tool_error_that_names_it(self):
        pid = self.person.sample()
        _, answers, _ = self.run_relay(
            modern_body("tools/call", {"name": "plan_acts", "arguments": {"programme_id": pid}}))
        result = answers[0]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["outcome"], "anchor_unconfirmed")

    def test_the_end_of_input_ends_the_relay_cleanly_with_no_output(self):
        for wire in (b"", b"\n\n  \n"):
            out = io.BytesIO()
            self.assertEqual(mcp.relay(self.endpoint, io.BytesIO(wire), out, io.StringIO()), 0)
            self.assertEqual(out.getvalue(), b"")

    def test_a_last_line_with_no_newline_is_still_answered(self):
        wire = json.dumps(message("ping")).encode("utf-8")
        out = io.BytesIO()
        self.assertEqual(mcp.relay(self.endpoint, io.BytesIO(wire), out), 0)
        self.assertEqual(json.loads(out.getvalue()), {"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_a_line_that_is_not_json_is_a_parse_error_and_the_next_line_is_still_served(self):
        for junk in (b"not json\n", b"{\"jsonrpc\": \n", b"\xff\xfe\x00 \n", b"[" * 30000 + b"\n"):
            _, answers, err = self.run_relay(junk, message("ping", id=5))
            self.assertEqual(err, "")
            shaped_error(self, answers[0], mcp.PARSE_ERROR, request_id=None)
            self.assertEqual(answers[1], {"jsonrpc": "2.0", "id": 5, "result": {}})

    def test_what_is_json_but_not_a_message_gets_the_endpoints_own_refusal(self):
        batch = b"[" + json.dumps(message("ping")).encode("utf-8") + b"]\n"
        for odd in (b"42\n", b'"text"\n', b"null\n", b"[]\n", b"{}\n", batch):
            _, answers, _ = self.run_relay(odd, message("ping", id=5))
            shaped_error(self, answers[0], mcp.INVALID_REQUEST, request_id=None)
            self.assertEqual(answers[1]["id"], 5)

    def test_a_line_over_the_limit_is_refused_and_skipped_whole(self):
        huge = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"pad":"' + b"x" * mcp.MAX_LINE + b'"}}\n'
        edge = b'{"jsonrpc":"2.0","id":2,"method":"ping","params":{"pad":"'
        edge += b"x" * (mcp.MAX_LINE - len(edge) - 3) + b'"}}\n'
        self.assertEqual(len(edge), mcp.MAX_LINE + 1)
        _, answers, _ = self.run_relay(huge, huge, edge, message("ping", id=3))
        shaped_error(self, answers[0], mcp.INVALID_REQUEST, request_id=None)
        shaped_error(self, answers[1], mcp.INVALID_REQUEST, request_id=None)
        self.assertEqual([a["id"] for a in answers[2:]], [2, 3])
        _, answers, _ = self.run_relay(message("ping", id=4), huge[:-1])
        self.assertEqual([a.get("id") for a in answers], [4, None])
        shaped_error(self, answers[1], mcp.INVALID_REQUEST, request_id=None)

    def test_a_notification_gets_no_line_and_a_batch_gets_the_endpoints_refusal(self):
        quiet = modern_body("notifications/cancelled", {"requestId": 1})
        del quiet["id"]
        _, answers, _ = self.run_relay(message("notifications/cancelled", {"requestId": 1}, id=None), quiet,
                                       [message("ping", id=1), message("ping", id=2)])
        self.assertEqual(len(answers), 1)
        shaped_error(self, answers[0], mcp.INVALID_REQUEST, request_id=None)

    def test_a_modern_request_the_endpoint_refuses_comes_back_as_its_error(self):
        for body, code in (
            (modern_body("tools/list", meta={**CLIENT_META, mcp.VERSION_KEY: "2027-01-01"}),
             mcp.UNSUPPORTED_VERSION),
            (modern_body("tools/list", meta={mcp.VERSION_KEY: mcp.MODERN}), mcp.INVALID_PARAMS),
            (modern_body("resources/list"), mcp.METHOD_NOT_FOUND),
        ):
            _, answers, _ = self.run_relay(body)
            shaped_error(self, answers[0], code, request_id=1)

    def test_a_wrong_token_is_the_endpoints_refusal_and_the_token_is_never_echoed(self):
        wrong = mcp.agent_endpoint(f"{self.site.origin}/agent/{NIL}")
        _, answers, err = self.run_relay(message("tools/list"), endpoint=wrong)
        shaped_error(self, answers[0], mcp.SERVER_ERROR, "no_workspace")
        self.assertNotIn(NIL, json.dumps(answers) + err)

    def test_an_address_nobody_answers_is_an_error_with_the_requests_id_and_no_address(self):
        with Stub(lambda r: (200, {}, {})) as stub:
            dead = mcp.agent_endpoint(f"{stub.base_url}/agent/{TOKEN}")
        code, answers, err = self.run_relay(message("tools/list", id="abc"), endpoint=dead)
        self.assertEqual(code, 0)
        shaped_error(self, answers[0], mcp.SERVER_ERROR, "agent_connection", "abc")
        self.assertEqual(answers[0]["error"]["message"], "The agent address could not be reached.")
        self.assertNotIn(TOKEN, json.dumps(answers) + err)
        self.assertNotIn("127.0.0.1", json.dumps(answers) + err)

    def test_a_notification_nobody_can_take_is_said_on_stderr_and_not_on_stdout(self):
        with Stub(lambda r: (200, {}, {})) as stub:
            dead = mcp.agent_endpoint(f"{stub.base_url}/agent/{TOKEN}")
        _, answers, err = self.run_relay(message("notifications/initialized", id=None), endpoint=dead)
        self.assertEqual(answers, [])
        self.assertEqual(err, "open-slot: mcp: The agent address could not be reached.\n")

    def test_an_answer_that_is_not_mcp_becomes_an_error_and_a_redirect_is_not_followed(self):
        with Stub(lambda r: (200, {}, {})) as elsewhere:
            target = elsewhere.base_url + "/stolen"
            for status, body, headers in ((503, b"<html>waking up</html>", {}), (200, b"plain text", {}),
                                          (200, b"[1, 2]", {}), (302, b"", {"Location": target})):
                with Stub(lambda r: (status, body, headers)) as stub:
                    endpoint = mcp.agent_endpoint(f"{stub.base_url}/agent/{TOKEN}")
                    _, answers, err = self.run_relay(message("tools/list", id=4), endpoint=endpoint)
                shaped_error(self, answers[0], mcp.SERVER_ERROR, "not_mcp", 4)
                self.assertIn(f"HTTP {status}", answers[0]["error"]["message"])
                self.assertNotIn(TOKEN, json.dumps(answers) + err)
        self.assertEqual(elsewhere.requests, [])

    def test_what_is_forwarded_is_the_clients_own_bytes_with_the_headers_its_era_needs(self):
        reply = {"jsonrpc": "2.0", "id": 1, "result": {}}
        with Stub(lambda r: (200, reply, {})) as stub:
            endpoint = mcp.agent_endpoint(f"{stub.base_url}/agent/{TOKEN}/mcp/")
            legacy = b'{"jsonrpc":"2.0",  "id":1,"method":"ping"}'
            call = modern_body("tools/call", {"name": "qloo_budget", "arguments": {}})
            odd = modern_body("tools/call", {"name": "caf\u00e9\ntool", "arguments": {}})
            self.run_relay(legacy + b"\n", call, odd, endpoint=endpoint)
        seen = stub.requests
        self.assertEqual([r["path"] for r in seen], [f"/agent/{TOKEN}/mcp"] * 3)
        self.assertEqual([r["method"] for r in seen], ["POST"] * 3)
        self.assertEqual(seen[0]["body"], legacy.decode("utf-8"))
        for r in seen:
            self.assertEqual(r["headers"]["content-type"], "application/json")
            self.assertEqual(r["headers"]["accept"], "application/json, text/event-stream")
        legacy_headers = {name for name in seen[0]["headers"] if name.startswith(("mcp", "x-"))}
        self.assertEqual(legacy_headers, set())
        self.assertEqual(seen[1]["headers"]["mcp-protocol-version"], mcp.MODERN)
        self.assertEqual(seen[1]["headers"]["mcp-method"], "tools/call")
        self.assertEqual(seen[1]["headers"]["mcp-name"], "qloo_budget")
        encoded = base64.b64encode("caf\u00e9\ntool".encode("utf-8")).decode("ascii")
        self.assertEqual(seen[2]["headers"]["mcp-name"], f"=?base64?{encoded}?=")

    def test_a_header_value_is_plain_only_when_it_is_visible_ascii_and_not_the_sentinel(self):
        self.assertEqual(mcp.header_text("qloo_budget"), "qloo_budget")
        self.assertEqual(mcp.header_text("a b"), "a b")
        for value in ("caf\u00e9", " padded ", "line1\nline2", "=?base64?literal?=", "tab\there"):
            shown = mcp.header_text(value)
            self.assertTrue(shown.startswith("=?base64?") and shown.endswith("?="), value)
            self.assertEqual(base64.b64decode(shown[9:-2]).decode("utf-8"), value)
        self.assertEqual(mcp.header_text("=?base64?literal?="), "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?=")

    def test_the_token_never_reaches_stdout_or_stderr(self):
        pid = self.person.sample()
        _, answers, err = self.run_relay(*self.legacy_start(), message("tools/list", id=2), b"junk\n",
                                         message("tools/call", {"name": "get_programme", "arguments": {
                                             "programme_id": pid}}, id=3))
        token = self.path.rsplit("/", 1)[-1]
        self.assertNotIn(token, json.dumps(answers) + err)


class TheMcpCommand(unittest.TestCase):
    def test_the_process_relays_two_messages_and_exits_zero_at_the_end_of_input(self):
        with Site() as site:
            address = site.origin + Browser(site).open().agent_path()
            wire = "".join(json.dumps(message(method, id=n)) + "\n"
                           for n, method in ((1, "ping"), (2, "tools/list")))
            done = subprocess.run([sys.executable, "-m", "open_slot", "mcp", "--address", address],
                                  input=wire.encode("utf-8"), capture_output=True, cwd=ROOT, timeout=30)
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        answers = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual([a["id"] for a in answers], [1, 2])
        self.assertEqual([t["name"] for t in answers[1]["result"]["tools"]], list(tools.REGISTRY))
        self.assertNotIn(address.rsplit("/", 1)[-1].encode("utf-8"), done.stdout + done.stderr)

    def test_a_bad_address_is_refused_with_status_2_and_a_sentence_that_does_not_repeat_it(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["mcp", "--address", f"http://demo.example/agent/{TOKEN}"])
        self.assertEqual(code, 2)
        self.assertIn("the address must look like https://<host>/agent/<token>", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())

    def test_the_address_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.main(["mcp"])
        self.assertEqual(caught.exception.code, 2)

    def test_a_client_that_closes_its_end_ends_the_relay_without_a_trace(self):
        class Closed(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError

        with Site() as site:
            address = site.origin + Browser(site).open().agent_path()
            stdin = io.BytesIO(json.dumps(message("ping")).encode("utf-8") + b"\n")
            fake = mock.Mock(stdin=mock.Mock(buffer=stdin), stdout=mock.Mock(buffer=Closed()))
            with mock.patch.object(cli, "sys", fake):
                self.assertEqual(cli.main(["mcp", "--address", address]), 0)


if __name__ == "__main__":
    unittest.main()
