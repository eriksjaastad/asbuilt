import random
import tokenize
import sys
import unittest
import warnings

from asbuilt.built import _text, python_file

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
    def test_code_python_accepts_has_no_note_when_utf8_and_is_not_run(self):
        sources = [b"", b"x = '\\d'\n", b"\xef\xbb\xbfx = 'caf\xc3\xa9'\n"]
        sources += [b"# coding: utf-8\nx = 1\n", b"# coding: utf-8-sig\nx = 1\n"]
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
        # Non-UTF-8 coding even when the body is ASCII (r3 FAIL was unicode_escape + dead code).
        sources += [b"#coding:latin-1\nx = 1\n", b"#coding:unicode_escape\n'\\d'\n"]
        # r3 FAIL case: under unicode_escape the backslash eats the newline, so Python
        # never sees hidden; tree-sitter on UTF-8 would. Gate refuses the coding line.
        sources += [
            b"# coding: unicode_escape\nif False:\n class A:\n  # c \\\n  def hidden(self): pass\n  pass\n"
        ]
        for source in sources:  # not UTF-8, or Python reads it as other text
            self.assertEqual(note(source), NOT_READ, source)
        misread = "tree-sitter could not read it all, though Python accepts it"
        new = misread if sys.version_info >= (3, 13) else "does not parse: line 1"
        self.assertTrue(note(b"class A[T: int = str]: pass\n").startswith(new))

    def test_text_for_tree_sitter_uses_python_newlines_and_refuses_cr_coding_lines(
        self,
    ):
        # CRLF and old Mac CR become LF so tree-sitter sees the same lines Python compiled.
        for source, want in (
            (b"x = 1\r\ny = 2\r\n", b"x = 1\ny = 2\n"),
            (b"x = 1\ry = 2\r", b"x = 1\ny = 2\n"),
        ):
            text, note_ = _text(source)
            self.assertEqual((text, note_), (want, ""), source)
        # A CR before a non-UTF-8 coding line: splitlines finds the cookie; split on LF misses it.
        source = b"\r#coding:latin-1\nx = 1\n"
        self.assertEqual(note(source), NOT_READ, source)

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
            # read only when tokenize names utf-8 or utf-8-sig and the bytes decode
            if not want:
                try:
                    lines = iter(source.splitlines(True)).__next__
                    encoding = tokenize.detect_encoding(lines)[0]
                    if encoding not in ("utf-8", "utf-8-sig"):
                        want = NOT_READ
                    else:
                        source.decode(encoding)
                except Exception:
                    want = NOT_READ
            self.assertEqual(got, want, source)
            seen.add(got[:15])
        self.assertEqual(
            seen, {"", "does not parse:", "does not parse ", "Python accepts "}
        )
