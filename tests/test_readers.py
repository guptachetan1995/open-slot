import copy
import unittest

from open_slot import qloo
from tests.support import fixture

ANCHOR_1 = "00000000-0000-4000-8000-000000000101"
ANCHOR_3 = "00000000-0000-4000-8000-000000000103"


class Search(unittest.TestCase):
    def test_reads_candidates(self):
        found = qloo.read_search(fixture("doc-search.json"))
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0], {
            "entityId": ANCHOR_1, "name": "Birchwater Duo", "type": "urn:entity:artist",
            "popularity": 0.62, "disambiguation": "synthetic folk duo",
        })
        self.assertIsNone(found[1]["popularity"])
        self.assertIsNone(found[1]["disambiguation"])

    def test_keeps_at_most_five(self):
        body = fixture("doc-search.json")
        body["results"] = body["results"] * 4
        self.assertEqual(len(qloo.read_search(body)), 5)

    def test_nothing_found_is_an_empty_list_that_maps_to_no_results(self):
        for body in ({}, {"results": []}, {"success": True, "results": None}, {"results": {}}):
            found = qloo.read_search(body)
            self.assertEqual(found, [])
            self.assertEqual(qloo.outcome(200, qloo.SEARCH, found), "qloo_no_results")

    def test_an_unknown_shape_fails_loudly_naming_the_path_and_no_value(self):
        broken = {
            "a list is not a body": [],
            "no results key": {"data": [{"label": "n"}]},
            "results is not a list": {"results": {"entities": []}},
            "an entry is not an object": {"results": ["x"]},
            "no id": {"results": [{"name": "n", "type": "t"}]},
            "no name": {"results": [{"entity_id": "i", "type": "t"}]},
            "popularity out of range": {"results": [{"entity_id": "i", "name": "n", "type": "t",
                                                    "popularity": 7}]},
            "popularity is text": {"results": [{"entity_id": "i", "name": "n", "type": "t",
                                               "popularity": "high"}]},
        }
        for label, body in broken.items():
            with self.assertRaises(qloo.UnknownShape, msg=label) as caught:
                qloo.read_search(body)
            self.assertNotIn("high", str(caught.exception))


class Tags(unittest.TestCase):
    def test_reads_tags(self):
        self.assertEqual(qloo.read_tags(fixture("doc-tags.json")), [
            {"id": "urn:tag:synthetic:stand-up", "name": "Stand-Up (synthetic)",
             "type": "urn:tag:synthetic:genre"},
        ])

    def test_empty_and_unknown(self):
        self.assertEqual(qloo.read_tags({"results": {"tags": []}}), [])
        self.assertEqual(qloo.read_tags({"results": {}}), [])
        self.assertEqual(qloo.read_tags({}), [])
        with self.assertRaises(qloo.UnknownShape):
            qloo.read_tags({"data": []})
        with self.assertRaises(qloo.UnknownShape):
            qloo.read_tags({"results": {"tags": [{"id": "x", "name": "n"}]}})
        with self.assertRaises(qloo.UnknownShape):
            qloo.read_tags({"results": {"other": []}})


class Insights(unittest.TestCase):
    def test_reads_popularity_tags_growth_and_explanation_scores(self):
        read = qloo.read_insights(fixture("doc-insights.json"))
        self.assertTrue(read["explainability"])
        first, second = read["entities"]
        self.assertEqual(first["entityId"], "00000000-0000-4000-8000-000000000201")
        self.assertEqual((first["popularity"], first["growth"], first["tags"]), (0.58, 0.04, ["Indie"]))
        self.assertEqual(first["explain"], [
            {"entityId": ANCHOR_1, "score": 0.34},
            {"entityId": ANCHOR_3, "score": 0.1},
            {"entityId": "00000000-0000-4000-8000-000000000104", "score": 0.05},
        ])
        self.assertEqual((second["popularity"], second["growth"], second["tags"], second["explain"]),
                         (None, None, [], []))

    def test_keeps_only_what_it_needs(self):
        first = qloo.read_insights(fixture("doc-insights.json"))["entities"][0]
        self.assertEqual(set(first), {"entityId", "name", "type", "popularity", "tags", "growth", "explain"})

    def test_a_response_without_explanations_says_so(self):
        body = fixture("doc-insights.json")
        for entity in body["results"]["entities"]:
            entity.pop("query", None)
        self.assertFalse(qloo.read_insights(body)["explainability"])

    def test_a_200_with_no_entities_is_no_results(self):
        for body in ({}, {"results": {}}, {"results": {"entities": []}}):
            read = qloo.read_insights(body)
            self.assertEqual(read["entities"], [])
            self.assertEqual(qloo.outcome(200, qloo.INSIGHTS, read["entities"]), "qloo_no_results")

    def test_a_shape_it_does_not_know_fails_loudly(self):
        def broken(change):
            body = copy.deepcopy(fixture("doc-insights.json"))
            change(body["results"]["entities"][0])
            return body

        cases = {
            "entities is not a list": {"results": {"entities": {"a": 1}}},
            "results has other keys only": {"results": {"people": []}},
            "no entity id": broken(lambda e: e.pop("entity_id")),
            "no type": broken(lambda e: e.pop("type")),
            "popularity is a percentage": broken(lambda e: e.update(popularity=62)),
            "tags is not a list": broken(lambda e: e.update(tags="indie")),
            "a tag has no name": broken(lambda e: e.update(tags=[{"id": "t"}])),
            "growth is text": broken(lambda e: e["query"]["measurements"].update(audience_growth="up")),
            "explainability is a list": broken(lambda e: e["query"].update(explainability=[])),
            "a score is out of range": broken(
                lambda e: e["query"]["explainability"]["signal.interests.entities"][0].update(score=3)),
            "a score is missing": broken(
                lambda e: e["query"]["explainability"]["signal.interests.entities"][0].pop("score")),
        }
        for label, body in cases.items():
            with self.assertRaises(qloo.UnknownShape, msg=label):
                qloo.read_insights(body)

    def test_the_error_names_a_path_not_a_value(self):
        body = copy.deepcopy(fixture("doc-insights.json"))
        body["results"]["entities"][0]["popularity"] = 62
        with self.assertRaises(qloo.UnknownShape) as caught:
            qloo.read_insights(body)
        self.assertEqual(caught.exception.where, "results.entities[].popularity")
        self.assertNotIn("62", str(caught.exception))


class Trending(unittest.TestCase):
    def test_reads_the_two_fields_the_rule_uses_and_the_rank(self):
        rows = qloo.read_trending(fixture("doc-trending.json"))
        self.assertEqual(rows[0], {"date": "2026-10-12", "population_percentile": 71.4,
                                   "population_rank": 120.0})
        self.assertEqual(len(rows), 3)

    def test_nothing_in_the_window_is_no_results(self):
        self.assertEqual(qloo.read_trending({"results": {"trends": []}}), [])

    def test_a_row_without_a_percentile_or_a_date_fails_loudly(self):
        for drop in ("population_percentile", "date"):
            body = copy.deepcopy(fixture("doc-trending.json"))
            del body["results"]["trends"][1][drop]
            with self.assertRaises(qloo.UnknownShape, msg=drop):
                qloo.read_trending(body)

    def test_a_bad_row_fails_loudly(self):
        for change in ({"population_percentile": "71"}, {"population_percentile": 140},
                       {"date": "last week"}, {"population_rank": -3}):
            body = copy.deepcopy(fixture("doc-trending.json"))
            body["results"]["trends"][0].update(change)
            with self.assertRaises(qloo.UnknownShape, msg=repr(change)):
                qloo.read_trending(body)


if __name__ == "__main__":
    unittest.main()
