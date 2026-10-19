import unittest

from open_slot import qloo, transport
from tests.support import Stub, fake_key

A = "00000000-0000-4000-8000-000000000101"
REQUEST = qloo.insights(qloo.ARTIST, [A], take=5)


class Sending(unittest.TestCase):
    def test_a_get_with_the_key_in_the_header_only(self):
        with Stub(lambda r: (200, {"results": {"entities": []}}, {"X-RateLimit-Remaining": "9"})) as stub:
            reply = transport.send(stub.base_url, REQUEST, fake_key())
        (seen,) = stub.requests
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["path"], "/v2/insights")
        self.assertEqual(seen["headers"]["x-api-key"], fake_key())
        self.assertNotIn(fake_key(), seen["raw_query"])
        self.assertNotIn("authorization", seen["headers"])
        self.assertEqual(reply.status, 200)
        self.assertEqual(reply.body, {"results": {"entities": []}})
        self.assertIn("x-ratelimit-remaining", reply.header_names)
        self.assertGreaterEqual(reply.ms, 0)

    def test_an_error_status_is_returned_with_its_body_not_raised(self):
        with Stub(lambda r: (401, {"message": "No API key found in request"}, {})) as stub:
            reply = transport.send(stub.base_url, REQUEST, fake_key())
        self.assertEqual((reply.status, reply.body), (401, {"message": "No API key found in request"}))

    def test_a_non_json_error_page_has_no_body(self):
        with Stub(lambda r: (502, b"<html>bad gateway</html>", {})) as stub:
            reply = transport.send(stub.base_url, REQUEST, fake_key())
        self.assertEqual((reply.status, reply.body), (502, None))

    def test_a_success_that_is_not_json_is_unreadable(self):
        with Stub(lambda r: (200, b"not json", {})) as stub:
            with self.assertRaises(transport.TransportError) as caught:
                transport.send(stub.base_url, REQUEST, fake_key())
        self.assertEqual(caught.exception.kind, "unreadable")

    def test_a_redirect_is_not_followed_so_the_key_is_not_replayed(self):
        with Stub(lambda r: (200, {}, {})) as elsewhere:
            target = elsewhere.base_url + "/stolen"
            with Stub(lambda r: (302, b"", {"Location": target})) as stub:
                reply = transport.send(stub.base_url, REQUEST, fake_key())
        self.assertEqual(reply.status, 302)
        self.assertEqual(elsewhere.requests, [])

    def test_the_key_is_not_sent_over_plain_http_to_another_host(self):
        with self.assertRaises(ValueError) as caught:
            transport.send("http://example.invalid", REQUEST, fake_key())
        self.assertNotIn(fake_key(), str(caught.exception))

    def test_a_refused_connection_gets_a_fixed_message(self):
        with Stub(lambda r: (200, {}, {})) as stub:
            dead = stub.base_url
        with self.assertRaises(transport.TransportError) as caught:
            transport.send(dead, REQUEST, fake_key(), timeout=2)
        self.assertEqual(caught.exception.kind, "connection")
        self.assertEqual(str(caught.exception), transport.FIXED_MESSAGES["connection"])
        self.assertNotIn("127.0.0.1", str(caught.exception))

    def test_a_slow_answer_times_out_with_a_fixed_message(self):
        import time

        def slow(request):
            time.sleep(0.6)
            return 200, {}, {}

        with Stub(slow) as stub:
            with self.assertRaises(transport.TransportError) as caught:
                transport.send(stub.base_url, REQUEST, fake_key(), timeout=0.2)
        self.assertEqual(caught.exception.kind, "timeout")


class Posting(unittest.TestCase):
    ADDRESS = "/agent/0123456789abcdef0123456789abcdef/mcp"

    def test_a_post_sends_the_bytes_and_headers_and_returns_the_status_and_the_raw_answer(self):
        with Stub(lambda r: (202, b"", {})) as stub:
            status, raw = transport.post(stub.base_url + self.ADDRESS, b'{"a": 1}', {"X-One": "1"})
        (seen,) = stub.requests
        self.assertEqual((status, raw), (202, b""))
        self.assertEqual((seen["method"], seen["path"], seen["body"]), ("POST", self.ADDRESS, '{"a": 1}'))
        self.assertEqual(seen["headers"]["x-one"], "1")
        self.assertNotIn("x-api-key", seen["headers"])

    def test_an_error_status_is_returned_with_its_body(self):
        with Stub(lambda r: (400, b'{"error": 1}', {})) as stub:
            self.assertEqual(transport.post(stub.base_url + self.ADDRESS, b"{}", {}), (400, b'{"error": 1}'))

    def test_a_redirect_is_not_followed_so_the_token_is_not_replayed(self):
        with Stub(lambda r: (200, {}, {})) as elsewhere:
            with Stub(lambda r: (302, b"", {"Location": elsewhere.base_url + "/stolen"})) as stub:
                status, _ = transport.post(stub.base_url + self.ADDRESS, b"{}", {})
        self.assertEqual(status, 302)
        self.assertEqual(elsewhere.requests, [])

    def test_a_token_is_not_sent_over_plain_http_to_another_host(self):
        with self.assertRaises(ValueError) as caught:
            transport.post("http://example.invalid" + self.ADDRESS, b"{}", {})
        self.assertNotIn("0123456789abcdef", str(caught.exception))

    def test_a_refused_connection_and_a_slow_answer_get_fixed_messages_without_the_address(self):
        import time

        with Stub(lambda r: (200, {}, {})) as stub:
            dead = stub.base_url
        with self.assertRaises(transport.TransportError) as caught:
            transport.post(dead + self.ADDRESS, b"{}", {}, timeout=2)
        self.assertEqual(caught.exception.kind, "agent_connection")
        self.assertEqual(str(caught.exception), transport.FIXED_MESSAGES["agent_connection"])
        self.assertNotIn("127.0.0.1", str(caught.exception))

        def slow(request):
            time.sleep(0.6)
            return 200, {}, {}

        with Stub(slow) as stub:
            with self.assertRaises(transport.TransportError) as caught:
                transport.post(stub.base_url + self.ADDRESS, b"{}", {}, timeout=0.2)
        self.assertEqual(caught.exception.kind, "agent_timeout")


if __name__ == "__main__":
    unittest.main()
