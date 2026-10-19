import unittest
from datetime import date

from open_slot import qloo

A, B, C = (f"00000000-0000-4000-8000-00000000010{n}" for n in (1, 2, 3))
TAG = "urn:tag:synthetic:stand-up"


class Allowlist(unittest.TestCase):
    def test_insights_sends_only_allowlisted_parameters(self):
        request = qloo.insights(qloo.ARTIST, [A, B], exclude=[A, B], tag=TAG, bias="medium",
                                popularity=(0.4, 0.8), take=50)
        self.assertEqual(request.path, "/v2/insights")
        self.assertEqual(set(request.params), {
            "filter.type", "signal.interests.entities", "filter.exclude.entities", "filter.tags",
            "bias.trends", "filter.popularity.min", "filter.popularity.max",
            "feature.explainability", "take",
        })
        self.assertTrue(set(request.params) <= qloo.ALLOWED[qloo.INSIGHTS][qloo.ARTIST])

    def test_the_wide_call_sends_no_popularity_filter(self):
        request = qloo.insights(qloo.PODCAST, [A, B], exclude=[A, B])
        self.assertNotIn("filter.popularity.min", request.params)
        self.assertNotIn("filter.popularity.max", request.params)
        self.assertEqual(request.params["feature.explainability"], "true")
        self.assertEqual(request.params["take"], "50")

    def test_an_unknown_parameter_is_refused_at_build_time(self):
        for name in ("filter.invented", "signal.demographics.age", "signal.demographics.audiences",
                     "filter.location", "bias.popularity"):
            with self.assertRaises(qloo.RequestRefused, msg=name):
                qloo.build(qloo.INSIGHTS, {"filter.type": qloo.ARTIST,
                                           "signal.interests.entities": [A], name: "x"})

    def test_a_demographics_parameter_is_never_built_by_any_helper(self):
        requests = [
            qloo.search("a name"), qloo.tags("folk"),
            qloo.insights(qloo.ARTIST, [A, B], exclude=[A], tag=TAG, bias="high", popularity=(0, 1)),
            qloo.trending(qloo.PERSON, A, "2026-09-01", "2026-10-19"),
        ]
        for request in requests:
            for name in request.params:
                self.assertNotIn("demographics", name)
                self.assertNotIn("audiences", name)

    def test_a_weight_object_is_refused(self):
        for weights in ([{"entity": A, "weight": 2}], {A: 2}, [(A, 2)], A + ":2"):
            with self.assertRaises(qloo.RequestRefused, msg=repr(weights)):
                qloo.build(qloo.INSIGHTS, {"filter.type": qloo.ARTIST, "signal.interests.entities": weights})

    def test_a_comma_joined_string_is_not_a_list_of_ids(self):
        with self.assertRaises(qloo.RequestRefused):
            qloo.build(qloo.INSIGHTS, {"filter.type": qloo.ARTIST, "signal.interests.entities": f"{A},{B}"})
        with self.assertRaises(qloo.RequestRefused):
            qloo.build(qloo.INSIGHTS, {"filter.type": qloo.ARTIST, "signal.interests.entities": [f"{A},{B}"]})

    def test_an_unsupported_filter_type_or_path_is_refused(self):
        with self.assertRaises(qloo.RequestRefused):
            qloo.build(qloo.INSIGHTS, {"filter.type": "urn:entity:movie", "signal.interests.entities": [A]})
        with self.assertRaises(qloo.RequestRefused):
            qloo.build(qloo.INSIGHTS, {"filter.type": "urn:demographics", "signal.interests.entities": [A]})
        for path in ("/recommendations", "/recs", "/v2/insights/search", "/v2/audiences", "/"):
            with self.assertRaises(qloo.RequestRefused, msg=path):
                qloo.build(path, {})

    def test_the_required_parameters_are_required(self):
        with self.assertRaises(qloo.RequestRefused):
            qloo.build(qloo.INSIGHTS, {"filter.type": qloo.ARTIST})
        with self.assertRaises(qloo.RequestRefused):
            qloo.build(qloo.TRENDING, {"filter.type": qloo.ARTIST, "signal.interests.entities": [A]})
        with self.assertRaises(qloo.RequestRefused):
            qloo.build(qloo.SEARCH, {"take": 5})


class Values(unittest.TestCase):
    def test_search_uses_the_comma_separated_types_form(self):
        request = qloo.search("Birchwater Duo")
        self.assertEqual(request.path, "/search")
        self.assertEqual(request.params["types"], "urn:entity:artist,urn:entity:podcast,urn:entity:person")
        self.assertEqual(request.params["take"], "5")
        self.assertEqual(
            request.url("https://x.example"),
            "https://x.example/search?query=Birchwater%20Duo"
            "&types=urn:entity:artist,urn:entity:podcast,urn:entity:person&take=5",
        )

    def test_a_search_name_is_bounded_and_cleaned(self):
        self.assertEqual(qloo.search("  two   spaces\n").params["query"], "two spaces")
        for bad in ("", "   ", "x" * 61, "nul\x00byte", "bell\x07", 5, None):
            with self.assertRaises(qloo.RequestRefused, msg=repr(bad)):
                qloo.search(bad)
        qloo.search("x" * 60)

    def test_a_name_that_looks_like_markup_is_encoded_not_interpreted(self):
        url = qloo.search("a&take=50&b=c").url("https://x.example")
        self.assertEqual(url.count("&take="), 1)
        self.assertIn("a%26take%3D50%26b%3Dc", url)

    def test_take_is_one_to_fifty(self):
        for good in (1, 50):
            qloo.search("x", take=good)
        for bad in (0, 51, 5.0, "5", True):
            with self.assertRaises(qloo.RequestRefused, msg=repr(bad)):
                qloo.search("x", take=bad)

    def test_popularity_is_a_fraction_with_min_not_above_max(self):
        request = qloo.insights(qloo.ARTIST, [A], popularity=(0.4, 0.8))
        self.assertEqual((request.params["filter.popularity.min"], request.params["filter.popularity.max"]),
                         ("0.4", "0.8"))
        for bad in ((0.8, 0.4), (-0.1, 0.5), (0.1, 1.1), ("0.1", 0.5), (True, 0.5)):
            with self.assertRaises(qloo.RequestRefused, msg=repr(bad)):
                qloo.insights(qloo.ARTIST, [A], popularity=bad)

    def test_a_tag_must_be_a_tag_id(self):
        for bad in ("stand-up", "comedian", "urn:entity:artist", "urn:tag:", "urn:tag:a b", f"{TAG},{TAG}"):
            with self.assertRaises(qloo.RequestRefused, msg=bad):
                qloo.insights(qloo.PERSON, [A], tag=bad)
        qloo.insights(qloo.PERSON, [A], tag=TAG)

    def test_the_trend_level_is_one_of_the_documented_ones(self):
        for level in qloo.BIAS_LEVELS:
            qloo.insights(qloo.ARTIST, [A], bias=level)
        with self.assertRaises(qloo.RequestRefused):
            qloo.insights(qloo.ARTIST, [A], bias="extreme")

    def test_trending_takes_exactly_one_entity_and_two_dates(self):
        request = qloo.trending(qloo.PODCAST, A, date(2026, 9, 1), "2026-10-19")
        self.assertEqual(request.params["signal.interests.entities"], A)
        self.assertEqual((request.params["filter.start_date"], request.params["filter.end_date"]),
                         ("2026-09-01", "2026-10-19"))
        for many in ([A, B], []):
            with self.assertRaises(qloo.RequestRefused):
                qloo.build(qloo.TRENDING, {
                    "filter.type": qloo.PODCAST, "signal.interests.entities": many,
                    "filter.start_date": "2026-09-01", "filter.end_date": "2026-10-19",
                })
        for bad in (("2026-10-19", "2026-09-01"), ("soon", "2026-10-19"), ("2026-9-1", "2026-10-19")):
            with self.assertRaises(qloo.RequestRefused, msg=repr(bad)):
                qloo.trending(qloo.PODCAST, A, *bad)

    def test_up_to_six_anchors_and_no_more(self):
        ids = [f"00000000-0000-4000-8000-0000000002{n:02d}" for n in range(7)]
        qloo.insights(qloo.ARTIST, ids[:6])
        with self.assertRaises(qloo.RequestRefused):
            qloo.insights(qloo.ARTIST, ids)

    def test_the_cache_key_is_stable_and_holds_only_the_request(self):
        one = qloo.build(qloo.INSIGHTS, {"filter.type": qloo.ARTIST, "bias.trends": "high",
                                         "signal.interests.entities": [A, B]})
        two = qloo.build(qloo.INSIGHTS, {"signal.interests.entities": [A, B], "bias.trends": "high",
                                         "filter.type": qloo.ARTIST})
        self.assertEqual(one.cache_key(), two.cache_key())
        self.assertNotIn("key", one.cache_key().lower().replace("filter", ""))
        self.assertNotEqual(one.cache_key(), qloo.insights(qloo.ARTIST, [A, B], bias="low").cache_key())


class Outcomes(unittest.TestCase):
    def test_status_codes_map_to_the_named_outcomes(self):
        cases = {
            (401, qloo.INSIGHTS): "qloo_auth",
            (403, qloo.INSIGHTS): "qloo_unsupported_type",
            (404, qloo.SEARCH): "qloo_no_results",
            (404, qloo.INSIGHTS): "qloo_bad_request",
            (400, qloo.INSIGHTS): "qloo_bad_request",
            (429, qloo.INSIGHTS): "qloo_rate_limited",
            (500, qloo.INSIGHTS): "qloo_unavailable",
            (503, qloo.TRENDING): "qloo_unavailable",
            (200, qloo.INSIGHTS): "ok",
        }
        for (status, path), expected in cases.items():
            self.assertEqual(qloo.outcome(status, path), expected, (status, path))

    def test_a_200_with_nothing_in_it_is_no_results_and_not_a_failure(self):
        self.assertEqual(qloo.outcome(200, qloo.INSIGHTS, []), "qloo_no_results")
        self.assertEqual(qloo.outcome(200, qloo.INSIGHTS, [1]), "ok")

    def test_every_refusal_outcome_has_a_fixed_message_without_a_url(self):
        for code in ("qloo_auth", "qloo_unsupported_type", "qloo_rate_limited", "qloo_bad_request",
                     "qloo_unavailable", "qloo_no_results"):
            self.assertNotIn("http", qloo.OUTCOME_MESSAGES[code])


if __name__ == "__main__":
    unittest.main()
