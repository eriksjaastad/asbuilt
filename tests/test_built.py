import random
import sys
import unittest
import warnings

from asbuilt.built import python_file

HEADS = [b""] * 7 + [b"# coding: %s\n" % c for c in b"latin-1 utf-7 utf-16 hex".split()]
HEADS += [b"# coding: unicode_escape\n", b"# coding: rot_13\n", b"# coding: nope\n"]
CODE = b"def f(|):|\n|\r\n|\r|  |pass|x = |'\\d'|\\ud800|+2AA-|\\|'|\xe9|\xff|\0|#"
NOT_READ = "Python accepts it; not UTF-8, so not read"
PIECES = CODE.split(b"|") + [b"\xef\xbb\xbf", b"caf\xc3\xa9", b"(" * 300]


def made(rand):
    """Seeded random bytes from pieces of code, bad bytes and escapes, maybe a coding line."""
    return rand.choice(HEADS) + b"".join(rand.choices(PIECES, k=rand.randrange(10)))


def note(source, name="t.py"):
    """The note on the box for these bytes, or "" when it has none; warnings raise here."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        box = python_file(name, source)
    labels = [label["text"] for label in box["labels"]]
    assert box["id"] == labels[0] == name and labels[1:] != [""] and len(labels) <= 2
    return "".join(labels[1:])


class PythonFileTest(unittest.TestCase):
    def test_code_python_accepts_has_no_note_whatever_its_encoding_and_is_not_run(self):
        sources = [b"", b"x = '\\d'\n", b"\xef\xbb\xbfx = 'caf\xc3\xa9'\n"]
        sources += [b"#coding:latin-1\nx = 1\n", b"#coding:unicode_escape\n'\\d'\n"]
        sources += [b"raise SystemExit\ntype L = int\nmatch x:\n case 1: pass\n"]
        sources += [b"x = 1\ry = 2\r\n", b"if 1:\r  x = 1\r"]  # old Mac line endings
        sources += [b"x = " + b"1+" * 3000 + b"1"]  # deep, and still read
        for source in sources:
            self.assertEqual(note(source), "", source)

    def test_code_python_rejects_is_noted_with_pythons_reason_and_line(self):
        cases = [  # the start of each note, as Python's words differ by version
            (b"def f(:\n pass\n", "does not parse: line 1 (Python: invalid syntax)"),
            (b"x = 1\ndef f():\n", "does not parse: line 2 (Python: expected an "),
            (b"return 1\n", "does not parse: line 1 (Python: 'return' outside "),
            (b"x = 1\0\n", "does not parse (Python: source code string cannot "),
            (b"# coding: nope\nx = 1\n", "does not parse (Python: unknown encoding"),
            (b"x = 1\ny = '\xe9'\n", "does not parse: line 2 (Python: "),
            (b"# coding: rot_13\nx = 1\n", "does not parse"),
        ]
        for source, want in cases:
            self.assertTrue(note(source).startswith(want), (source, note(source)))

    def test_code_python_accepts_but_tree_sitter_does_not_read_as_python_is_noted(self):
        sources = [b"#coding:latin-1\n\xe9 = 1\n", b"# coding: utf-7\nx = '+AGE-'\n"]
        sources += [b"# coding: unicode_escape\r\n# c \\\r\n!\r\n"]  # `!` unseen
        for source in sources:  # not UTF-8, or Python reads it as other text
            self.assertEqual(note(source), NOT_READ, source)
        misread = "tree-sitter could not read it all, though Python accepts it"
        new = misread if sys.version_info >= (3, 13) else "does not parse: line 1"
        self.assertTrue(note(b"class A[T: int = str]: pass\n").startswith(new))

    def test_nesting_of_any_depth_is_noted_not_a_crash(self):
        cases = [
            (b"(" * n + b"!" + b")" * n, ": line 1 (Python: ") for n in (3, 1200, 10**5)
        ]
        cases += [(b"1+" * 200_000 + b"1", " (Python: RecursionError: ")]
        cases += [(b"not " * 200_000 + b"1", " (Python: MemoryError: Parser stack")]
        for middle, want in cases:  # the type of error Python raised is named
            self.assertTrue(note(b"x = " + middle).startswith("does not parse" + want))

    def test_generated_bytes_get_pythons_verdict_never_a_crash(self):
        seen = set()
        for n in range(3000):
            source, name = made(random.Random(n)), f"P{n}/é.py"
            got = note(source, name)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    compile(source, "", "exec", dont_inherit=True)
                want = ""
            except SyntaxError as e:
                line = f": line {e.lineno}" if e.lineno else ""
                want = f"does not parse{line} (Python: {e.msg})"
            except Exception as e:
                want = f"does not parse (Python: {type(e).__name__}: {e})"
            # read if UTF-8, maybe not with a coding line, never if not UTF-8
            if not want:
                try:
                    source.decode("utf-8")
                    ok = {"", NOT_READ} if source.startswith(b"# coding: ") else {""}
                except UnicodeDecodeError:
                    ok = {NOT_READ}
                want = got if got in ok else ok
            self.assertEqual(got, want, source)
            seen.add(got[:15])
        self.assertEqual(
            seen, {"", "does not parse:", "does not parse ", "Python accepts "}
        )
