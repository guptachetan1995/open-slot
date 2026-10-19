"""The HTTP front: the workspace cookie, the agent address and the per-route guards.

App.handle is one function from a request to a response. The http.server classes under it only
read the socket and write the answer, so the whole contract is testable with no socket at all
and, over a real one, with nothing mocked.

Who is asking decides what may run. A request is the person only when it carries the workspace
cookie and the browser labels it as the page's own (`Sec-Fetch-Site: same-origin`); anything
weaker is downgraded to the agent, and nothing a client sends upgrades it. The agent address
carries its own token, always acts as the agent and reaches one workspace.
"""

import hmac
import ipaddress
import json
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer

from . import config, invoke, mcp, store, tools
from .providers import AGENT, PERSON, workspace_label

WEB = Path(__file__).resolve().parent.parent / "web"
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
COOKIE = "os_w"
BODY_MAX = 64 * 1024
DRAIN_MAX = 1024 * 1024
INVOKES_PER_MINUTE = 30
CREATES_PER_MINUTE_PER_KEY = 30
CREATES_PER_MINUTE = 120
CALLS_SHOWN = 40
SOCKET_TIMEOUT_S = 15
MAX_CONNECTIONS = 64

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

HOST = re.compile(r"(?P<name>\[[0-9a-f:.]+\]|[a-z0-9][a-z0-9.-]*)(?::(?P<port>[0-9]{1,5}))?")
WORKSPACE = re.compile(r"ws_[0-9a-f]{32}")
AGENT_PATH = re.compile(r"/agent/(?P<token>[0-9a-f]{32})/(?P<name>tools|invoke|mcp)")
INQUIRY_PATH = re.compile(r"/api/inquiry/(?P<programme>prog_[0-9]+)-(?P<draft>d[0-9]+)\.txt")
API_ROUTES = {"/api/state": "state", "/api/tools": "tools", "/api/calls": "calls", "/api/invoke": "invoke"}

REJECTIONS = {
    "bad_request": "The request could not be understood.",
    "bad_host": "This server does not answer to that Host.",
    "cross_site": "This request came from another site.",
    "bad_origin": "This request's Origin is not this server's.",
    "no_workspace": "There is no workspace for this request. Open the page first.",
    "rate_limited": invoke.MESSAGES["rate_limited"],
    "busy": "The server is busy; try again in a minute.",
    "not_found": "Not found.",
    "method_not_allowed": "That method is not allowed here.",
    "length_required": "The request needs a plain Content-Length.",
    "too_large": "The body is over 64 KiB.",
    "bad_content_type": "The body must be application/json.",
    "bad_json": "The body is not JSON.",
    "bad_body": "The body must be an object with tool and args, and nothing else.",
    "internal": "Something went wrong on the server.",
}

BUSY_PAGE = (
    b"<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>Open Slot</title>"
    b"<body><p>Open Slot is busy. Try again in a minute.</p></body></html>"
)


@dataclass
class Request:
    """`headers` are lower-cased. `length` is the declared body length, -1 when it cannot be trusted;
    the shell reads a body only when it is within BODY_MAX."""

    method: str
    target: str
    headers: dict
    body: bytes = b""
    length: int = None
    peer: str = "127.0.0.1"

    def __post_init__(self):
        if self.length is None:
            self.length = len(self.body)


@dataclass
class Response:
    status: int
    body: bytes
    type: str
    headers: dict = field(default_factory=dict)

    @classmethod
    def json(cls, payload, status=200, headers=None):
        return cls(status, json.dumps(payload).encode("utf-8"), "application/json", headers or {})


@dataclass(frozen=True)
class Host:
    text: str
    name: str
    loopback: bool

    @property
    def origin(self):
        return f"{'http' if self.loopback else 'https'}://{self.text}"


class Reject(Exception):
    def __init__(self, status, code, headers=None):
        super().__init__(code)
        self.status, self.code, self.headers = status, code, headers or {}


class Window:
    """At most `limit` hits per key in any `seconds`."""

    def __init__(self, limit, clock, seconds=60.0):
        self.limit, self.clock, self.seconds = limit, clock, seconds
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        with self._lock:
            now = self.clock()
            if len(self._hits) > 4096:
                self._hits = {k: q for k, q in self._hits.items() if q and q[-1] > now - self.seconds}
            queue = self._hits.setdefault(key, deque())
            while queue and queue[0] <= now - self.seconds:
                queue.popleft()
            if len(queue) >= self.limit:
                return False
            queue.append(now)
            return True


class App:
    def __init__(self, settings, runtime, sample=None, clock=time.monotonic):
        self.settings, self.runtime, self.sample = settings, runtime, sample
        self.port = settings.port
        self.invokes = Window(INVOKES_PER_MINUTE, clock)
        self.agents = Window(INVOKES_PER_MINUTE, clock)
        self.creates = Window(CREATES_PER_MINUTE_PER_KEY, clock)
        self.creates_all = Window(CREATES_PER_MINUTE, clock)

    def handle(self, request):
        path = request.target.split("?", 1)[0]
        over_rpc = AGENT_PATH.fullmatch(path) is not None and path.endswith("/mcp")
        try:
            response = self._route(request, path)
        except Reject as refused:
            response = self._rejection(refused, over_rpc)
        except Exception as failure:  # the boundary: anything else is a fixed answer, never a trace
            sys.stderr.write(f"open-slot: internal error ({type(failure).__name__})\n")
            response = self._rejection(Reject(500, "internal"), over_rpc)
        return self._secured(response)

    def malformed(self, status):
        """The answer for a request the socket layer could not parse."""
        return self._secured(self._rejection(Reject(status, "bad_request"), False))

    def overloaded(self):
        """The answer for a connection that arrives when every handler thread is taken."""
        return self._secured(self._rejection(Reject(503, "busy", {"Retry-After": "5"}), False))

    @staticmethod
    def _secured(response):
        return Response(response.status, response.body, response.type,
                        {**SECURITY_HEADERS, **response.headers})

    @staticmethod
    def _rejection(refused, over_rpc):
        message = REJECTIONS[refused.code]
        if over_rpc:
            payload = mcp.refusal(refused.code, message)
        else:
            payload = {"ok": False, "outcome": refused.code,
                       "error": {"code": refused.code, "message": message}}
        return Response.json(payload, refused.status, refused.headers)

    def _route(self, request, path):
        if not path.startswith("/"):
            raise Reject(400, "bad_request")
        if path == "/healthz":
            return self._healthz(request)
        host = self._host(request)
        if path in PAGES:
            return self._page(request, path, host)
        if path.startswith("/api/"):
            return self._api(request, path, host)
        if path.startswith("/agent/"):
            return self._agent(request, path, host)
        raise Reject(404, "not_found")

    @staticmethod
    def _healthz(request):
        if request.method not in ("GET", "HEAD"):
            raise Reject(405, "method_not_allowed", {"Allow": "GET, HEAD"})
        return Response(200, b"ok\n", "text/plain; charset=utf-8")

    def _host(self, request):
        """The Host header if it names this server: a loopback name on the bound port, or an allowed
        name on no port (a proxy's public name) or the bound port. Anything else is refused, which is
        what stops a DNS-rebinding page from reaching a server bound to loopback."""
        text = request.headers.get("host", "").lower()
        found = HOST.fullmatch(text)
        if found is None:
            raise Reject(403, "bad_host")
        name, port = found["name"], found["port"]
        loopback = name in config.LOOPBACK_HOSTS
        if loopback:
            named = port == str(self.port)
        else:
            named = name in self.settings.extra_hosts and port in (None, str(self.port))
        if not named:
            raise Reject(403, "bad_host")
        return Host(text, name, loopback)

    @staticmethod
    def _actor(request):
        site = request.headers.get("sec-fetch-site")
        if site not in (None, "same-origin", "none"):
            raise Reject(403, "cross_site")
        if request.headers.get("x-open-slot-as", "").strip().lower() == AGENT:
            return AGENT, "agent_header"
        if site is None:
            return AGENT, "no_fetch_metadata"
        if site == "none":
            return AGENT, "typed_address"
        return PERSON, "same_origin"

    @staticmethod
    def _same_origin(request, host):
        origin = request.headers.get("origin")
        if origin is not None and origin.lower() != host.origin:
            raise Reject(403, "bad_origin")

    def _client(self, request):
        """The key a creation limit counts by, and which kind it is: the right-most X-Forwarded-For
        value (the hop the proxy appended, never one to its left, which a client can write) when a
        proxy is trusted, else the TCP peer."""
        if self.settings.trust_proxy:
            last = request.headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
            try:
                return str(ipaddress.ip_address(last)), "forwarded"
            except ValueError:
                pass
        return request.peer, "peer"

    def _alive(self, workspace_id):
        try:
            found = store.workspace(self.runtime.doc, workspace_id)
        except store.Refusal:
            return False
        idle = (self.runtime.clock() - datetime.fromisoformat(found["lastSeenAt"])).total_seconds()
        return idle < self.settings.workspace_ttl_s

    def _cookie_workspace(self, request):
        for part in request.headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and WORKSPACE.fullmatch(value) and self._alive(value):
                return value
        return None

    def _token_workspace(self, token):
        for found in self.runtime.doc["workspaces"]:
            if hmac.compare_digest(found["agentToken"], token) and self._alive(found["id"]):
                return found["id"]
        return None

    def _new_workspace(self, request):
        key, _ = self._client(request)
        if not (self.creates.allow(key) and self.creates_all.allow("all")):
            return None
        self.runtime.prune(self.settings.workspace_ttl_s)
        return self.runtime.new_workspace()

    def _page(self, request, path, host):
        if request.method not in ("GET", "HEAD"):
            raise Reject(405, "method_not_allowed", {"Allow": "GET, HEAD"})
        name, kind = PAGES[path]
        page = (WEB / name).read_bytes()
        headers = {}
        if request.method == "GET" and path == "/" and self._cookie_workspace(request) is None:
            workspace_id = self._new_workspace(request)
            if workspace_id is None:
                return Response(429, BUSY_PAGE, "text/html; charset=utf-8", {"Retry-After": "60"})
            secure = "" if host.loopback else "; Secure"
            headers["Set-Cookie"] = f"{COOKIE}={workspace_id}; HttpOnly; SameSite=Strict; Path=/{secure}"
        return Response(200, page, kind, headers)

    def _api(self, request, path, host):
        inquiry = INQUIRY_PATH.fullmatch(path)
        route = "inquiry" if inquiry else API_ROUTES.get(path)
        if route is None:
            raise Reject(404, "not_found")
        allowed = "POST" if route == "invoke" else "GET"
        if request.method != allowed:
            raise Reject(405, "method_not_allowed", {"Allow": allowed})
        actor, why = self._actor(request)
        if route == "invoke":
            self._same_origin(request, host)
        workspace_id = self._cookie_workspace(request)
        if workspace_id is None:
            raise Reject(401, "no_workspace")
        if route == "invoke":
            if not self.invokes.allow(workspace_id):
                raise Reject(429, "rate_limited", {"Retry-After": "30"})
            return self._invoke(request, workspace_id, actor, "agent" if why == "agent_header" else "page")
        if route == "state":
            return self._state(request, workspace_id, actor, why)
        if route == "tools":
            return Response.json({"tools": tools.listing()})
        if route == "calls":
            return self._calls(workspace_id)
        return self._inquiry(workspace_id, inquiry["programme"], inquiry["draft"])

    def _agent(self, request, path, host):
        matched = AGENT_PATH.fullmatch(path)
        if matched is None:
            raise Reject(404, "not_found")
        workspace_id = self._token_workspace(matched["token"])
        if workspace_id is None:
            raise Reject(404, "no_workspace")
        route = matched["name"]
        allowed = "GET" if route == "tools" else "POST"
        if request.method != allowed:
            raise Reject(405, "method_not_allowed", {"Allow": allowed})
        self._same_origin(request, host)
        if not self.agents.allow(workspace_id):
            raise Reject(429, "rate_limited", {"Retry-After": "30"})
        if route == "tools":
            return Response.json({"tools": tools.listing()})
        if route == "invoke":
            return self._invoke(request, workspace_id, AGENT, "agent")
        self._check_body(request)
        headers = request.headers
        status, answer = mcp.handle(self.runtime, workspace_id, request.body,
                                    headers.get("mcp-protocol-version"), headers.get("mcp-method"),
                                    headers.get("mcp-name"))
        if answer is None:
            return Response(status, b"", "application/json")
        return Response.json(answer, status)

    @staticmethod
    def _check_body(request):
        if request.length < 0:
            raise Reject(411, "length_required")
        if request.length > BODY_MAX:
            raise Reject(413, "too_large")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise Reject(415, "bad_content_type")

    def _invoke(self, request, workspace_id, actor, surface):
        self._check_body(request)
        try:
            message = json.loads(request.body)
        except (ValueError, RecursionError):
            raise Reject(400, "bad_json") from None
        if not isinstance(message, dict) or not set(message) <= {"tool", "args"}:
            raise Reject(400, "bad_body")
        envelope = invoke.invoke(self.runtime, workspace_id, message.get("tool"), message.get("args", {}),
                                 actor, surface)
        return Response.json(envelope)

    def _state(self, request, workspace_id, actor, why):
        runtime, settings = self.runtime, self.settings
        _, limiter = self._client(request)
        with runtime.workspace_lock(workspace_id):
            runtime.update(store.touch, workspace_id, runtime.clock().isoformat(timespec="seconds"))
            found = store.workspace(runtime.doc, workspace_id)
            allowances = runtime.ledger.snapshot(workspace_id)
            return Response.json({
                "actor": actor,
                "actorWhy": why,
                "limiterKey": limiter,
                "server": {
                    "provider": runtime.provider.name,
                    "mode": runtime.mode(),
                    "key": settings.key_status,
                },
                "allowances": allowances,
                "limits": {"callCap": settings.max_calls, "callCapMax": config.MAX_CALLS_RAISED},
                "sample": self.sample,
                "agentPath": f"/agent/{found['agentToken']}" if actor == PERSON else None,
                "workspace": {
                    "createdAt": found["createdAt"],
                    "lastSeenAt": found["lastSeenAt"],
                    "idleTtlSeconds": settings.workspace_ttl_s,
                    "programmes": [{**p, "view": tools.board_view(p)} for p in found["programmes"]],
                    "activityLog": found["activityLog"],
                },
            })

    def _calls(self, workspace_id):
        """The workspace's own call log, the counters and the newest programme's budget and steps, read
        without the workspace lock so the page can watch a plan fill them: the document is replaced,
        never changed in place, and a plan saves the programme after each call."""
        label = workspace_label(workspace_id)
        doc = self.runtime.doc
        mine = [{name: value for name, value in call.items() if name != "workspace"}
                for call in doc["calls"] if call["workspace"] == label]
        programmes = store.workspace(doc, workspace_id)["programmes"]
        newest = None
        if programmes:
            newest = {"id": programmes[-1]["id"], "budget": programmes[-1]["budget"],
                      "steps": [_brief(step) for step in programmes[-1]["trace"]]}
        return Response.json({
            "calls": mine[-CALLS_SHOWN:],
            "total": len(mine),
            "allowances": self.runtime.ledger.snapshot(workspace_id),
            "programme": newest,
        })

    def _inquiry(self, workspace_id, programme_id, draft_id):
        try:
            found = store.programme(self.runtime.doc, workspace_id, programme_id)
        except store.Refusal:
            raise Reject(404, "not_found") from None
        draft = next((d for d in found["inquiries"] if d["draftId"] == draft_id and d["state"] == "approved"),
                     None)
        if draft is None:
            raise Reject(404, "not_found")
        return Response(200, (draft["text"] + "\n").encode("utf-8"), "text/plain; charset=utf-8", {
            "Content-Disposition": f'attachment; filename="inquiry-{programme_id}-{draft_id}.txt"',
        })


def _brief(step):
    """A trace step without its request parameters, which carry the names a person typed."""
    kept = {name: value for name, value in step.items() if name not in ("request", "entityId")}
    if "request" in step:
        kept["request"] = {"path": step["request"]["path"]}
    return kept


class Handler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT_S

    def version_string(self):
        return "open-slot"

    def log_message(self, *args):
        """Nothing is logged per request: a path can carry an agent token and a body a typed name."""

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self._write(self.server.app.malformed(code))

    def _length(self, headers):
        if "transfer-encoding" in headers:
            return -1
        raw = headers.get("content-length", "0")
        return int(raw) if re.fullmatch(r"[0-9]{1,10}", raw) else -1

    def _discard(self, length):
        left = min(length, DRAIN_MAX)
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)

    def _write(self, response):
        self.send_response(response.status)
        self.send_header("Content-Type", response.type)
        self.send_header("Content-Length", str(len(response.body)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _serve(self):
        headers = {}
        for name, value in self.headers.items():
            name = name.lower()
            headers[name] = f"{headers[name]}, {value}" if name in headers else value
        length = self._length(headers)
        body = b""
        if 0 < length <= BODY_MAX:
            body = self.rfile.read(length)
        elif length > BODY_MAX:
            self._discard(length)
            self.close_connection = True
        request = Request(self.command, self.path, headers, body, length, self.client_address[0])
        self._write(self.server.app.handle(request))

    do_GET = do_POST = do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _serve


class Server(ThreadingHTTPServer):
    """One thread per connection, at most `max_connections` at a time: a flood is answered 503 on the
    accepting thread, without starting another, so it cannot exhaust the host's threads or memory."""

    daemon_threads = True

    def __init__(self, address, app, max_connections=MAX_CONNECTIONS):
        self.app = app
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self._turn_away(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def _turn_away(self, request):
        reply = self.app.overloaded()
        head = (f"HTTP/1.1 {reply.status} Service Unavailable\r\nContent-Type: {reply.type}\r\n"
                f"Content-Length: {len(reply.body)}\r\nConnection: close\r\n"
                + "".join(f"{name}: {value}\r\n" for name, value in reply.headers.items()) + "\r\n")
        try:
            request.settimeout(1)
            request.sendall(head.encode("ascii") + reply.body)
            # Reading what the client already sent keeps the close from becoming a reset that would
            # take the answer with it.
            request.setblocking(False)
            request.recv(65536)
        except OSError:
            pass
        self.shutdown_request(request)

    def server_bind(self):
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_server(app, bind, port, max_connections=MAX_CONNECTIONS):
    """A bound server for `app`; port 0 picks a free one, and app.port becomes the one bound."""
    httpd = Server((bind, port), app, max_connections)
    app.port = httpd.server_address[1]
    return httpd
