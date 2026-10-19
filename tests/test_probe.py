import json
import re
import unittest
from datetime import date

from open_slot import pins, probe, providers, qloo, transport
from tests.support import FIXTURES, Stub, fake_key

NAMES = ["The Milk Carton Kids", "Maria Bamford", "Judge John Hodgman"]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
FAKE = providers.FakeProvider()


def answer_from_fake(override=None):
    def answer(request):
        if override:
            replaced = override(request)
            if replaced is not None:
                return replaced
        status, body = FAKE.respond(request["path"], request["query"])
        return status, body, {}
    return answer


def response_strings():
    """Every string value the synthetic responses carry, and the typed names that find them: none
    may reach the probe's output. Field names are not values, and the entity type URNs are
    constants of this program, so they may."""
    found = set()

    def walk(value):
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if UUID.fullmatch(key):
                    found.add(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for path in FIXTURES.glob("scenario-*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        walk(data)
        found.update(data["search"])
    return {s for s in found if len(s) >= 5 and not s.startswith("urn:entity:")}


def run_against(answer, names=NAMES):
    with Stub(answer) as stub:
        report = probe.run(fake_key(), names, base_url=stub.base_url, today=date(2026, 10, 19))
    return report, stub.requests


class Run(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.requests = run_against(answer_from_fake())
        cls.output = probe.format_report(cls.report)
        cls.notes = probe.render_notes(cls.report)

    def test_it_stays_within_fourteen_calls_and_says_how_many_it_used(self):
        self.assertEqual(len(self.requests), 13)
        self.assertEqual(self.report["calls_used"], 13)
        self.assertLessEqual(self.report["calls_used"], probe.BUDGET)

    def test_every_call_is_a_get_with_the_key_in_the_header_only(self):
        for request in self.requests:
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["headers"]["x-api-key"], fake_key())
            self.assertNotIn(fake_key(), request["raw_query"])

    def test_the_calls_are_the_ones_the_plan_names_in_order(self):
        paths = [(r["path"], r["query"].get("filter.type")) for r in self.requests]
        self.assertEqual(paths, [
            ("/search", None), ("/search", None), ("/search", None),
            ("/v2/insights", qloo.ARTIST), ("/v2/insights", qloo.ARTIST),
            ("/v2/insights", qloo.PODCAST), ("/v2/tags", None), ("/v2/insights", qloo.PERSON),
            ("/v2/trending", qloo.ARTIST), ("/v2/trending", qloo.ARTIST),
            ("/v2/insights", qloo.ARTIST),
            ("/v2/insights", probe.UNSUPPORTED_TYPE), ("/search", None),
        ])
        trending = [r for r in self.requests if r["path"] == "/v2/trending"]
        for request in trending:
            self.assertNotIn(",", request["query"]["signal.interests.entities"])
        self.assertNotEqual(trending[0]["query"]["signal.interests.entities"],
                            trending[1]["query"]["signal.interests.entities"])

    def test_the_output_has_no_key_no_id_and_no_string_from_a_response(self):
        for text in (self.output, self.notes):
            self.assertNotIn(fake_key(), text)
            self.assertFalse(UUID.findall(text), "an id-shaped string was printed")
            lowered = text.lower()
            for string in response_strings():
                self.assertNotIn(string.lower(), lowered, f"a response string was printed: {string!r}")
        for name in NAMES:
            self.assertNotIn(name.lower(), self.output.lower())

    def test_the_report_is_shapes_counts_status_codes_and_constants(self):
        paths = self.report["shapes"]["p2_artists"]
        self.assertIn("results.entities[].popularity: number", paths)
        self.assertIn("results.entities[].query.explainability.signal.interests.entities[].score: number",
                      paths)
        self.assertIn("results.entities[].name: string", paths)
        self.assertEqual(self.report["p1"]["status"], [200, 200, 200])
        self.assertEqual(self.report["p1"]["counts"], [2, 1, 1])
        self.assertEqual(self.report["p1"]["types_match"], [True, True, True])
        self.assertTrue(self.report["p1"]["comma_types_accepted"])

        def leaves(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from leaves(item)
            elif isinstance(value, list):
                for item in value:
                    yield from leaves(item)
            else:
                yield value

        measured = {k: v for k, v in self.report.items() if k not in ("shapes", "pins", "pins_notes")}
        for leaf in leaves(measured):
            plain_text = isinstance(leaf, str) and len(leaf) < 90 and not UUID.search(leaf)
            self.assertTrue(leaf is None or isinstance(leaf, (bool, int, float)) or plain_text, repr(leaf))

    def test_the_thresholds_on_the_synthetic_answers(self):
        thresholds = self.report["thresholds"]
        self.assertTrue(thresholds["T1"]["pass"])
        self.assertTrue(thresholds["T2"]["podcast"]["pass"])
        self.assertTrue(thresholds["T2"]["comedy"]["pass"])
        self.assertTrue(thresholds["T3"]["pass"])
        self.assertTrue(thresholds["T4"]["pass"])
        self.assertTrue(thresholds["T5"]["pass"])

    def test_the_measurements_behind_them(self):
        p2, p4, p5, p6 = (self.report[k] for k in ("p2", "p4", "p5", "p6"))
        self.assertEqual((p2["artist_count"], p2["calls"]), (10, 2))
        self.assertTrue(p2["popularity_filter_changes_result"])
        self.assertTrue(p2["explainability_scores"])
        self.assertEqual(p2["popularity_share"], 1.0)
        self.assertTrue(p4["entity_filter_honoured"] and p4["percentile_varies"] and p4["direction_check"])
        self.assertEqual([c["rows"] for c in p4["per_call"]], [8, 8])
        self.assertEqual(p4["rank_falls_when_percentile_rises"], 1.0)
        self.assertFalse(p5["order_differs_from_default"])
        self.assertEqual((p6["unsupported_type"]["maps_to"], p6["nonsense_search"]["maps_to"]),
                         ("qloo_unsupported_type", "qloo_no_results"))

    def test_the_pins_block_is_python_for_pins_py(self):
        block = self.report["pins"]
        namespace = {}
        exec(block, namespace)
        self.assertTrue(namespace["VERIFIED"])
        self.assertEqual(namespace["CONFIRMED_LENSES"], ("music", "podcast", "comedy"))
        self.assertEqual(namespace["LENS_DEFAULTS"]["comedy"]["type"], qloo.PERSON)
        self.assertEqual(namespace["LENS_DEFAULTS"]["music"]["bias_trends"], "medium")
        self.assertIsNone(namespace["LENS_DEFAULTS"]["comedy"]["tag"])
        self.assertEqual(namespace["TREND_FIELDS"], pins.TREND_FIELDS)
        self.assertEqual(namespace["PERCENTILE_SCALE"], 100)
        self.assertIsInstance(namespace["TREND_EPS"], float)
        self.assertNotIn("urn:tag:synthetic", block, "a tag id from a response is never printed")
        self.assertTrue(any("comedian tag exists" in note for note in self.report["pins_notes"]))

    def test_the_notes_are_markdown_with_no_placeholder_words(self):
        self.assertTrue(self.notes.startswith("# Qloo API notes"))
        self.assertIn("- T1: pass", self.notes)
        self.assertIn("- T2 comedy: pass", self.notes)
        self.assertNotIn("TODO", self.notes)


class WhenThingsGoWrong(unittest.TestCase):
    def test_it_never_retries_a_429_or_a_5xx(self):
        def refuse(request):
            if request["path"] == "/v2/trending":
                return 429, {"message": "slow down"}, {}
            if request["path"] == "/v2/insights" and request["query"].get("bias.trends") == "high":
                return 503, {"message": "down"}, {}

        report, requests = run_against(answer_from_fake(refuse))
        counts = {}
        for request in requests:
            key = (request["path"], request["raw_query"])
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(max(counts.values()), 1, "a request was repeated")
        self.assertEqual([c["status"] for c in report["p4"]["per_call"]], [429, 429])
        self.assertFalse(report["thresholds"]["T3"]["pass"])
        self.assertEqual(report["p5"]["status"], 503)
        self.assertLessEqual(len(requests), probe.BUDGET)

    def test_an_empty_200_for_artists_is_tried_once_more_with_other_anchors(self):
        def empty(request):
            if request["path"] == "/v2/insights" and request["query"].get("filter.type") == qloo.ARTIST:
                return 200, {"success": True, "results": {"entities": []}}, {}

        report, requests = run_against(answer_from_fake(empty))
        self.assertEqual(report["p2"]["calls"], 2)
        self.assertEqual(report["p2"]["retry_with_other_anchors"], {"status": 200, "count": 0})
        self.assertFalse(report["thresholds"]["T1"]["pass"])
        self.assertTrue(any("T1 did not pass" in note for note in report["pins_notes"]))
        self.assertLessEqual(len(requests), probe.BUDGET)
        first, second = [r for r in requests if r["path"] == "/v2/insights"][:2]
        key = "signal.interests.entities"
        self.assertNotEqual(first["query"][key], second["query"][key])

    def test_the_worst_case_still_stops_at_fourteen(self):
        sent = []

        def send(base, request, key):
            sent.append(request)
            return transport.Response(200, {"success": True, "results": {"entities": [], "tags": []}}, 0)

        class Greedy(probe.Probe):
            pass

        asker = Greedy(fake_key(), NAMES, send=send)
        for _ in range(probe.BUDGET):
            asker.ask("x", qloo.search("a"), qloo.read_search)
        with self.assertRaises(probe.BudgetSpent):
            asker.ask("x", qloo.search("a"), qloo.read_search)
        self.assertEqual(len(sent), probe.BUDGET)

    def test_a_shape_it_does_not_know_is_reported_as_a_path_and_the_rest_is_skipped(self):
        def other_shape(request):
            if request["path"] == "/search":
                return 200, {"data": [{"label": "Secret Name", "ref": "abc"}]}, {}

        report, requests = run_against(answer_from_fake(other_shape))
        self.assertEqual(len(requests), 3)
        self.assertIn("stopped", report)
        self.assertEqual(report["p1"]["unreadable_at"], "results")
        self.assertIn("data[].label: string", report["shapes"]["p1_search"])
        output = probe.format_report(report)
        self.assertNotIn("Secret Name", output)
        self.assertFalse(report["thresholds"]["T1"]["pass"])

    def test_a_connection_that_fails_is_reported_as_a_fixed_word(self):
        def send(base, request, key):
            raise transport.TransportError("connection")

        report = probe.run(fake_key(), NAMES, send=send)
        self.assertEqual(report["p1"]["status"], ["connection"] * 3)
        self.assertEqual(report["calls_used"], 3)

    def test_the_key_never_appears_when_a_401_or_a_response_quotes_it(self):
        key = fake_key()

        def quoting(request):
            return 401, {"message": f"No API key found in request {key}", key: "x", "detail": [key]}, {}

        report, _ = run_against(answer_from_fake(quoting))
        for text in (probe.format_report(report), probe.render_notes(report)):
            self.assertNotIn(key, text)
        self.assertIn("<key>", json.dumps(report["shapes"]))

    def test_a_dict_keyed_by_a_response_string_is_not_printed(self):
        def keyed(request):
            if request["path"] == "/search":
                return 200, {"results": [], "by_name": {"birchwater duo": 1}}, {}

        report, _ = run_against(answer_from_fake(keyed))
        self.assertNotIn("birchwater", json.dumps(report).lower())


class Names(unittest.TestCase):
    def test_a_name_the_request_builder_refuses_is_refused_before_any_call(self):
        sent = []
        with self.assertRaises(qloo.RequestRefused):
            probe.run(fake_key(), ["x" * 61, "b", "c"], send=lambda *a: sent.append(a))
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
