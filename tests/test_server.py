import contextlib
import io
import json
import socket
import threading
import time
import unittest
from unittest import mock

from open_slot import invoke, providers, qloo, server, tools
from tests.support import Stub, fake_key
from tests.webrig import HUMAN, Browser, Site

SECURE = server.SECURITY_HEADERS
DASH = "0" * 32
API_GETS = ("/api/state", "/api/tools", "/api/calls", "/api/inquiry/prog_001-d1.txt")


def refusal(test, reply, status, code):
    test.assertEqual(reply.status, status, reply.text)
    body = reply.json()
    test.assertEqual((body["ok"], body["outcome"], body["error"]["code"]), (False, code, code))
    test.assertTrue(body["error"]["message"])


def exchange(site, raw):
    """Sends raw bytes to the server and returns everything it answers."""
    with socket.create_connection(("127.0.0.1", site.port), timeout=10) as sock:
        sock.sendall(raw)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


class Gated(providers.FakeProvider):
    """A synthetic provider that stops inside its second Insights call until released, like a slow Qloo."""

    def __init__(self):
        super().__init__()
        self.reached, self.release = threading.Event(), threading.Event()
        self.wide_calls = 0

    def fetch(self, request, origin=None):
        if request.path == qloo.INSIGHTS:
            self.wide_calls += 1
            if self.wide_calls == 2:
                self.reached.set()
                self.release.wait(10)
        return super().fetch(request, origin)


class ThePage(unittest.TestCase):
    def test_the_first_visit_serves_the_page_and_starts_a_workspace_whatever_site_it_came_from(self):
        with Site() as site:
            for label in ("none", "cross-site", "same-site", "same-origin"):
                reply = site.get("/", {"Sec-Fetch-Site": label})
                self.assertEqual(reply.status, 200, label)
                self.assertIn("text/html", reply.headers["content-type"])
                self.assertIn(b"<title>Open Slot</title>", reply.body)
                self.assertRegex(reply.headers["set-cookie"],
                                 r"^os_w=ws_[0-9a-f]{32}; HttpOnly; SameSite=Strict; Path=/$", label)

    def test_a_later_visit_with_the_cookie_starts_nothing_and_a_forged_cookie_starts_a_new_workspace(self):
        with Site() as site:
            first = site.get("/").headers["set-cookie"].split(";")[0]
            self.assertNotIn("set-cookie", site.get("/", {"Cookie": first}).headers)
            for forged in (f"os_w=ws_{DASH}", "os_w=ws_", "os_w=", "os_w=../..", "other=1",
                           f"x; os_w=ws_{DASH}"):
                reply = site.get("/", {"Cookie": forged})
                self.assertIn("set-cookie", reply.headers, forged)
                self.assertNotIn(DASH, reply.headers["set-cookie"])

    def test_the_static_files_are_served_with_their_types_and_start_no_workspace(self):
        with Site() as site:
            for path, kind in (("/app.js", "text/javascript"), ("/app.css", "text/css")):
                for label in ("none", "cross-site"):
                    reply = site.get(path, {"Sec-Fetch-Site": label})
                    self.assertEqual(reply.status, 200)
                    self.assertTrue(reply.headers["content-type"].startswith(kind), path)
                    self.assertNotIn("set-cookie", reply.headers)
            self.assertEqual(site.runtime.doc["workspaces"], [])

    def test_the_page_takes_only_get_and_head_and_a_head_starts_no_workspace(self):
        with Site() as site:
            for method in ("POST", "PUT", "DELETE", "OPTIONS", "PATCH"):
                for path in ("/", "/app.js", "/app.css"):
                    reply = site.request(method, path)
                    self.assertEqual(reply.status, 405, (method, path))
                    self.assertEqual(reply.headers["allow"], "GET, HEAD")
            for path in ("/", "/app.js", "/app.css"):
                reply = site.request("HEAD", path)
                self.assertEqual((reply.status, reply.body), (200, b""), path)
                self.assertGreater(int(reply.headers["content-length"]), 100)
                self.assertNotIn("set-cookie", reply.headers)
            self.assertEqual(site.runtime.doc["workspaces"], [])

    def test_every_answer_carries_the_security_headers_and_none_allows_another_origin(self):
        with Site() as site:
            browser = Browser(site).open()
            asked = [
                ("GET", "/", None), ("GET", "/app.js", None), ("GET", "/app.css", None),
                ("GET", "/healthz", None), ("GET", "/nothing", None), ("POST", "/", None),
                ("GET", "/api/state", None), ("GET", "/api/state", {"Cookie": browser.cookie}),
                ("GET", "/", {"Host": "evil.example"}), ("OPTIONS", "/api/invoke", None),
                ("POST", "/api/invoke", {"Content-Type": "text/plain"}),
                ("GET", f"/agent/{DASH}/tools", None),
            ]
            for method, path, headers in asked:
                reply = site.request(method, path, headers=headers)
                for name, value in SECURE.items():
                    self.assertEqual(reply.headers.get(name.lower()), value, (method, path, name))
                cors = [name for name in reply.headers if name.startswith("access-control-")]
                self.assertEqual(cors, [], (method, path))
            self.assertIn("script-src 'self'", SECURE["Content-Security-Policy"])
            self.assertIn("frame-ancestors 'none'", SECURE["Content-Security-Policy"])


class Healthz(unittest.TestCase):
    def test_it_answers_ok_to_any_host_any_site_and_no_cookie_and_sets_none(self):
        with Site() as site:
            for headers in ({}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Host": "x:1"}):
                reply = site.get("/healthz", headers)
                self.assertEqual((reply.status, reply.body), (200, b"ok\n"), headers)
                self.assertNotIn("set-cookie", reply.headers)
            self.assertEqual(site.request("HEAD", "/healthz").status, 200)
            self.assertEqual(site.request("POST", "/healthz", b"{}").status, 405)
            self.assertEqual(site.runtime.doc["workspaces"], [])


class TheHostGuard(unittest.TestCase):
    def routes(self, token):
        return [
            ("GET", "/"), ("GET", "/app.js"), ("GET", "/app.css"), ("GET", "/api/state"),
            ("GET", "/api/tools"), ("GET", "/api/calls"), ("GET", "/api/inquiry/prog_001-d1.txt"),
            ("POST", "/api/invoke"), ("GET", f"/agent/{token}/tools"), ("POST", f"/agent/{token}/invoke"),
            ("POST", f"/agent/{token}/mcp"),
        ]

    def test_every_route_but_healthz_refuses_a_name_that_is_not_this_servers(self):
        with Site() as site:
            browser = Browser(site).open()
            token = browser.agent_path().rsplit("/", 1)[1]
            for host in ("evil.example", "evil.example:%d" % site.port, "127.0.0.1", "127.0.0.1:1",
                         f"127.0.0.1:{site.port}.evil.example", "", "a b", "[::2]:%d" % site.port):
                for method, path in self.routes(token):
                    reply = site.request(method, path, b"{}" if method == "POST" else None, {
                        "Host": host, "Cookie": browser.cookie, "Sec-Fetch-Site": "same-origin",
                        "Content-Type": "application/json"})
                    if path.endswith("/mcp"):
                        self.assertEqual(reply.status, 403)
                    else:
                        refusal(self, reply, 403, "bad_host")

    def test_the_loopback_names_pass_on_the_bound_port_whatever_their_case(self):
        with Site() as site:
            for host in (f"127.0.0.1:{site.port}", f"localhost:{site.port}", f"LOCALHOST:{site.port}",
                         f"[::1]:{site.port}"):
                self.assertEqual(site.get("/app.css", {"Host": host}).status, 200, host)

    def test_an_allowed_name_passes_with_no_port_or_the_bound_one_and_gets_a_secure_cookie(self):
        with Site(extra_hosts=("demo.example",)) as site:
            for host in ("demo.example", f"demo.example:{site.port}", "DEMO.example"):
                reply = site.get("/", {"Host": host})
                self.assertEqual(reply.status, 200, host)
                self.assertTrue(reply.headers["set-cookie"].endswith("; Path=/; Secure"), host)
            self.assertEqual(site.get("/", {"Host": "demo.example:9"}).status, 403)
            self.assertEqual(site.get("/", {"Host": "other.example"}).status, 403)


class TheApiGuards(unittest.TestCase):
    def post(self, site, browser, label, **more):
        headers = {"Cookie": browser.cookie, "Origin": site.origin, **more}
        if label:
            headers["Sec-Fetch-Site"] = label
        return site.post("/api/invoke", {"tool": "qloo_budget", "args": {}}, headers)

    def test_a_same_site_or_cross_site_request_is_refused_on_every_api_route(self):
        with Site() as site:
            browser = Browser(site).open()
            for label in ("same-site", "cross-site", "bogus", ""):
                for path in API_GETS:
                    reply = site.get(path, {"Cookie": browser.cookie, "Sec-Fetch-Site": label})
                    refusal(self, reply, 403, "cross_site")
                refusal(self, self.post(site, browser, label or "bogus"), 403, "cross_site")

    def test_a_post_from_another_origin_is_refused_even_when_labelled_same_origin(self):
        with Site() as site:
            browser = Browser(site).open()
            for origin in ("https://evil.example", "null", f"http://127.0.0.1:{site.port + 1}",
                           f"https://127.0.0.1:{site.port}", "http://localhost:%d" % site.port):
                refusal(self, self.post(site, browser, "same-origin", Origin=origin), 403, "bad_origin")
            self.assertTrue(self.post(site, browser, "same-origin").json()["ok"])
            self.assertTrue(site.post("/api/invoke", {"tool": "qloo_budget"}, {
                "Cookie": browser.cookie, "Sec-Fetch-Site": "same-origin"}).json()["ok"])

    def test_with_no_cookie_every_api_route_is_401_whatever_the_label(self):
        with Site() as site:
            Browser(site).open()
            for label in ("same-origin", "none", None):
                headers = {"Sec-Fetch-Site": label} if label else {}
                for path in API_GETS:
                    refusal(self, site.get(path, headers), 401, "no_workspace")
                posted = site.post("/api/invoke", {"tool": "qloo_budget"}, {**headers, "Origin": site.origin})
                refusal(self, posted, 401, "no_workspace")
            for forged in (f"os_w=ws_{DASH}", "os_w=nonsense"):
                refusal(self, site.get("/api/state", {"Cookie": forged, "Sec-Fetch-Site": "same-origin"}),
                        401, "no_workspace")

    def test_an_unknown_route_is_404_and_the_wrong_method_is_405_with_the_one_allowed(self):
        with Site() as site:
            browser = Browser(site).open()
            refusal(self, browser.get("/api/nothing"), 404, "not_found")
            refusal(self, site.get("/agent/short/tools"), 404, "not_found")
            refusal(self, site.get("/api/invoke", {"Cookie": browser.cookie}), 405, "method_not_allowed")
            refusal(self, site.post("/api/state", {}, {"Cookie": browser.cookie}), 405, "method_not_allowed")
            self.assertEqual(site.get("/api/invoke").headers["allow"], "POST")
            self.assertEqual(site.post("/api/state", {}).headers["allow"], "GET")

    def test_the_query_string_is_ignored(self):
        with Site() as site:
            browser = Browser(site).open()
            self.assertEqual(browser.get("/api/tools?actor=person&x=1").status, 200)


class WhoIsAsking(unittest.TestCase):
    CASES = (
        ({"Sec-Fetch-Site": "same-origin"}, "person", "same_origin"),
        ({}, "agent", "no_fetch_metadata"),
        ({"Sec-Fetch-Site": "none"}, "agent", "typed_address"),
        ({"Sec-Fetch-Site": "same-origin", "X-Open-Slot-As": "agent"}, "agent", "agent_header"),
        ({"Sec-Fetch-Site": "same-origin", "X-Open-Slot-As": " AGENT "}, "agent", "agent_header"),
        ({"Sec-Fetch-Site": "none", "X-Open-Slot-As": "agent"}, "agent", "agent_header"),
        ({"X-Open-Slot-As": "agent"}, "agent", "agent_header"),
        ({"X-Open-Slot-As": "person"}, "agent", "no_fetch_metadata"),
        ({"Sec-Fetch-Site": "none", "X-Open-Slot-As": "person"}, "agent", "typed_address"),
    )

    def test_state_reports_the_actor_this_very_request_resolves_to_and_why(self):
        with Site() as site:
            cookie = Browser(site).open().cookie
            for headers, actor, why in self.CASES:
                shown = site.get("/api/state", {"Cookie": cookie, **headers}).json()
                self.assertEqual((shown["actor"], shown["actorWhy"]), (actor, why), headers)

    def test_the_agent_address_is_shown_only_to_the_person(self):
        with Site() as site:
            cookie = Browser(site).open().cookie
            for headers, actor, _ in self.CASES:
                shown = site.get("/api/state", {"Cookie": cookie, **headers}).json()
                if actor == "person":
                    self.assertRegex(shown["agentPath"], r"^/agent/[0-9a-f]{32}$")
                else:
                    self.assertIsNone(shown["agentPath"])
                    self.assertNotIn("/agent/", json.dumps(shown))

    def test_the_gate_over_http_a_person_confirms_and_nobody_else_does(self):
        with Site() as site:
            person = Browser(site).open()
            pid = person.sample()
            anchor = person.programme()["anchors"][0]
            confirm = {"tool": "confirm_anchor", "args": {
                "programme_id": pid, "name": anchor["name"],
                "entity_id": anchor["candidates"][0]["entityId"]}}
            weaker = (
                {},
                {"Sec-Fetch-Site": "none"},
                {"Sec-Fetch-Site": "same-origin", "X-Open-Slot-As": "agent"},
            )
            for extra in weaker:
                headers = {"Cookie": person.cookie, "Origin": site.origin, **extra}
                body = site.post("/api/invoke", confirm, headers).json()
                self.assertFalse(body["ok"], extra)
                outcome = "not_a_tool" if "X-Open-Slot-As" in extra else "wrong_actor"
                self.assertEqual(body["outcome"], outcome)
                self.assertIsNone(person.programme()["anchors"][0]["confirmed"])
            self.assertTrue(person.invoke(confirm["tool"], confirm["args"])["ok"])
            self.assertEqual(person.programme()["anchors"][0]["confirmed"],
                             anchor["candidates"][0]["entityId"])

    def test_every_human_verb_is_refused_to_a_request_that_is_not_the_person(self):
        with Site() as site:
            person = Browser(site).open()
            for verb in HUMAN:
                for extra in ({}, {"Sec-Fetch-Site": "none"}):
                    reply = site.post("/api/invoke", {"tool": verb, "args": {}}, {
                        "Cookie": person.cookie, "Origin": site.origin, **extra})
                    self.assertEqual(reply.json()["outcome"], "wrong_actor", (verb, extra))

    def test_a_body_that_names_an_actor_is_refused_and_the_actor_is_never_taken_from_it(self):
        with Site() as site:
            person = Browser(site, labelled=False).open()
            for body in ({"tool": "qloo_budget", "actor": "person"}, {"tool": "qloo_budget", "args": {},
                                                                      "surface": "page"}):
                headers = {"Cookie": person.cookie, "Origin": site.origin}
                refusal(self, site.post("/api/invoke", body, headers), 400, "bad_body")


class TheAgentAddress(unittest.TestCase):
    def test_it_lists_the_registry_and_no_verb_and_runs_a_tool_as_the_agent(self):
        with Site() as site:
            person = Browser(site).open()
            path = person.agent_path()
            listed = site.get(path + "/tools").json()["tools"]
            self.assertEqual([entry["name"] for entry in listed], list(tools.REGISTRY))
            for verb in HUMAN:
                self.assertNotIn(verb, json.dumps(listed))
            opened = site.post(path + "/invoke", {"tool": "open_programme", "args": person.state()["sample"]})
            self.assertTrue(opened.json()["ok"])
            self.assertEqual(person.programme()["label"], "The Lantern Room (fictional)")
            actors = {entry["actor"] for entry in person.state()["workspace"]["activityLog"]}
            self.assertEqual(actors, {"agent"})

    def test_a_human_verb_is_not_a_tool_there_even_with_the_cookie_and_the_page_labels(self):
        with Site() as site:
            person = Browser(site).open()
            pid = person.sample()
            before = person.programme()
            anchor = before["anchors"][0]
            path = person.agent_path()
            calls = {
                "confirm_anchor": {"programme_id": pid, "name": anchor["name"],
                                   "entity_id": anchor["candidates"][0]["entityId"]},
                "assign_slot": {"programme_id": pid, "night_id": "n1", "entity_id": None, "revision": 1},
                "approve_inquiry": {"programme_id": pid, "draft_id": "d1", "sender_name": "Maya",
                                    "revision": 1},
                "drop_act": {"programme_id": pid, "entity_id": "x", "reason": "no"},
                "raise_call_cap": {"programme_id": pid, "cap": 30},
            }
            self.assertEqual(set(calls), set(HUMAN))
            for verb, args in calls.items():
                for extra in ({}, {"Cookie": person.cookie, "Sec-Fetch-Site": "same-origin",
                                   "Origin": site.origin}):
                    body = site.post(path + "/invoke", {"tool": verb, "args": args}, extra).json()
                    self.assertEqual((body["ok"], body["outcome"]), (False, "not_a_tool"), (verb, extra))
            self.assertEqual(person.programme(), before)

    def test_it_reaches_the_workspace_its_token_names_and_no_other(self):
        with Site() as site:
            first, second = Browser(site).open(), Browser(site).open()
            pid = first.sample()
            theirs = site.post(second.agent_path() + "/invoke", {"tool": "get_programme",
                                                                   "args": {"programme_id": pid}}).json()
            self.assertEqual(theirs["outcome"], "unknown_programme")
            mine = site.post(first.agent_path() + "/invoke", {"tool": "get_programme",
                                                                "args": {"programme_id": pid}}).json()
            self.assertTrue(mine["ok"])
            crossed = site.post(first.agent_path() + "/invoke", {"tool": "get_programme", "args": {
                "programme_id": pid}}, {"Cookie": second.cookie, "Sec-Fetch-Site": "same-origin"}).json()
            self.assertTrue(crossed["ok"], "the token decides the workspace, not the cookie")

    def test_a_token_that_names_nothing_is_404_and_the_token_is_in_no_other_answer(self):
        with Site() as site:
            person = Browser(site).open()
            path = person.agent_path()
            token = path.rsplit("/", 1)[1]
            refusal(self, site.get(f"/agent/{DASH}/tools"), 404, "no_workspace")
            refusal(self, site.post(f"/agent/{DASH}/invoke", {"tool": "qloo_budget"}), 404, "no_workspace")
            pid = person.sample()
            person.confirm_all(pid)
            person.ok("plan_acts", {"programme_id": pid})
            seen = [site.get("/").text, site.get("/app.js").text, person.get("/api/tools").text,
                    person.get("/api/calls").text, site.get("/api/state", {"Cookie": person.cookie}).text,
                    site.get("/api/nothing").text, site.get(f"/agent/{DASH}/tools").text,
                    site.post(path + "/invoke", {"tool": "nothing"}).text,
                    json.dumps(person.programme()), json.dumps(site.runtime.doc["calls"])]
            for text in seen:
                self.assertNotIn(token, text)

    def test_the_invoke_body_is_a_tool_and_args_and_nothing_else(self):
        with Site() as site:
            path = Browser(site).open().agent_path()
            smuggled = {"tool": "qloo_budget", "actor": "person"}
            refusal(self, site.post(path + "/invoke", smuggled), 400, "bad_body")
            refusal(self, site.post(path + "/invoke", [1]), 400, "bad_body")
            refusal(self, site.post(path + "/invoke", b"nope"), 400, "bad_json")
            self.assertFalse(site.post(path + "/invoke", {"args": {}}).json()["ok"])
            self.assertEqual(site.post(path + "/invoke", {"tool": 7}).json()["outcome"], "not_a_tool")

    def test_a_cross_origin_request_to_the_address_is_refused_by_its_origin(self):
        with Site() as site:
            path = Browser(site).open().agent_path()
            refusal(self, site.get(path + "/tools", {"Origin": "https://evil.example"}), 403, "bad_origin")
            self.assertEqual(site.get(path + "/tools", {"Origin": site.origin}).status, 200)
            self.assertEqual(site.get(path + "/tools", {"Sec-Fetch-Site": "cross-site"}).status, 200)


class TheLimits(unittest.TestCase):
    def test_the_31st_invoke_in_a_minute_is_429_for_that_workspace_only(self):
        with Site() as site:
            first, second = Browser(site).open(), Browser(site).open()
            for _ in range(30):
                self.assertTrue(first.invoke("qloo_budget")["ok"])
            headers = {"Cookie": first.cookie, "Sec-Fetch-Site": "same-origin", "Origin": site.origin}
            limited = site.post("/api/invoke", {"tool": "qloo_budget"}, headers)
            refusal(self, limited, 429, "rate_limited")
            self.assertIn("retry-after", limited.headers)
            self.assertTrue(second.invoke("qloo_budget")["ok"], "one peer address, two buckets")
            site.ticks[0] += 61
            self.assertTrue(first.invoke("qloo_budget")["ok"])

    def test_the_agent_address_has_its_own_bucket_of_30_a_minute(self):
        with Site() as site:
            person = Browser(site).open()
            path = person.agent_path()
            for _ in range(30):
                self.assertEqual(site.get(path + "/tools").status, 200)
            refusal(self, site.get(path + "/tools"), 429, "rate_limited")
            refusal(self, site.post(path + "/invoke", {"tool": "qloo_budget"}), 429, "rate_limited")
            self.assertTrue(person.invoke("qloo_budget")["ok"])
            site.ticks[0] += 61
            self.assertEqual(site.get(path + "/tools").status, 200)

    def test_only_the_invoke_routes_are_counted_so_the_page_can_read_freely(self):
        with Site() as site:
            person = Browser(site).open()
            for _ in range(45):
                self.assertEqual(person.get("/api/calls").status, 200)
                self.assertEqual(person.get("/api/state").status, 200)

    def test_without_a_proxy_every_creation_counts_for_the_peer_and_x_forwarded_for_is_ignored(self):
        with Site(trust_proxy=False) as site:
            for number in range(30):
                self.assertEqual(site.get("/", {"X-Forwarded-For": f"10.0.0.{number}"}).status, 200)
            busy = site.get("/", {"X-Forwarded-For": "10.9.9.9"})
            self.assertEqual(busy.status, 429)
            self.assertIn("busy", busy.text.lower())
            self.assertIn("retry-after", busy.headers)
            self.assertNotIn("set-cookie", busy.headers)
            site.ticks[0] += 61
            self.assertEqual(site.get("/").status, 200)

    def test_behind_a_proxy_the_right_most_value_is_the_client_and_a_spoofed_one_to_its_left_is_not(self):
        with Site(trust_proxy=True) as site:
            for _ in range(30):
                self.assertEqual(site.get("/", {"X-Forwarded-For": "203.0.113.1"}).status, 200)
            self.assertEqual(site.get("/", {"X-Forwarded-For": "203.0.113.1"}).status, 429)
            self.assertEqual(site.get("/", {"X-Forwarded-For": "198.51.100.9, 203.0.113.1"}).status, 429)
            self.assertEqual(site.get("/", {"X-Forwarded-For": "203.0.113.1, 203.0.113.2"}).status, 200)
            self.assertEqual(site.get("/", {"X-Forwarded-For": "2001:db8::1"}).status, 200)

    def test_a_proxy_header_that_is_not_an_address_falls_back_to_the_peer(self):
        with Site(trust_proxy=True) as site:
            for _ in range(30):
                self.assertEqual(site.get("/", {"X-Forwarded-For": "not-an-address"}).status, 200)
            self.assertEqual(site.get("/").status, 429, "the peer's own bucket is the one that filled")

    def test_the_total_creation_limit_holds_whoever_asks(self):
        with Site(trust_proxy=True) as site:
            for client in range(4):
                for _ in range(30):
                    self.assertEqual(site.get("/", {"X-Forwarded-For": f"203.0.113.{client}"}).status, 200)
            self.assertEqual(site.get("/", {"X-Forwarded-For": "203.0.113.99"}).status, 429)
            site.ticks[0] += 61
            self.assertEqual(site.get("/", {"X-Forwarded-For": "203.0.113.99"}).status, 200)

    def test_one_client_flooding_does_not_use_up_the_total_for_everyone_else(self):
        with Site(trust_proxy=True) as site:
            for _ in range(200):
                site.get("/", {"X-Forwarded-For": "203.0.113.1"})
            self.assertEqual(site.get("/", {"X-Forwarded-For": "203.0.113.2"}).status, 200)

    def test_state_says_which_key_the_limit_counts_by(self):
        with Site(trust_proxy=True) as site:
            cookie = Browser(site).open().cookie
            forwarded = site.get("/api/state", {"Cookie": cookie, "X-Forwarded-For": "203.0.113.5"}).json()
            self.assertEqual(forwarded["limiterKey"], "forwarded")
            self.assertEqual(site.get("/api/state", {"Cookie": cookie}).json()["limiterKey"], "peer")
        with Site(trust_proxy=False) as site:
            cookie = Browser(site).open().cookie
            shown = site.get("/api/state", {"Cookie": cookie, "X-Forwarded-For": "203.0.113.5"}).json()
            self.assertEqual(shown["limiterKey"], "peer")

    def test_workspaces_idle_for_two_hours_are_forgotten_and_a_call_keeps_one_alive(self):
        with Site() as site:
            kept, lost = Browser(site).open(), Browser(site).open()
            token = lost.agent_path().rsplit("/", 1)[1]
            site.clock.advance(3600)
            self.assertTrue(kept.invoke("qloo_budget")["ok"])
            site.clock.advance(3601)
            refusal(self, lost.get("/api/state"), 401, "no_workspace")
            refusal(self, site.get(f"/agent/{token}/tools"), 404, "no_workspace")
            self.assertEqual(kept.get("/api/state").status, 200)
            self.assertIn("set-cookie", site.get("/", {"Cookie": lost.cookie}).headers)
            self.assertEqual(len(site.runtime.doc["workspaces"]), 2)


class TheBody(unittest.TestCase):
    def setUp(self):
        self.site = Site()
        self.site.__enter__()
        self.addCleanup(self.site.__exit__, None, None, None)
        self.cookie = Browser(self.site).open().cookie

    def send(self, body, **headers):
        return self.site.request("POST", "/api/invoke", body, {
            "Cookie": self.cookie, "Sec-Fetch-Site": "same-origin", "Origin": self.site.origin,
            "Content-Type": "application/json", **headers})

    def test_only_json_is_taken_and_a_charset_parameter_is_fine(self):
        body = json.dumps({"tool": "qloo_budget"}).encode()
        for kind in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""):
            refusal(self, self.send(body, **{"Content-Type": kind}), 415, "bad_content_type")
        for kind in ("application/json", "application/json; charset=utf-8", "Application/JSON"):
            self.assertTrue(self.send(body, **{"Content-Type": kind}).json()["ok"], kind)

    def test_a_body_over_64_kib_is_refused_without_being_run(self):
        big = json.dumps({"tool": "qloo_budget", "args": {"x": "a" * 70000}}).encode()
        refusal(self, self.send(big), 413, "too_large")
        self.assertEqual(self.site.runtime.doc["workspaces"][0]["activityLog"], [])
        edge = json.dumps({"tool": "qloo_budget", "args": {"x": "a" * 60000}}).encode()
        self.assertLess(len(edge), server.BODY_MAX)
        self.assertEqual(self.send(edge).json()["outcome"], "smuggled_field")

    def test_a_body_that_is_not_json_or_not_an_object_is_400_never_a_trace(self):
        nested = b"[" * 30000 + b"]" * 30000
        for body, code in ((b"{", "bad_json"), (b"\xff\xfe", "bad_json"), (b"", "bad_json"),
                           (nested, "bad_json"), (b"[]", "bad_body"), (b'"tool"', "bad_body"),
                           (b'{"tool": "x", "extra": 1}', "bad_body")):
            reply = self.send(body)
            refusal(self, reply, 400, code)
            self.assertNotIn("Traceback", reply.text)

    def test_a_chunked_body_has_no_length_to_trust_and_is_refused(self):
        site = self.site
        raw = (f"POST /api/invoke HTTP/1.1\r\nHost: 127.0.0.1:{site.port}\r\nCookie: {self.cookie}\r\n"
               "Sec-Fetch-Site: same-origin\r\nContent-Type: application/json\r\n"
               f"Origin: {site.origin}\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{{}}\r\n0\r\n\r\n")
        answer = exchange(site, raw.encode())
        self.assertTrue(answer.startswith(b"HTTP/1.0 411 "), answer[:40])
        self.assertIn(b'"length_required"', answer)

    def test_a_request_the_socket_layer_cannot_parse_still_gets_a_json_answer_with_the_headers(self):
        with Site() as site:
            long_target = b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\n\r\n"
            headers = b"".join(b"X-%d: 1\r\n" % n for n in range(150))
            many = b"GET / HTTP/1.1\r\nHost: x\r\n" + headers + b"\r\n"
            for raw, status in ((long_target, b"414"), (many, b"431")):
                answer = exchange(site, raw)
                self.assertTrue(answer.startswith(b"HTTP/1.0 " + status), answer[:40])
                self.assertIn(b"Content-Security-Policy", answer)
                self.assertIn(b'"bad_request"', answer)
                self.assertNotIn(b"<html", answer.lower())

    def test_the_request_log_is_silent_so_no_token_or_name_is_ever_written(self):
        with Site() as site:
            errors, out = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(out):
                person = Browser(site).open()
                pid = person.sample()
                site.get(person.agent_path() + "/tools")
                site.get("/nothing")
                site.get("/", {"Host": "evil.example"})
                person.ok("get_programme", {"programme_id": pid})
            self.assertEqual((errors.getvalue(), out.getvalue()), ("", ""))

    def test_an_unexpected_failure_is_a_fixed_500_that_names_no_value(self):
        with Site() as site:
            person = Browser(site).open()
            errors = io.StringIO()
            with mock.patch.object(invoke, "invoke", side_effect=ValueError("secret text 12345")), \
                    contextlib.redirect_stderr(errors):
                reply = site.post("/api/invoke", {"tool": "qloo_budget"}, {
                    "Cookie": person.cookie, "Sec-Fetch-Site": "same-origin", "Origin": site.origin})
            refusal(self, reply, 500, "internal")
            self.assertNotIn("secret", reply.text)
            self.assertEqual(errors.getvalue(), "open-slot: internal error (ValueError)\n")


class TheStateAndTheCalls(unittest.TestCase):
    def test_state_holds_the_mode_the_allowances_the_sample_and_never_the_workspace_id(self):
        with Site() as site:
            person = Browser(site).open()
            shown = person.state()
            self.assertEqual(shown["server"], {"provider": "fake", "mode": "synthetic", "key": "missing"})
            self.assertEqual(shown["sample"]["label"], "The Lantern Room (fictional)")
            self.assertEqual(shown["limits"], {"callCap": 16, "callCapMax": 40})
            self.assertEqual(shown["allowances"]["daily"]["limit"], 600)
            self.assertEqual(shown["allowances"]["agent"], {"limit": 48, "used": 0, "windowHours": 24})
            self.assertEqual(shown["workspace"]["programmes"], [])
            self.assertEqual(shown["workspace"]["idleTtlSeconds"], 7200)
            self.assertNotIn(person.cookie.split("=")[1], json.dumps(shown))
            self.assertNotIn("agentToken", json.dumps(shown))

    def test_calls_lists_only_this_workspaces_calls_by_an_opaque_label_and_never_a_name(self):
        with Site() as site:
            first, second = Browser(site).open(), Browser(site).open()
            first.sample()
            body = first.get("/api/calls").json()
            self.assertEqual(body["total"], 4)
            self.assertEqual([call["path"] for call in body["calls"]], ["/search"] * 4)
            self.assertEqual(sorted(body["calls"][0]), ["attempts", "cache", "ms", "path", "provider",
                                                        "startedBy", "status", "ts"])
            self.assertEqual(second.get("/api/calls").json()["total"], 0)
            for name in ("Milk Carton", "Alvvays", "Bamford", "Hodgman"):
                self.assertNotIn(name, json.dumps(body))

    def test_calls_answers_while_a_plan_runs_and_state_waits_for_it(self):
        gated = Gated()
        with Site(engine=gated) as site:
            person = Browser(site).open()
            pid = person.sample()
            person.confirm_all(pid)
            running = []
            plan = threading.Thread(target=lambda: running.append(person.invoke("plan_acts", {
                "programme_id": pid})), daemon=True)
            plan.start()
            self.assertTrue(gated.reached.wait(10))
            self.assertEqual(person.get("/api/calls").json()["total"], 5, "four searches and one wide call")
            waiting = []
            reader = threading.Thread(target=lambda: waiting.append(person.get("/api/state")), daemon=True)
            reader.start()
            reader.join(0.4)
            self.assertTrue(reader.is_alive(), "state is read under the lock")
            gated.release.set()
            plan.join(10)
            reader.join(10)
            self.assertTrue(running[0]["ok"])
            self.assertEqual(waiting[0].status, 200)
            self.assertEqual(person.get("/api/calls").json()["total"], 12)

    def test_an_approved_inquiry_is_a_text_download_and_a_draft_is_404(self):
        with Site() as site:
            person = Browser(site).open()
            pid = person.sample()
            person.confirm_all(pid)
            person.ok("plan_acts", {"programme_id": pid})
            entity = person.programme()["view"]["board"][0]["entity_id"]
            draft = person.ok("draft_inquiry", {"programme_id": pid, "entity_id": entity})["draft_id"]
            address = f"/api/inquiry/{pid}-{draft}.txt"
            refusal(self, person.get(address), 404, "not_found")
            person.ok("approve_inquiry", {"programme_id": pid, "draft_id": draft, "sender_name": "Maya",
                                          "revision": person.programme()["revision"]})
            reply = person.get(address)
            self.assertEqual(reply.status, 200)
            self.assertTrue(reply.headers["content-type"].startswith("text/plain"))
            self.assertEqual(reply.headers["content-disposition"],
                             f'attachment; filename="inquiry-{pid}-{draft}.txt"')
            self.assertNotIn("DRAFT", reply.text)
            self.assertTrue(reply.text.rstrip().endswith("Maya"))
            refusal(self, person.get("/api/inquiry/prog_009-d1.txt"), 404, "not_found")
            refusal(self, person.get(f"/api/inquiry/{pid}-d9.txt"), 404, "not_found")


class TheConnectionCap(unittest.TestCase):
    """At most `connections` handler threads: the next connection is answered 503 on the accepting thread."""

    def test_connections_past_the_cap_are_answered_busy_and_the_server_recovers_when_they_close(self):
        with Site(connections=2) as site:
            holding = [socket.create_connection(("127.0.0.1", site.port), timeout=10) for _ in range(2)]
            try:
                for _ in range(5):
                    reply = site.get("/healthz")
                    self.assertEqual(reply.status, 503)
                    self.assertEqual(reply.headers["retry-after"], "5")
                    self.assertEqual(reply.json()["outcome"], "busy")
                    self.assertEqual(reply.headers["cache-control"], "no-store")
                    self.assertEqual(reply.headers["connection"], "close")
            finally:
                for held in holding:
                    held.close()
            for _ in range(100):
                if site.get("/healthz").status == 200:
                    break
                time.sleep(0.05)
            self.assertEqual(site.get("/healthz").text, "ok\n")
            self.assertEqual(site.get("/healthz").status, 200)

    def test_a_slow_plan_holds_one_slot_and_the_rest_still_answer(self):
        gated = Gated()
        with Site(engine=gated, connections=3) as site:
            person = Browser(site).open()
            pid = person.sample()
            person.confirm_all(pid)
            plan = threading.Thread(target=lambda: person.invoke("plan_acts", {"programme_id": pid}),
                                    daemon=True)
            plan.start()
            self.assertTrue(gated.reached.wait(10))
            self.assertEqual(site.get("/healthz").status, 200)
            self.assertEqual(person.get("/api/calls").status, 200)
            gated.release.set()
            plan.join(10)

    def test_the_cap_is_in_force_by_default_and_a_turned_away_connection_leaks_no_slot(self):
        self.assertEqual(server.MAX_CONNECTIONS, 64)
        with Site(connections=1) as site:
            holding = socket.create_connection(("127.0.0.1", site.port), timeout=10)
            for _ in range(10):
                self.assertEqual(site.get("/healthz").status, 503)
            holding.close()
            for _ in range(100):
                if site.get("/healthz").status == 200:
                    break
                time.sleep(0.05)
            for _ in range(3):
                self.assertEqual(site.get("/healthz").status, 200)


class TheFlowThePageRuns(unittest.TestCase):
    """The page's own calls, one by one: the room form, the matches, the confirmations, the run, the cards."""

    def test_room_form_to_confirm_to_run_to_cards_on_the_synthetic_provider(self):
        with Site() as site:
            self.assertIn(b'id="room-form"', site.get("/").body)
            person = Browser(site).open()
            shown = person.state()
            self.assertEqual((shown["actor"], shown["actorWhy"]), ("person", "same_origin"))
            opened = person.ok("open_programme", shown["sample"])
            pid = opened["programme_id"]
            self.assertEqual([night["label"] for night in opened["nights"]],
                             ["Tue 3 Nov", "Tue 10 Nov", "Tue 17 Nov"])
            for name in opened["anchors"]:
                self.assertTrue(person.ok("find_anchor", {"programme_id": pid, "name": name})["candidates"])
            programme = person.programme()
            self.assertTrue(all(anchor["candidates"] and anchor["confirmed"] is None
                                for anchor in programme["anchors"]))
            refused = person.invoke("plan_acts", {"programme_id": pid})
            self.assertEqual(refused["outcome"], "anchor_unconfirmed")
            self.assertEqual(person.programme()["candidates"], [])

            person.confirm_all(pid)
            planned = person.ok("plan_acts", {"programme_id": pid})
            self.assertEqual(planned["stopped_because"], "enough_ready")
            self.assertEqual(planned["calls"]["spent_in_total"], 12)

            programme = person.programme()
            view = programme["view"]
            self.assertEqual([card["status"] for card in view["board"]], ["ready"] * 4 + ["watch"] * 4)
            self.assertEqual(len(view["set_aside"]), 26)
            card = view["board"][0]
            self.assertEqual(card["popularity"]["source"], "Qloo's popularity percentile (0 to 1)")
            self.assertTrue(card["links"] and all(link["source"] == "Qloo's explanation score"
                                                  for link in card["links"]))
            self.assertIn("this app's rule", card["trend"]["source"])
            self.assertTrue(card["ranked_by_text"])
            self.assertEqual(programme["trace"][-1]["kind"], "stop")
            self.assertEqual(person.get("/api/calls").json()["total"], 12)
            log = person.state()["workspace"]["activityLog"]
            confirmers = {entry["actor"] for entry in log if entry["tool"] == "confirm_anchor"}
            self.assertEqual(confirmers, {"person"})

    def test_the_same_tools_are_reached_by_a_click_and_by_the_agent_address(self):
        with Site() as site:
            person = Browser(site).open()
            pid = person.sample()
            person.confirm_all(pid)
            planned = person.ok("plan_acts", {"programme_id": pid})
            entity = planned["board"][0]["entity_id"]
            by_click = person.ok("explain_act", {"programme_id": pid, "entity_id": entity})
            by_agent = site.post(person.agent_path() + "/invoke", {"tool": "explain_act", "args": {
                "programme_id": pid, "entity_id": entity}}).json()["result"]
            self.assertEqual(by_click, by_agent)


class TheKeyStaysOnTheServer(unittest.TestCase):
    def test_a_fake_key_that_qloo_echoes_back_reaches_no_answer_the_browser_can_read(self):
        key = fake_key()
        def answered(request):
            return 401, {"success": False, "message": f"bad key {key}"}, {}

        with Stub(answered) as stub:
            def engine(ledger):
                return providers.LiveProvider(key, ledger, stub.base_url, sleep=lambda seconds: None)

            with Site(engine=engine, key=key, provider="live") as site:
                person = Browser(site).open()
                shown = person.state()
                self.assertEqual(shown["server"], {"provider": "live", "mode": "live", "key": "set"})
                pid = person.ok("open_programme", shown["sample"])["programme_id"]
                refused = person.invoke("find_anchor", {"programme_id": pid, "name": "Alvvays"})
                self.assertEqual(refused["outcome"], "qloo_auth")
                answers = [site.get("/").text, site.get("/app.js").text, refused, person.state(),
                           person.get("/api/calls").text, person.get("/api/tools").text,
                           person.invoke("get_programme", {"programme_id": pid}),
                           person.invoke("qloo_budget", {"programme_id": pid}),
                           site.get("/api/state").text, site.get("/nothing").text]
                for answer in answers:
                    self.assertNotIn(key, answer if isinstance(answer, str) else json.dumps(answer))
                self.assertNotIn(key, json.dumps(site.runtime.doc))
                self.assertEqual(len(stub.requests), 1)
                self.assertEqual(stub.requests[0]["headers"]["x-api-key"], key)


if __name__ == "__main__":
    unittest.main()
