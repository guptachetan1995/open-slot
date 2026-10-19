"""The MCP front end: JSON-RPC 2.0 over Streamable HTTP, written on the standard library.

It offers the agent's tools to any MCP client and nothing more: no resources or prompts, no streams,
no sessions. Every call goes through invoke() as the agent, on the agent's surface, so the five
human-only verbs are not tools here and asking for one is `not_a_tool`. The caller is fixed by the
address the request arrived on and never by anything the client sends.

One endpoint speaks two eras of the protocol and picks one per request. A request that carries the
`MCP-Protocol-Version: 2026-07-28` header or the protocol version in its `_meta` is modern and
served statelessly: no handshake, the version and headers checked on every request, `server/discover`.
Anything else is legacy: the `initialize` handshake, then the same tools.

Every answer is a JSON-RPC message with a code and a fixed sentence, never a stack trace.

The stdio relay at the end is the same endpoint for a client that only speaks stdio: it reads one
message per line, posts it to an agent address and writes the answer, so what it can do is exactly
what the address can do.
"""

import base64
import json
import re
import sys
from urllib.parse import urlsplit

from . import config, invoke, tools, transport
from .providers import AGENT

MODERN = "2026-07-28"
# The legacy revisions `initialize` is answered for, newest first. 2025-03-26 asks a server to accept
# JSON-RPC batches and 2024-11-05 speaks HTTP+SSE; this endpoint does neither, so neither is claimed,
# and a client that asks for one is offered the newest.
VERSIONS = ("2025-11-25", "2025-06-18")
SUPPORTED = (MODERN, *VERSIONS)
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
SERVER = {"name": "open-slot", "version": "0.1"}
INSTRUCTIONS = (
    "Open Slot finds acts whose audiences overlap a venue's regulars and drafts inquiries about "
    "them. These tools search, plan, propose nights and draft. Confirming which match is the act "
    "meant, assigning a night, approving an inquiry, dropping an act and raising the call cap are "
    "done by a person on the page, and this address cannot do them. Nothing here contacts anyone."
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
SERVER_ERROR = -32000
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022


class Failure(Exception):
    def __init__(self, code, message, data=None, status=200):
        super().__init__(message)
        self.code, self.data, self.status = code, data, status


def error(code, message, request_id=None, data=None):
    detail = {"code": code, "message": message}
    if data:
        detail["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": detail}


def refusal(outcome, message):
    """The server's own refusal of an HTTP request to the MCP route, as a JSON-RPC error."""
    return error(SERVER_ERROR, message, None, {"outcome": outcome})


def run_tool(runtime, workspace_id, params):
    """The payload of one tool call and whether it is a refusal the agent can read and correct."""
    name = params.get("name")
    if not isinstance(name, str):
        raise Failure(INVALID_PARAMS, "name must be the name of a tool")
    envelope = invoke.invoke(runtime, workspace_id, name, params.get("arguments", {}), AGENT, "agent")
    if envelope["ok"]:
        return envelope["result"], False
    if envelope["outcome"] == "not_a_tool":
        raise Failure(INVALID_PARAMS, "unknown tool", {"outcome": "not_a_tool"})
    return {"outcome": envelope["outcome"], "error": envelope["error"]}, True


def text(payload):
    return [{"type": "text", "text": json.dumps(payload)}]


def initialize(runtime, workspace_id, params):
    asked = params.get("protocolVersion")
    if not isinstance(asked, str):
        raise Failure(INVALID_PARAMS, "protocolVersion must be a string")
    return {
        "protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER,
        "instructions": INSTRUCTIONS,
    }


def ping(runtime, workspace_id, params):
    return {}


def list_tools(runtime, workspace_id, params):
    return {"tools": tools.listing()}


def call_tool(runtime, workspace_id, params):
    payload, failed = run_tool(runtime, workspace_id, params)
    return {"content": text(payload), "isError": failed}


def discover(runtime, workspace_id, params):
    return {"supportedVersions": list(SUPPORTED), "capabilities": {"tools": {}},
            "instructions": INSTRUCTIONS, "ttlMs": 0, "cacheScope": "private"}


def modern_list_tools(runtime, workspace_id, params):
    return {"tools": tools.listing(), "ttlMs": 0, "cacheScope": "private"}


def modern_call_tool(runtime, workspace_id, params):
    payload, failed = run_tool(runtime, workspace_id, params)
    return {"content": text(payload), "structuredContent": payload, "isError": failed}


LEGACY_METHODS = {
    "initialize": initialize,
    "ping": ping,
    "tools/list": list_tools,
    "tools/call": call_tool,
}
MODERN_METHODS = {
    "server/discover": discover,
    "tools/list": modern_list_tools,
    "tools/call": modern_call_tool,
}


def _invalid(message):
    return 400, error(INVALID_REQUEST, message)


def _meta(message):
    params = message.get("params") if isinstance(message, dict) else None
    meta = params.get("_meta") if isinstance(params, dict) else None
    return meta if isinstance(meta, dict) else {}


def _decoded(value):
    """A mirrored header value as the body would carry it: the Base64 sentinel form is decoded."""
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except ValueError:
            return None
    return value


def _mismatch(message):
    return Failure(HEADER_MISMATCH, f"Header mismatch: {message}", status=400)


def _check(message, protocol, header_method, header_name):
    """A modern request's own rules: the version in the header and in `_meta` agree and are one this
    server speaks, `_meta` carries what every request must, and Mcp-Method and Mcp-Name say what the
    body says. The version comes first, because the other rules are those of the version asked for.
    Returns the params."""
    meta = _meta(message)
    asked = meta.get(VERSION_KEY)
    if VERSION_KEY in meta and not isinstance(asked, str):
        raise Failure(INVALID_PARAMS, "_meta protocolVersion must be text", status=400)
    if None not in (protocol, asked) and protocol != asked:
        raise _mismatch("MCP-Protocol-Version does not match the version in _meta")
    requested = protocol if asked is None else asked
    if requested != MODERN:
        data = {"supported": list(SUPPORTED), "requested": requested}
        raise Failure(UNSUPPORTED_VERSION, "Unsupported protocol version", data, 400)
    if protocol is None:
        raise _mismatch("MCP-Protocol-Version is missing")
    if asked is None:
        raise Failure(INVALID_PARAMS, "_meta must carry the protocol version", status=400)
    if not isinstance(meta.get(CAPABILITIES_KEY), dict):
        raise Failure(INVALID_PARAMS, "_meta must carry the client capabilities", status=400)
    if header_method != message["method"]:
        raise _mismatch("Mcp-Method does not match the method in the body")
    params = message["params"]
    if message["method"] == "tools/call" and _decoded(header_name) != params.get("name"):
        raise _mismatch("Mcp-Name does not match the tool named in the body")
    return params


def _handle_modern(runtime, workspace_id, message, protocol, header_method, header_name):
    if not (isinstance(message, dict) and message.get("jsonrpc") == "2.0"
            and isinstance(message.get("method"), str)):
        return _invalid("a message is one JSON-RPC 2.0 request or notification")
    if "id" not in message:
        return 202, None
    request_id = message["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        return _invalid("id must be a string or an integer")
    try:
        params = _check(message, protocol, header_method, header_name)
        handler = MODERN_METHODS.get(message["method"])
        if handler is None:
            return 404, error(METHOD_NOT_FOUND, "method not found", request_id)
        result = handler(runtime, workspace_id, params)
    except Failure as failure:
        return failure.status, error(failure.code, str(failure), request_id, failure.data)
    return 200, {"jsonrpc": "2.0", "id": request_id,
                 "result": {"resultType": "complete", **result, "_meta": {SERVER_INFO_KEY: SERVER}}}


def _handle_legacy(runtime, workspace_id, message, protocol):
    if protocol is not None and protocol not in VERSIONS:
        return 400, error(INVALID_REQUEST, "unsupported MCP-Protocol-Version", None,
                          {"outcome": "bad_protocol_version"})
    if isinstance(message, list):
        return _invalid("batches are not supported; send one message per request")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _invalid("a message is a JSON-RPC 2.0 object")
    if "method" not in message:
        if "id" in message and ("result" in message or "error" in message):
            return 202, None
        return _invalid("a message needs a method")
    method = message["method"]
    if not isinstance(method, str):
        return _invalid("method must be a string")
    if "id" not in message:
        return 202, None
    request_id = message["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        return _invalid("id must be a string or an integer")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return 200, error(INVALID_PARAMS, "params must be an object", request_id)
    handler = LEGACY_METHODS.get(method)
    if handler is None:
        return 200, error(METHOD_NOT_FOUND, "method not found", request_id)
    try:
        result = handler(runtime, workspace_id, params)
    except Failure as failure:
        return 200, error(failure.code, str(failure), request_id, failure.data)
    return 200, {"jsonrpc": "2.0", "id": request_id, "result": result}


def handle(runtime, workspace_id, body, protocol=None, method=None, name=None):
    """One POSTed message to a workspace's agent. `protocol`, `method` and `name` are the
    MCP-Protocol-Version, Mcp-Method and Mcp-Name headers, None when the request had none. Returns
    the HTTP status and the JSON-RPC answer, or None for the answer when the message needs none (a
    notification, or a legacy client's reply): that is a 202 with no body."""
    try:
        message = json.loads(body)
    except (ValueError, RecursionError):
        return 400, error(PARSE_ERROR, "the body is not JSON")
    if protocol == MODERN or VERSION_KEY in _meta(message):
        return _handle_modern(runtime, workspace_id, message, protocol, method, name)
    return _handle_legacy(runtime, workspace_id, message, protocol)


MAX_LINE = 64 * 1024
NO_REPLY = object()
TOO_LONG = object()
AGENT_PATH = re.compile(r"/agent/[0-9a-f]{32}")


def agent_endpoint(address):
    """The MCP endpoint of an agent address, `<origin>/agent/<token>` with or without `/mcp`. The token
    is in the path, so only https is accepted, or http to a loopback name."""
    parts = urlsplit(address.strip())
    path = parts.path.rstrip("/").removesuffix("/mcp")
    local = parts.hostname in config.LOOPBACK_BINDS
    if (parts.scheme not in ("https", "http") or (parts.scheme == "http" and not local)
            or "@" in parts.netloc or parts.query or parts.fragment or not AGENT_PATH.fullmatch(path)):
        raise ValueError("the address must look like https://<host>/agent/<token>, or "
                         "http://127.0.0.1:<port>/agent/<token> for a local server")
    return f"{parts.scheme}://{parts.netloc}{path}/mcp"


def header_text(value):
    """A header value that says what the body says: plain when it is visible ASCII, else the Base64
    sentinel form that the 2026-07-28 Streamable HTTP page prescribes."""
    plain = value.isascii() and value.isprintable() and value == value.strip()
    if plain and not (value.startswith("=?base64?") and value.endswith("?=")):
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8", "replace")).decode("ascii") + "?="


def modern_headers(message):
    """The 2026-07-28 stdio transport has no header layer, and the endpoint's HTTP form needs one, so a
    modern message gets its headers from its own body: the version in `_meta`, the method and, for a
    call, the tool's name. A legacy message gets none, as on the endpoint."""
    version = _meta(message).get(VERSION_KEY)
    if not isinstance(version, str):
        return {}
    headers = {"MCP-Protocol-Version": header_text(version)}
    method = message.get("method")
    if isinstance(method, str):
        headers["Mcp-Method"] = header_text(method)
        params = message.get("params")
        if method == "tools/call" and isinstance(params, dict) and isinstance(params.get("name"), str):
            headers["Mcp-Name"] = header_text(params["name"])
    return headers


def _read_line(stream):
    """The next line as bytes, TOO_LONG for one over MAX_LINE (its rest is skipped), None at the end."""
    line = stream.readline(MAX_LINE + 1)
    if not line:
        return None
    if len(line) > MAX_LINE and not line.endswith(b"\n"):
        while line and not line.endswith(b"\n"):
            line = stream.readline(MAX_LINE)
        return TOO_LONG
    return line


def _reply_id(message):
    """The id an answer to `message` carries: NO_REPLY for a notification, null for what is no request."""
    if not isinstance(message, dict):
        return None
    found = message.get("id", NO_REPLY)
    return found if found is NO_REPLY or (isinstance(found, (str, int)) and not isinstance(found, bool)) \
        else None


def _relay_one(endpoint, line, stderr):
    """The answer to one line, or None when there is none to write: a notification gets a 202."""
    if line is TOO_LONG:
        return error(INVALID_REQUEST, "the message is over the 64 KiB this address accepts")
    try:
        message = json.loads(line)
    except (ValueError, RecursionError):
        return error(PARSE_ERROR, "the line is not JSON")
    reply_id = _reply_id(message)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if isinstance(message, dict):
        headers.update(modern_headers(message))

    def unanswered(text, outcome):
        if reply_id is NO_REPLY:
            stderr.write(f"open-slot: mcp: {text}\n")
            return None
        return error(SERVER_ERROR, text, reply_id, {"outcome": outcome})

    try:
        status, raw = transport.post(endpoint, line.strip(), headers)
    except transport.TransportError as failure:
        return unanswered(str(failure), failure.kind)
    if status == 202 and not raw.strip():
        return None
    try:
        answer = json.loads(raw)
    except (ValueError, RecursionError):
        answer = None
    if isinstance(answer, dict) and answer.get("jsonrpc") == "2.0":
        return answer
    return unanswered(f"The agent address answered HTTP {status} with something that is not MCP.", "not_mcp")


def relay(endpoint, stdin, stdout, stderr=sys.stderr):
    """Reads one JSON-RPC message per line from `stdin` (bytes), posts each to the endpoint and writes
    each answer as one line to `stdout` (bytes). Returns 0 when `stdin` ends."""
    while True:
        line = _read_line(stdin)
        if line is None:
            return 0
        if line is TOO_LONG or line.strip():
            answer = _relay_one(endpoint, line, stderr)
            if answer is not None:
                stdout.write(json.dumps(answer, separators=(",", ":")).encode("ascii") + b"\n")
                stdout.flush()
