import json
import re
import unittest

from open_slot import pins
from tests.support import ROOT

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
RESERVED = "00000000-0000-4000-8000-"
SKIP_DIRS = {".git", ".cache", "out", "__pycache__", ".venv"}


def tracked_files():
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if path.is_file() and not SKIP_DIRS & set(relative.parts) and not path.name.startswith(".env") \
                and path.name != ".DS_Store":
            yield path


def text_of(path):
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


class Tree(unittest.TestCase):
    def test_no_id_outside_the_reserved_synthetic_range_anywhere_in_the_tree(self):
        for path in tracked_files():
            if path.name == "verify.sh":
                continue
            for found in UUID.findall(text_of(path)):
                self.assertTrue(found.startswith(RESERVED), f"{path.relative_to(ROOT)} holds {found}")

    def test_the_scan_catches_an_id_built_at_run_time(self):
        real = "-".join(["deadbeef", "dead", "4bad", "8bad", "deadbeefdead"])
        self.assertTrue(UUID.search(real))
        self.assertFalse(real.startswith(RESERVED))

    def test_no_key_header_with_a_value_and_no_key_setting_with_a_value(self):
        header = re.compile(r"x-api-key\"?[:=] *[\"']?[A-Za-z0-9_-]{20,}", re.I)
        setting = re.compile(r"QLOO_API_KEY=[A-Za-z0-9_-]{8,}")
        for path in tracked_files():
            if path.name == "verify.sh":
                continue
            text = text_of(path)
            self.assertIsNone(header.search(text), path.relative_to(ROOT))
            self.assertIsNone(setting.search(text), path.relative_to(ROOT))

    def test_the_example_settings_are_all_empty(self):
        for line in text_of(ROOT / ".env.example").splitlines():
            if re.match(r"^[A-Za-z_]+=", line):
                self.assertTrue(line.endswith("="), line)


class Scenarios(unittest.TestCase):
    ALLOWED = {"label", "capacity_note", "nights", "anchors", "lenses"}

    def test_scenario_files_hold_names_and_nights_only(self):
        files = sorted((ROOT / "scenarios").glob("*.json"))
        self.assertGreaterEqual(len(files), 2)
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(set(data) <= self.ALLOWED, f"{path.name}: {sorted(set(data) - self.ALLOWED)}")
            for key in ("label", "nights", "anchors"):
                self.assertIn(key, data)

            def strings(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, list):
                    for item in value:
                        yield from strings(item)

            for text in strings(data):
                self.assertIsNone(UUID.search(text), path.name)
                self.assertNotIn("urn:", text, path.name)
                self.assertLessEqual(len(text), 60, path.name)


class Pins(unittest.TestCase):
    def test_pins_holds_no_id_shaped_string_and_any_tag_id_is_a_urn_tag(self):
        text = (ROOT / "open_slot" / "pins.py").read_text(encoding="utf-8")
        self.assertIsNone(UUID.search(text))
        for default in pins.LENS_DEFAULTS.values():
            self.assertTrue(default["tag"] is None or default["tag"].startswith("urn:tag:"))

    def test_the_unverified_defaults_say_so(self):
        self.assertFalse(pins.VERIFIED)
        self.assertIn("UNVERIFIED", (ROOT / "open_slot" / "pins.py").read_text(encoding="utf-8"))


class Fixtures(unittest.TestCase):
    def test_every_synthetic_fixture_says_it_is_not_qloo_data_and_uses_reserved_ids_only(self):
        files = sorted((ROOT / "tests" / "fixtures" / "synthetic").glob("scenario-*.json"))
        self.assertEqual(len(files), 3)
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("not Qloo data", data["synthetic"])
            for found in UUID.findall(path.read_text(encoding="utf-8")):
                self.assertTrue(found.startswith(RESERVED), path.name)


if __name__ == "__main__":
    unittest.main()
