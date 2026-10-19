import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fake_key():
    return "-".join(["fake", "key", "9d3f1c07", "b2e4a681"])


class Stub:
    """A loopback server that records each request and answers with `answer(request)`."""

    def __init__(self, answer):
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                request = {
                    "method": self.command,
                    "path": parts.path,
                    "query": dict(parse_qsl(parts.query)),
                    "raw_query": parts.query,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": self.rfile.read(length).decode("utf-8") if length else "",
                }
                stub.requests.append(request)
                status, body, headers = answer(request)
                raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)

            do_POST = do_PUT = do_DELETE = do_GET

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


ANCHOR_IDS = [f"00000000-0000-4000-8000-0000000005{n:02d}" for n in range(1, 5)]
ANCHOR_NAMES = ["The Folk Duo", "The Indie Band", "The Stand-Up", "The Live Podcast"]
AT = "2026-10-19T12:00:00+05:30"


def lantern_programme():
    """A programme as the store builds it, with all four anchors found and confirmed."""
    from open_slot import store

    doc = store.new_workspace(store.empty_document(), "ws_1", "tok_1", AT)
    doc, programme_id = store.new_programme(doc, "ws_1", {
        "label": "The Lantern Room (fictional)",
        "nights": ["Tue 3 Nov", "Tue 10 Nov", "Tue 17 Nov"],
        "anchors": ANCHOR_NAMES,
        "capacity_note": "300 seats",
    }, 16, AT)
    found = store.programme(doc, "ws_1", programme_id)
    for anchor, entity_id, popularity in zip(found["anchors"], ANCHOR_IDS, (0.62, 0.71, 0.55, 0.68)):
        anchor["candidates"] = [{
            "entityId": entity_id, "name": f"Resolved {anchor['name'][4:]}", "type": "urn:entity:artist",
            "popularity": popularity, "disambiguation": None,
        }]
        anchor["confirmed"] = entity_id
    return doc, found
