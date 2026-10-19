import unittest
from datetime import date, timedelta
from unittest import mock

from open_slot import pins, trend

EPS = pins.TREND_EPS


def rows(*percentiles):
    """Weekly rows for these percentiles, written from the oldest week to the newest."""
    return [{"date": (date(2026, 8, 3) + timedelta(weeks=i)).isoformat(),
             "population_percentile": p, "population_rank": 1}
            for i, p in enumerate(percentiles)]


def from_changes(*changes, start=50.0):
    """Changes written oldest first; returns four rows whose week-over-week changes are those."""
    values = [start]
    for change in changes:
        values.append(values[-1] + change)
    return rows(*values)


class States(unittest.TestCase):
    def test_two_of_three_changes_above_eps_with_the_newest_not_falling_is_rising(self):
        self.assertEqual(trend.classify(from_changes(1.0, 1.0, 0.0))["state"], "rising")
        self.assertEqual(trend.classify(from_changes(-0.1, 1.0, 1.0))["state"], "rising")
        self.assertEqual(trend.classify(from_changes(1.0, -0.1, 1.0))["state"], "rising")

    def test_two_of_three_below_minus_eps_with_the_newest_not_rising_is_fading(self):
        self.assertEqual(trend.classify(from_changes(-1.0, -1.0, 0.0))["state"], "fading")
        self.assertEqual(trend.classify(from_changes(0.1, -1.0, -1.0))["state"], "fading")

    def test_one_of_three_is_not_enough(self):
        self.assertEqual(trend.classify(from_changes(1.0, 0.0, 0.0))["state"], "steady")
        self.assertEqual(trend.classify(from_changes(0.0, -1.0, 0.0))["state"], "steady")
        self.assertEqual(trend.classify(from_changes(0.0, 0.0, 1.0))["state"], "steady")

    def test_the_newest_change_can_veto_a_direction(self):
        self.assertEqual(trend.classify(from_changes(1.0, 1.0, -0.2))["state"], "steady")
        self.assertEqual(trend.classify(from_changes(-1.0, -1.0, 0.2))["state"], "steady")

    def test_the_newest_change_zero_negative_and_positive(self):
        self.assertEqual(trend.classify(from_changes(1.0, 1.0, 0.0))["state"], "rising")
        self.assertEqual(trend.classify(from_changes(1.0, 1.0, -EPS / 2))["state"], "steady")
        self.assertEqual(trend.classify(from_changes(-1.0, -1.0, EPS / 2))["state"], "steady")
        self.assertEqual(trend.classify(from_changes(-1.0, -1.0, 0.0))["state"], "fading")

    def test_exactly_at_eps_is_not_movement_and_just_beyond_it_is(self):
        self.assertEqual(trend.classify(from_changes(EPS, EPS, EPS))["state"], "steady")
        self.assertEqual(trend.classify(from_changes(-EPS, -EPS, -EPS))["state"], "steady")
        self.assertEqual(trend.classify(from_changes(EPS + 0.01, EPS + 0.01, 0))["state"], "rising")
        self.assertEqual(trend.classify(from_changes(-EPS - 0.01, -EPS - 0.01, 0))["state"], "fading")
        self.assertEqual(trend.classify(from_changes(EPS - 0.01, EPS - 0.01, EPS - 0.01))["state"], "steady")

    def test_float_noise_does_not_cross_the_line(self):
        rising_by_eps = rows(85.2, 85.2 + 0.5, 85.2 + 1.0, 85.2 + 1.5)
        with mock.patch.object(pins, "TREND_EPS", 0.5):
            self.assertEqual(trend.classify(rising_by_eps)["state"], "steady")

    def test_fewer_than_three_rows_is_unknown(self):
        self.assertEqual(trend.classify([])["state"], "unknown")
        self.assertEqual(trend.classify(rows(50.0))["state"], "unknown")
        self.assertEqual(trend.classify(rows(50.0, 60.0))["state"], "unknown")
        self.assertEqual(trend.classify(rows(50.0, 60.0, 70.0))["state"], "rising")

    def test_with_three_rows_both_changes_must_agree(self):
        self.assertEqual(trend.classify(rows(50.0, 51.0, 51.0))["state"], "steady")
        self.assertEqual(trend.classify(rows(50.0, 49.0, 48.0))["state"], "fading")

    def test_only_the_latest_four_weeks_are_read(self):
        old_falls_then_rises = rows(90.0, 80.0, 70.0, 60.0, 61.0, 62.0, 63.0)
        result = trend.classify(old_falls_then_rises)
        self.assertEqual((result["state"], result["weeks"], len(result["rows"])), ("rising", 4, 4))
        self.assertEqual(result["rows"][0]["population_percentile"], 63.0)

    def test_rows_arriving_unsorted_give_the_same_answer(self):
        ordered = from_changes(1.0, 1.0, 1.0)
        for arrangement in (ordered, ordered[::-1], [ordered[2], ordered[0], ordered[3], ordered[1]]):
            result = trend.classify(arrangement)
            self.assertEqual(result["state"], "rising")
            self.assertEqual([row["date"] for row in result["rows"]], sorted(
                (row["date"] for row in ordered), reverse=True))

    def test_the_result_carries_the_arithmetic_and_the_rule(self):
        result = trend.classify(from_changes(1.0, 2.0, 3.0, start=50.0))
        self.assertEqual(result["changes"], [3.0, 2.0, 1.0])
        self.assertIn("TREND_MIN_MOVES=2 of 3 changes", result["rule"])
        self.assertEqual(result["rows"][0]["population_percentile"], 56.0)


class Shape(unittest.TestCase):
    def test_a_row_without_a_percentile_fails_loudly(self):
        bad = rows(50.0, 51.0, 52.0, 53.0)
        del bad[1]["population_percentile"]
        with self.assertRaises(trend.TrendRowError):
            trend.classify(bad)

    def test_a_non_numeric_percentile_fails_loudly(self):
        bad = rows(50.0, "51", 52.0, 53.0)
        with self.assertRaises(trend.TrendRowError):
            trend.classify(bad)

    def test_the_fields_come_from_the_pins(self):
        self.assertEqual((trend.DATE, trend.PERCENTILE), pins.TREND_FIELDS)

    def test_the_newest_percentile_on_the_popularity_scale(self):
        self.assertAlmostEqual(trend.newest_fraction(rows(50.0, 71.4, 60.0)), 0.60)
        self.assertAlmostEqual(trend.newest_fraction(rows(60.0, 50.0, 71.4)), 0.714)


if __name__ == "__main__":
    unittest.main()
