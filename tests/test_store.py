import copy
import json
import unittest

from open_slot import pins, store
from tests.support import AT, lantern_programme

ARGS = {
    "label": "The Lantern Room (fictional)",
    "nights": ["Tue 3 Nov", "Tue 10 Nov", "Tue 17 Nov"],
    "anchors": ["One", "Two", "Three", "Four"],
}


def refused(test, code, call, *args, **kwargs):
    with test.assertRaises(store.Refusal) as caught:
        call(*args, **kwargs)
    test.assertEqual(caught.exception.code, code)


class Workspaces(unittest.TestCase):
    def test_functions_return_a_new_document_and_leave_the_old_one_alone(self):
        empty = store.empty_document({"mode": "fake"})
        before = copy.deepcopy(empty)
        doc = store.new_workspace(empty, "ws_1", "tok", AT)
        self.assertEqual(empty, before)
        self.assertEqual([w["id"] for w in doc["workspaces"]], ["ws_1"])
        self.assertEqual(doc["server"], {"mode": "fake"})
        touched = store.touch(doc, "ws_1", "2026-10-19T13:00:00+05:30")
        self.assertEqual(store.workspace(doc, "ws_1")["lastSeenAt"], AT)
        self.assertEqual(store.workspace(touched, "ws_1")["lastSeenAt"], "2026-10-19T13:00:00+05:30")

    def test_a_workspace_has_the_documented_fields(self):
        fresh = store.workspace(store.new_workspace(store.empty_document(), "ws_1", "tok", AT), "ws_1")
        self.assertEqual(set(fresh), {"id", "agentToken", "createdAt", "lastSeenAt", "agentCalls24h",
                                      "programmes", "activityLog"})

    def test_an_unknown_workspace_is_refused(self):
        refused(self, "no_workspace", store.workspace, store.empty_document(), "ws_x")
        refused(self, "no_workspace", store.touch, store.empty_document(), "ws_x", AT)

    def test_the_least_recently_seen_workspace_is_forgotten_when_the_table_is_full(self):
        doc = store.empty_document()
        for n in range(store.MAX_WORKSPACES):
            doc = store.new_workspace(doc, f"ws_{n}", "t", f"2026-10-19T12:{n // 60:02d}:{n % 60:02d}+05:30")
        doc = store.touch(doc, "ws_0", "2026-10-19T20:00:00+05:30")
        doc = store.new_workspace(doc, "ws_new", "t", "2026-10-19T21:00:00+05:30")
        ids = {w["id"] for w in doc["workspaces"]}
        self.assertEqual(len(ids), store.MAX_WORKSPACES)
        self.assertIn("ws_0", ids)
        self.assertIn("ws_new", ids)
        self.assertNotIn("ws_1", ids)

    def test_idle_workspaces_are_pruned_after_the_ttl(self):
        doc = store.new_workspace(store.empty_document(), "old", "t", "2026-10-19T10:00:00+05:30")
        doc = store.new_workspace(doc, "recent", "t", "2026-10-19T11:30:00+05:30")
        kept = store.prune(doc, "2026-10-19T12:00:00+05:30", 7200)
        self.assertEqual([w["id"] for w in kept["workspaces"]], ["recent"])
        self.assertEqual(len(doc["workspaces"]), 2)
        self.assertEqual(len(store.prune(doc, "2026-10-19T11:59:59+05:30", 7200)["workspaces"]), 2)


class Programmes(unittest.TestCase):
    def open(self, **changes):
        doc = store.new_workspace(store.empty_document(), "ws_1", "tok", AT)
        return store.new_programme(doc, "ws_1", {**ARGS, **changes}, 16, AT)

    def test_a_new_programme_has_the_documented_shape(self):
        doc, programme_id = self.open(capacity_note="300 seats")
        found = store.programme(doc, "ws_1", programme_id)
        self.assertEqual(programme_id, "prog_001")
        self.assertEqual(set(found), {
            "id", "revision", "provider", "label", "capacityNote", "nights", "anchors", "knownTags",
            "lenses", "range", "budget", "candidates", "inquiries", "trace",
        })
        self.assertEqual(found["nights"][1], {"id": "n2", "label": "Tue 10 Nov", "state": "open",
                                              "proposed": None, "assigned": None})
        self.assertEqual(found["anchors"][0], {"name": "One", "candidates": [], "confirmed": None})
        self.assertEqual(found["lenses"], list(pins.CONFIRMED_LENSES))
        self.assertEqual(found["budget"], {"cap": 16, "spent": 0, "saved": 0, "stoppedBecause": None})
        self.assertEqual((found["capacityNote"], found["range"], found["revision"]), ("300 seats", None, 0))

    def test_programmes_are_numbered_per_workspace(self):
        doc, first = self.open()
        doc, second = store.new_programme(doc, "ws_1", ARGS, 16, AT)
        self.assertEqual((first, second), ("prog_001", "prog_002"))
        refused(self, "unknown_programme", store.programme, doc, "ws_1", "prog_009")

    def test_the_whole_document_round_trips_through_json(self):
        doc, _ = lantern_programme()
        doc = store.log_activity(doc, "ws_1", AT, "agent", "find_anchor", {"programme_id": "prog_001"}, "ok")
        doc = store.log_call(doc, {"ts": AT, "provider": "fake", "path": "/search", "cache": "miss",
                                   "ms": 3, "startedBy": "agent", "workspace": "ws_1"})
        self.assertEqual(json.loads(json.dumps(doc)), doc)

    def test_the_limits_of_open_programme(self):
        bad = {
            "label": ["", "x" * 61, 5, None],
            "nights": [[], ["n"] * 7, ["x" * 41], ["Tue", "tue"], "Tue 3 Nov", [5]],
            "anchors": [["One"], ["a"] * 7, ["x" * 61], ["One", "one"], "One, Two"],
            "capacity_note": ["x" * 41, 5, ""],
            "lenses": [[], ["jazz"], ["music", "music"], "music"],
        }
        for field, values in bad.items():
            for value in values:
                with self.assertRaises(store.Refusal, msg=f"{field}={value!r}") as caught:
                    self.open(**{field: value})
                self.assertEqual(caught.exception.code, "bad_args")

    def test_the_limits_at_their_edges_are_accepted(self):
        self.open(label="x" * 60, nights=["n" * 40] + [f"n{i}" for i in range(5)],
                  anchors=["a" * 60] + [f"a{i}" for i in range(5)], capacity_note="c" * 40)

    def test_text_is_cleaned_and_control_characters_are_refused(self):
        doc, programme_id = self.open(label="  The   Lantern\nRoom ")
        self.assertEqual(store.programme(doc, "ws_1", programme_id)["label"], "The Lantern Room")
        refused(self, "bad_args", self.open, label="bell\x07")

    def test_lenses_default_to_the_confirmed_ones_and_cannot_be_anything_else(self):
        doc, programme_id = self.open(lenses=["comedy"])
        self.assertEqual(store.programme(doc, "ws_1", programme_id)["lenses"], ["comedy"])

    def test_a_pinned_lens_tag_is_known_without_a_search_and_an_unpinned_one_is_not(self):
        doc, programme_id = self.open()
        self.assertEqual(store.programme(doc, "ws_1", programme_id)["knownTags"], [])
        original = pins.LENS_DEFAULTS["comedy"]
        try:
            pins.LENS_DEFAULTS["comedy"] = {**original, "tag": "urn:tag:synthetic:stand-up"}
            doc, programme_id = self.open()
        finally:
            pins.LENS_DEFAULTS["comedy"] = original
        self.assertEqual(store.programme(doc, "ws_1", programme_id)["knownTags"],
                         [{"id": "urn:tag:synthetic:stand-up", "name": "comedy", "type": None}])

    def test_replacing_a_programme_adds_one_to_its_revision_and_leaves_the_old_document_alone(self):
        doc, found = lantern_programme()
        changed = {**found, "label": "Changed"}
        after = store.replace_programme(doc, "ws_1", changed)
        self.assertEqual(store.programme(after, "ws_1", "prog_001")["revision"], found["revision"] + 1)
        self.assertEqual(store.programme(after, "ws_1", "prog_001")["label"], "Changed")
        self.assertEqual(store.programme(doc, "ws_1", "prog_001")["label"], "The Lantern Room (fictional)")
        refused(self, "unknown_programme", store.replace_programme, doc, "ws_1", {**found, "id": "prog_404"})

    def test_a_change_to_one_programme_does_not_touch_another(self):
        doc, first = self.open()
        doc, second = store.new_programme(doc, "ws_1", ARGS, 16, AT)
        after = store.replace_programme(doc, "ws_1", {**store.programme(doc, "ws_1", first), "label": "X"})
        self.assertEqual(store.programme(after, "ws_1", second), store.programme(doc, "ws_1", second))


class Logs(unittest.TestCase):
    def test_the_activity_log_keeps_the_latest_entries_only(self):
        doc = store.new_workspace(store.empty_document(), "ws_1", "tok", AT)
        for n in range(store.ACTIVITY_MAX + 5):
            doc = store.log_activity(doc, "ws_1", AT, "person", "confirm_anchor", {"n": n}, "ok")
        log = store.workspace(doc, "ws_1")["activityLog"]
        self.assertEqual((len(log), log[0]["args"], log[-1]["args"]),
                         (store.ACTIVITY_MAX, {"n": 5}, {"n": store.ACTIVITY_MAX + 4}))
        self.assertEqual(log[0], {"ts": AT, "actor": "person", "tool": "confirm_anchor",
                                  "args": {"n": 5}, "outcome": "ok"})

    def test_the_call_log_is_bounded_and_global(self):
        doc = store.empty_document()
        for n in range(store.CALLS_MAX + 3):
            doc = store.log_call(doc, {"n": n})
        self.assertEqual((len(doc["calls"]), doc["calls"][0]["n"]), (store.CALLS_MAX, 3))


class Anchors(unittest.TestCase):
    def test_only_confirmed_anchors_are_returned_with_the_entity_confirmed(self):
        _, found = lantern_programme()
        found["anchors"][1]["confirmed"] = None
        found["anchors"][2]["candidates"].insert(0, {"entityId": "00000000-0000-4000-8000-000000000999",
                                                     "name": "Other", "type": "t", "popularity": 0.1,
                                                     "disambiguation": None})
        anchors = store.confirmed_anchors(found)
        self.assertEqual([a["name"] for a in anchors], ["The Folk Duo", "The Stand-Up", "The Live Podcast"])
        self.assertEqual(anchors[1]["resolvedName"], "Resolved Stand-Up")
        self.assertEqual(anchors[1]["popularity"], 0.55)


if __name__ == "__main__":
    unittest.main()
