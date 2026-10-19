"""The one module that touches the network or holds the key: Qloo's API, and the stdio relay's
posts to an agent address."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import LOOPBACK_BINDS

MAX_BYTES = 8 * 1024 * 1024
FIXED_MESSAGES = {
    "timeout": "Qloo did not answer in time.",
    "connection": "Qloo could not be reached.",
    "unreadable": "Qloo sent an answer that could not be read.",
    "agent_timeout": "The agent address did not answer in time.",
    "agent_connection": "The agent address could not be reached.",
    "agent_unreadable": "The agent address sent an answer that could not be read.",
}


class TransportError(Exception):
    """Carries a fixed message only: what the library said may hold the key or the address."""

    def __init__(self, kind):
        super().__init__(FIXED_MESSAGES[kind])
        self.kind = kind


@dataclass(frozen=True)
class Response:
    status: int
    body: object
    ms: int
    header_names: frozenset = frozenset()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would replay the key header at whatever address it names.
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _require_safe(url):
    parts = urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in LOOPBACK_BINDS:
        raise ValueError("a key or a token is only sent over https, or to a loopback address")


def _exchange(outgoing, timeout, prefix):
    """The status, the body and the lower-cased header names of one request. The library's own text
    may hold the key or the address, so a failure is only ever one of the fixed kinds, named
    `prefix` + timeout, connection or unreadable."""
    try:
        try:
            reply = _opener.open(outgoing, timeout=timeout)
        except urllib.error.HTTPError as error:
            reply = error
        with reply:
            raw = reply.read(MAX_BYTES + 1)
            status = reply.status
            names = frozenset(name.lower() for name in reply.headers.keys())
    except TimeoutError:
        raise TransportError(prefix + "timeout") from None
    except urllib.error.URLError as error:
        slow = isinstance(error.reason, TimeoutError)
        raise TransportError(prefix + ("timeout" if slow else "connection")) from None
    except Exception:
        raise TransportError(prefix + "connection") from None
    if len(raw) > MAX_BYTES:
        raise TransportError(prefix + "unreadable")
    return status, raw, names


def send(base_url, request, key, timeout=15.0):
    _require_safe(base_url)
    outgoing = urllib.request.Request(request.url(base_url), method="GET")
    outgoing.add_header("X-Api-Key", key)
    outgoing.add_header("Accept", "application/json")
    started = time.monotonic()
    status, raw, names = _exchange(outgoing, timeout, "")
    ms = round((time.monotonic() - started) * 1000)
    try:
        body = json.loads(raw.decode("utf-8")) if raw.strip() else None
    except ValueError:
        if 200 <= status < 300:
            raise TransportError("unreadable") from None
        body = None
    return Response(status, body, ms, names)


def post(url, body, headers, timeout=120.0):
    """One POST to an agent address, the way the stdio relay forwards a message: the status and the raw
    answer. The token is in the path, so it follows no redirect and never travels over plain HTTP to
    another host."""
    _require_safe(url)
    outgoing = urllib.request.Request(url, data=body, method="POST")
    for name, value in headers.items():
        outgoing.add_header(name, value)
    status, raw, _ = _exchange(outgoing, timeout, "agent_")
    return status, raw
