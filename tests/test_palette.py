import re
import unittest

from tests.support import ROOT

CSS = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
TEXT_RATIO = 4.5
CONTROL_RATIO = 3.0
SURFACES = ("bg", "card")


def blocks(text):
    """The (prelude, body) pairs at the top level of `text`."""
    found, depth, start, prelude, inner = [], 0, 0, "", 0
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                prelude, inner = text[start:index].strip(), index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                found.append((prelude, text[inner:index]))
                start = index + 1
    return found


def declarations(body):
    return {name.strip(): value.strip() for name, value in
            (part.split(":", 1) for part in body.split(";") if ":" in part)}


def tokens(body):
    return {name[2:]: value for name, value in declarations(body).items() if name.startswith("--")}


def token_of(value):
    found = re.fullmatch(r"var\(--([a-z-]+)\)", value or "")
    return found[1] if found else None


def schemes():
    light, dark = {}, {}
    for prelude, body in blocks(CSS):
        if prelude == ":root":
            light.update(tokens(body))
        elif prelude.startswith("@media (prefers-color-scheme: dark)"):
            for inner, rule in blocks(body):
                if inner == ":root":
                    dark.update(tokens(rule))
    return light, {**light, **dark}, dark


def rules():
    found = []
    for prelude, body in blocks(CSS):
        if not prelude.startswith("@") and prelude != ":root":
            found.append((prelude, declarations(body)))
    return found


def pairs():
    """Every (text token, surface token) the stylesheet can put together. A rule that names both gives
    one pair; a rule that names only a colour is checked on each page surface; a rule that names only
    a background has the page's text on it, unless it is an empty decoration (`content: ""`)."""
    seen = set()
    for _, declared in rules():
        if declared.get("content") == '""':
            continue
        text = token_of(declared.get("color"))
        surface = token_of(declared.get("background") or declared.get("background-color"))
        if text and surface:
            seen.add((text, surface))
        elif text:
            seen.update((text, each) for each in SURFACES)
        elif surface:
            seen.add(("text", surface))
    seen.add(("text", "bg"))
    return sorted(seen)


def luminance(hexed):
    channels = [int(hexed[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    low = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * low[0] + 0.7152 * low[1] + 0.0722 * low[2]


def ratio(first, second):
    bright, dim = sorted((luminance(first), luminance(second)), reverse=True)
    return (bright + 0.05) / (dim + 0.05)


LIGHT, DARK_ALL, DARK_ONLY = schemes()
SCHEMES = {"light": LIGHT, "dark": DARK_ALL}


class TheWcagContrastOfThePalette(unittest.TestCase):
    def test_the_parser_found_the_stylesheets_colours_so_the_checks_below_are_not_empty(self):
        self.assertGreaterEqual(len(LIGHT), 19)
        self.assertGreaterEqual(len(pairs()), 14)
        for pair in (("text", "bg"), ("muted", "card"), ("on-accent", "accent"), ("danger", "card"),
                     ("warn-text", "warn-bg"), ("spent-text", "spent-bg"), ("live-text", "live-bg"),
                     ("synthetic-text", "synthetic-bg")):
            self.assertIn(pair, pairs())

    def test_dark_mode_redefines_every_colour_token_the_light_scheme_has(self):
        self.assertEqual(set(DARK_ONLY), set(LIGHT))

    def test_every_colour_the_rules_use_is_defined(self):
        for name in {name for pair in pairs() for name in pair}:
            self.assertIn(name, LIGHT, name)
        for _, declared in rules():
            for value in declared.values():
                for used in re.findall(r"var\(--([a-z-]+)\)", value):
                    self.assertIn(used, LIGHT, used)

    def test_every_text_and_background_pair_is_at_least_4_5_to_1_in_both_schemes(self):
        for scheme, palette in SCHEMES.items():
            for text, surface in pairs():
                self.assertGreaterEqual(
                    ratio(palette[text], palette[surface]), TEXT_RATIO,
                    f"{scheme}: {text} {palette[text]} on {surface} {palette[surface]}")

    def test_borders_and_the_focus_ring_are_at_least_3_to_1_against_the_surfaces_in_both_schemes(self):
        for scheme, palette in SCHEMES.items():
            for control in ("line", "focus", "accent"):
                for surface in SURFACES:
                    self.assertGreaterEqual(
                        ratio(palette[control], palette[surface]), CONTROL_RATIO,
                        f"{scheme}: {control} {palette[control]} on {surface} {palette[surface]}")

    def test_the_status_colours_are_never_the_only_signal(self):
        banners = ("pending", "synthetic", "live", "spent", "warn")
        for name in banners:
            self.assertRegex(CSS, rf"\.banner\.{name}\s*\{{")
        index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for words in ("Synthetic sample data.", "Live: Qloo is being called", "Daily allowance spent",
                      "plain HTTP", "does not label requests"):
            self.assertIn(words, index)


class Motion(unittest.TestCase):
    def test_reduced_motion_switches_off_every_animation_and_transition(self):
        query = "@media (prefers-reduced-motion: reduce)"
        reduced = [body for prelude, body in blocks(CSS) if prelude == query]
        self.assertEqual(len(reduced), 1)
        inner = blocks(reduced[0])
        self.assertEqual(len(inner), 1)
        for property_name in ("animation", "transition"):
            self.assertRegex(inner[0][1], rf"{property_name}:\s*none\s*!important")
        self.assertRegex(reduced[0], r"\*,\s*\*::before,\s*\*::after")

    def test_the_one_animation_is_a_pulse_on_the_working_dot_and_nothing_else_moves(self):
        moving = [prelude for prelude, declared in rules()
                  if any(name.startswith(("animation", "transition")) for name in declared)]
        self.assertEqual(moving, [".status.working::before"])


class Reflow(unittest.TestCase):
    def test_nothing_in_the_stylesheet_fixes_a_width_wider_than_a_375_px_screen(self):
        for width in re.findall(r"(?<![a-z-])(?:min-)?width:\s*([0-9.]+)(px|rem)", CSS):
            amount, unit = float(width[0]), width[1]
            self.assertLessEqual(amount * (16 if unit == "rem" else 1), 343 + 1, f"{width}")
        self.assertNotRegex(CSS, r"[^-]min-width:\s*(?!2\.75rem)[0-9.]+(px|rem)")

    def test_long_words_wrap_instead_of_widening_the_page(self):
        self.assertIn("overflow-wrap: anywhere", CSS)
        self.assertRegex(CSS, r"body\s*\{[^}]*overflow-wrap:\s*break-word")
        self.assertRegex(CSS, r"pre\.text[^{]*\{[^}]*white-space:\s*pre-wrap")


if __name__ == "__main__":
    unittest.main()
