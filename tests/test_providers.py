import socket
import unittest
from unittest import mock

from open_slot import providers, qloo, transport
from tests.support import ROOT

KIDS = "00000000-0000-4000-8000-00000000a001"
BAND = "00000000-0000-4000-8000-00000000a002"
COMEDIAN = "00000000-0000-4000-8000-00000000a003"
PODCAST = "00000000-0000-4000-8000-00000000a004"
TAG = "urn:tag:synthetic:stand-up"


class FakeNeverTouchesTheNetwork(unittest.TestCase):
    def test_not_even_loopback_is_opened(self):
        fake = providers.FakeProvider()
        with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("connect")), \
                mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("lookup")):
            fake.fetch(qloo.search("Alvvays"))
            fake.fetch(qloo.tags("stand-up"))
            fake.fetch(qloo.insights(qloo.ARTIST, [KIDS, BAND]))
            fake.fetch(qloo.trending(qloo.ARTIST, KIDS, "2026-09-01", "2026-10-19"))

    def test_the_provider_module_does_not_import_the_network(self):
        source = (ROOT / "open_slot" / "providers.py").read_text(encoding="utf-8")
        for word in ("urllib", "http.client", "socket", "X-Api-Key"):
            self.assertNotIn(word, source)

    def test_it_answers_with_the_same_response_type_the_live_transport_returns(self):
        reply = providers.FakeProvider().fetch(qloo.search("Alvvays"))
        self.assertIsInstance(reply, transport.Response)
        self.assertEqual((reply.status, reply.ms), (200, 0))
        self.assertEqual(providers.FakeProvider.name, "fake")


class Search(unittest.TestCase):
    def setUp(self):
        self.fake = providers.FakeProvider()

    def names(self, query, **kwargs):
        reply = self.fake.fetch(qloo.search(query, **kwargs))
        return [c["name"] for c in qloo.read_search(reply.body)]

    def test_a_typed_name_resolves_to_an_invented_performer_with_alternatives(self):
        self.assertEqual(self.names("The Milk Carton Kids"), ["Birchwater Duo", "Birchwater Duo Tribute Act"])
        self.assertEqual(self.names("  alvvays "), ["Cinderfield Radio"])

    def test_every_candidate_says_it_is_synthetic_and_carries_a_reserved_id(self):
        for query in ("The Milk Carton Kids", "Alvvays", "Maria Bamford", "Judge John Hodgman"):
            for found in qloo.read_search(self.fake.fetch(qloo.search(query)).body):
                self.assertIn("synthetic", found["disambiguation"])
                self.assertTrue(found["entityId"].startswith("00000000-0000-4000-8000-"))

    def test_the_four_kinds_resolve_as_the_type_their_kind_implies(self):
        kinds = {}
        for query in ("The Milk Carton Kids", "Alvvays", "Maria Bamford", "Judge John Hodgman"):
            kinds[query] = qloo.read_search(self.fake.fetch(qloo.search(query)).body)[0]["type"]
        self.assertEqual(list(kinds.values()), [qloo.ARTIST, qloo.ARTIST, qloo.PERSON, qloo.PODCAST])

    def test_an_unknown_name_is_a_404_with_no_results(self):
        reply = self.fake.fetch(qloo.search("zzzzzz not a performer"))
        self.assertEqual(reply.status, 404)
        self.assertEqual(qloo.outcome(reply.status, qloo.SEARCH), "qloo_no_results")

    def test_take_and_types_are_honoured(self):
        self.assertEqual(self.names("The Milk Carton Kids", take=1), ["Birchwater Duo"])
        only_podcasts = qloo.build(qloo.SEARCH, {"query": "Alvvays", "types": [qloo.PODCAST]})
        self.assertEqual(self.fake.fetch(only_podcasts).status, 404)


class Tags(unittest.TestCase):
    def test_a_tag_search_returns_tags_in_the_documented_shape(self):
        fake = providers.FakeProvider()
        found = qloo.read_tags(fake.fetch(qloo.tags("stand-up")).body)
        self.assertEqual([t["id"] for t in found], [TAG])
        self.assertTrue(found[0]["id"].startswith("urn:tag:"))
        comedian = qloo.read_tags(fake.fetch(qloo.tags("comedian")).body)
        self.assertEqual(comedian[0]["name"], "Comedian (synthetic)")
        self.assertEqual(qloo.read_tags(fake.fetch(qloo.tags("polka")).body), [])


class Insights(unittest.TestCase):
    def setUp(self):
        self.fake = providers.FakeProvider()
        self.anchors = [KIDS, BAND, COMEDIAN, PODCAST]

    def read(self, request):
        return qloo.read_insights(self.fake.fetch(request).body)

    def test_the_pool_is_filtered_the_way_qloo_filters_it(self):
        wide = self.read(qloo.insights(qloo.ARTIST, self.anchors, exclude=self.anchors, take=50))
        self.assertEqual(len(wide["entities"]), 14)
        narrow = self.read(qloo.insights(qloo.ARTIST, self.anchors, exclude=self.anchors,
                                         popularity=(0.4, 0.76)))
        self.assertTrue(narrow["entities"])
        self.assertTrue(all(0.4 <= e["popularity"] <= 0.76 for e in narrow["entities"]))
        self.assertLess(len(narrow["entities"]), len(wide["entities"]))

    def test_without_the_exclusion_an_anchor_comes_back_and_with_it_does_not(self):
        names = {e["entityId"] for e in self.read(qloo.insights(qloo.ARTIST, self.anchors))["entities"]}
        self.assertIn(KIDS, names)
        excluded = self.read(qloo.insights(qloo.ARTIST, self.anchors, exclude=[KIDS]))
        kept = {e["entityId"] for e in excluded["entities"]}
        self.assertNotIn(KIDS, kept)

    def test_take_limits_the_count(self):
        self.assertEqual(len(self.read(qloo.insights(qloo.ARTIST, self.anchors, take=3))["entities"]), 3)

    def test_explainability_is_present_only_when_asked_for(self):
        on = self.read(qloo.insights(qloo.PODCAST, self.anchors))
        off = self.read(qloo.insights(qloo.PODCAST, self.anchors, explain=False))
        self.assertTrue(on["explainability"])
        self.assertFalse(off["explainability"])
        self.assertTrue(any(e["explain"] for e in on["entities"]))
        self.assertFalse(any(e["explain"] for e in off["entities"]))

    def test_scores_are_given_only_for_the_anchors_in_the_request(self):
        first = self.read(qloo.insights(qloo.ARTIST, [KIDS, BAND]))["entities"]
        scored = {item["entityId"] for e in first for item in e["explain"]}
        self.assertEqual(scored, {KIDS, BAND})

    def test_the_comedy_lens_needs_its_tag(self):
        tagged = self.read(qloo.insights(qloo.PERSON, self.anchors, tag=TAG))["entities"]
        self.assertEqual(len(tagged), 12)
        other = self.read(qloo.insights(qloo.PERSON, self.anchors, tag="urn:tag:synthetic:folk"))["entities"]
        self.assertEqual(other, [])
        self.assertEqual(qloo.outcome(200, qloo.INSIGHTS, other), "qloo_no_results")

    def test_growth_appears_for_the_ones_that_have_it(self):
        found = {e["name"]: e for e in self.read(qloo.insights(qloo.PERSON, self.anchors))["entities"]}
        self.assertEqual(found["Odile Thackeray"]["growth"], 0.09)
        self.assertIsNone(found["Ferris Quillon"]["growth"])

    def test_an_unknown_anchor_gets_nothing(self):
        other = "00000000-0000-4000-8000-0000000fffff"
        self.assertEqual(self.read(qloo.insights(qloo.ARTIST, [other]))["entities"], [])

    def test_an_unsupported_type_and_an_unknown_path_have_named_errors(self):
        for path in (qloo.INSIGHTS, qloo.TRENDING):
            status, body = self.fake.respond(path, {"filter.type": "urn:demographics",
                                                    "signal.interests.entities": KIDS})
            self.assertEqual(qloo.outcome(status, path), "qloo_unsupported_type")
        status, body = self.fake.respond("/recs", {})
        self.assertEqual(status, 404)


class Trending(unittest.TestCase):
    def test_weekly_rows_newest_first_in_the_documented_fields(self):
        fake = providers.FakeProvider()
        reply = fake.fetch(qloo.trending(qloo.ARTIST, KIDS, "2026-08-24", "2026-10-19"))
        rows = qloo.read_trending(reply.body)
        self.assertEqual(len(rows), 8)
        self.assertEqual([r["date"] for r in rows], sorted((r["date"] for r in rows), reverse=True))
        raw = fake.respond(qloo.TRENDING, {"filter.type": qloo.ARTIST, "signal.interests.entities": KIDS})[1]
        self.assertEqual(set(raw["results"]["trends"][0]), {
            "date", "population_percentile", "population_rank", "population_rank_velocity",
            "velocity_fold_change", "population_percent_delta"})

    def test_take_limits_the_rows_and_an_unknown_entity_has_none(self):
        fake = providers.FakeProvider()
        self.assertEqual(len(qloo.read_trending(fake.fetch(qloo.trending(qloo.ARTIST, KIDS, "2026-08-24",
                                                                         "2026-10-19", take=4)).body)), 4)
        reply = fake.fetch(qloo.trending(qloo.ARTIST, "00000000-0000-4000-8000-0000000fffff",
                                         "2026-08-24", "2026-10-19"))
        self.assertEqual(qloo.read_trending(reply.body), [])

    def test_different_entities_get_different_rows(self):
        fake = providers.FakeProvider()
        first = fake.fetch(qloo.trending(qloo.ARTIST, KIDS, "2026-08-24", "2026-10-19")).body
        second = fake.fetch(qloo.trending(qloo.ARTIST, BAND, "2026-08-24", "2026-10-19")).body
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
