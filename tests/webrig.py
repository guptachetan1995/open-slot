import http.client
import json
import threading
from datetime import timedelta

from open_slot import invoke, providers, server
from tests.rig import NOW, scenario, settings

HUMAN = ("confirm_anchor", "assign_slot", "approve_inquiry", "drop_act", "raise_call_cap")


class Reply:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)

    @property
    def text(self):
        return self.body.decode("utf-8")


class Clock:
    """The runtime's wall clock, which a test moves to age a workspace."""

    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Site:
    """A real server on an ephemeral loopback port, over the synthetic provider unless given another.
    `ticks` is the clock the rate limits count by, so a test can move a minute without waiting."""

    def __init__(self, engine=None, sample=None, connections=server.MAX_CONNECTIONS, fallback=None,
                 **changes):
        self.settings = settings(**changes)
        self.clock = Clock()
        self.ticks = [0.0]
        self.ledger = providers.Ledger(self.settings.daily_live_calls, self.settings.agent_calls_per_day,
                                       self.settings.cache_ttl_s)
        if engine is None:
            engine = providers.FakeProvider()
        elif callable(engine):
            engine = engine(self.ledger)
        self.runtime = invoke.Runtime(self.settings, engine, self.ledger, clock=self.clock, fallback=fallback)
        self.app = server.App(self.settings, self.runtime, sample or scenario("lantern-room"),
                              clock=lambda: self.ticks[0])
        self.httpd = server.make_server(self.app, "127.0.0.1", 0, connections)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01),
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            reply = connection.getresponse()
            return Reply(reply.status, {k.lower(): v for k, v in reply.getheaders()}, reply.read())
        finally:
            connection.close()

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, payload, headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return self.request("POST", path, body, {"Content-Type": "application/json", **(headers or {})})


class Browser:
    """What a current browser does on the page: it keeps the cookie, labels the page's own requests
    `same-origin` and sends the page's Origin with a POST. `labelled=False` is a browser that sends no
    request label."""

    def __init__(self, site, labelled=True, extra=None):
        self.site, self.labelled, self.extra = site, labelled, extra or {}
        self.cookie = None

    def _headers(self, post):
        headers = dict(self.extra)
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.labelled:
            headers["Sec-Fetch-Site"] = "same-origin"
        if post:
            headers["Origin"] = self.site.origin
        return headers

    def open(self):
        reply = self.site.get("/", {"Sec-Fetch-Site": "none", **self.extra})
        assert reply.status == 200, reply.text
        self.cookie = reply.headers["set-cookie"].split(";")[0]
        return self

    def get(self, path):
        return self.site.get(path, self._headers(False))

    def state(self):
        reply = self.get("/api/state")
        assert reply.status == 200, reply.text
        return reply.json()

    def invoke(self, tool, args=None):
        return self.site.post("/api/invoke", {"tool": tool, "args": args or {}}, self._headers(True)).json()

    def ok(self, tool, args=None):
        envelope = self.invoke(tool, args)
        assert envelope["ok"], envelope
        return envelope["result"]

    def agent_path(self):
        return self.state()["agentPath"]

    def programme(self):
        return self.state()["workspace"]["programmes"][-1]

    def sample(self):
        """The page's own first steps: open the sample programme and search each act."""
        opened = self.ok("open_programme", self.state()["sample"])
        for name in opened["anchors"]:
            self.ok("find_anchor", {"programme_id": opened["programme_id"], "name": name})
        return opened["programme_id"]

    def confirm_all(self, programme_id):
        for anchor in self.programme()["anchors"]:
            self.ok("confirm_anchor", {"programme_id": programme_id, "name": anchor["name"],
                                       "entity_id": anchor["candidates"][0]["entityId"]})
