import sys
import unittest
import warnings

from asbuilt.built import python_file


def note(source):
    """The note on the file box for these bytes, or "" when it has none."""
    labels = [label["text"] for label in python_file("t.py", source)["labels"]]
    assert labels[0] == "t.py" and labels[1:] != [""] and len(labels) <= 2
    return "".join(labels[1:])


class PythonFileTest(unittest.TestCase):
    def test_code_python_accepts_has_no_note_whatever_its_encoding(self):
        sources = [b"", b"x = 1\n", b"\xef\xbb\xbfx = 'caf\xc3\xa9'\n"]
        sources += [b"# coding: latin-1\ncaf\xe9 = 1\n", b"type L[T] = list[T]\n"]
        sources += [b"match x:\n    case [1, *r]: pass\ntry: pass\nexcept* E: pass\n"]
        for source in sources:
            self.assertEqual(note(source), "", source)

    def test_a_warning_is_not_an_error(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            self.assertEqual(note(b"x = '\\d'\n"), "")

    def test_code_python_rejects_is_noted_with_pythons_reason_and_line(self):
        cases = [  # the start of each note, as Python's words differ by version
            (b"def f(:\n pass\n", "does not parse: line 1 (Python: invalid syntax)"),
            (b"x = 1\ndef f():\n", "does not parse: line 2 (Python: expected an "),
            (b"return 1\n", "does not parse: line 1 (Python: 'return' outside "),
            (b"x = 1\0\n", "does not parse (Python: source code string cannot "),
            (b"# coding: nope\nx = 1\n", "does not parse (Python: unknown encoding"),
            (b"x = 1\ny = '\xe9'\n", "does not parse: line 2 (Python: "),
        ]
        for source, want in cases:
            self.assertTrue(note(source).startswith(want), (source, note(source)))

    def test_code_tree_sitter_misreads_is_noted_as_its_own(self):
        got = note(b"class A[T: int = str]: pass\n")  # a default, new in Python 3.13
        if sys.version_info >= (3, 13):
            self.assertEqual(got, "tree-sitter could not read it all")
        else:
            self.assertEqual(got, "does not parse: line 1 (Python: invalid syntax)")

    def test_nesting_of_any_depth_is_noted_not_a_crash(self):
        sources = [b"(" * n + b"!" + b")" * n for n in (3, 1200, 100_000)]
        sources += [b"1+" * 200_000 + b"1", b"not " * 200_000 + b"1"]
        for middle in sources:
            got = note(b"def f(): pass\nx = " + middle)
            self.assertTrue(got.startswith("does not parse"), got[:80])
