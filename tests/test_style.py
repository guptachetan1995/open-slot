import ast
import unittest

from tests.support import ROOT

SOURCES = sorted(list((ROOT / "open_slot").glob("*.py")) + list((ROOT / "tests").glob("*.py")))


class Style(unittest.TestCase):
    def test_no_tabs_no_trailing_whitespace_no_line_over_110_characters(self):
        for path in SOURCES:
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                where = f"{path.relative_to(ROOT)}:{number}"
                self.assertNotIn("\t", line, where)
                self.assertEqual(line, line.rstrip(), f"trailing whitespace at {where}")
                self.assertLessEqual(len(line), 110, f"{where} is {len(line)} characters")

    def test_the_files_end_with_one_newline(self):
        for path in SOURCES:
            text = path.read_text(encoding="utf-8")
            if text:
                where = str(path.relative_to(ROOT))
                self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), where)

    def test_no_leftover_debug_print_in_the_package(self):
        for path in (ROOT / "open_slot").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                    self.fail(f"print() at {path.name}:{node.lineno}; write to sys.stdout or sys.stderr")

    def test_the_package_names_neither_the_plan_file_nor_the_private_tree(self):
        for path in SOURCES:
            text = path.read_text(encoding="utf-8")
            if path.name == "test_style.py":
                continue
            for word in ("SP" + "EC.md", "entries/", "bin/publish", "bin/pages", "bin/ready"):
                self.assertNotIn(word, text, f"{path.name} mentions {word}, which the published copy lacks")


if __name__ == "__main__":
    unittest.main()
