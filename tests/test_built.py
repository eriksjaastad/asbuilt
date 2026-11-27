import contextlib
import io
import json
import random
import sys
import tempfile
import tokenize
import unittest
import warnings
from pathlib import Path

import asbuilt
from asbuilt.built import _text, main, python_file
from asbuilt.compare import compare

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
    def test_top_level_functions_and_classes_become_boxes_on_their_lines(self):
        lines = [
            "def one():",
            "    return 1",
            "",
            "async def two():",
            "    return 2",
            "",
            "@dec",
            "def three():",
            "    return 3",
            "",
            "class Box:",
            "    def hidden(self):",
            "        return 4",
            "",
            "def holder():",
            "    def inner():",
            "        return 5",
            "",
            "if True:",
            "    def skipped():",
            "        return 0",
        ]
        box = python_file("t.py", ("\n".join(lines) + "\n").encode())
        kids = box["children"]
        self.assertEqual(
            [kid["labels"][0]["text"] for kid in kids],
            ["one()", "two()", "three()", "Box", "holder()"],
        )
        self.assertEqual(
            [kid["asbuilt"]["lines"] for kid in kids],
            [[1, 2], [4, 5], [7, 9], [11, 13], [15, 17]],
        )
        self.assertEqual(
            [kid["id"] for kid in kids],
            ["t.py:one:1", "t.py:two:4", "t.py:three:7", "t.py:Box:11", "t.py:holder:15"],
        )

    def test_code_python_accepts_has_no_note_when_utf8_and_is_not_run(self):
        sources = [b"", b"x = '\\d'\n", b"\xef\xbb\xbfx = 'caf\xc3\xa9'\n"]
        sources += [b"# coding: utf-8\nx = 1\n", b"# coding: utf-8-sig\nx = 1\n"]
        sources += [b"raise SystemExit\ntype L = int\nmatch x:\n case 1: pass\n"]
        sources += [b"x = 1\ry = 2\r\n", b"if 1:\r  x = 1\r"]  # old Mac line endings
        sources += [b"x = " + b"1+" * 3000 + b"1"]  # deep, and still read
        for source in sources:
            self.assertEqual(note(source), "", source)

    def test_as_builts_own_files_each_get_a_box_with_no_note(self):
        # As-Built's own repo is its first test project: its real files, not generated ones.
        files = sorted(Path(asbuilt.__file__).parent.rglob("*.py"))
        self.assertGreater(len(files), 10)
        for path in files:
            self.assertEqual(note(path.read_bytes(), path.name), "", path)

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

    def test_nesting_of_any_depth_is_noted_not_a_crash(self):
        cases = [
            (b"(" * n + b"!" + b")" * n, ": line 1 (Python: ") for n in (3, 1200, 10**5)
        ]
        cases += [(b"1+" * 200_000 + b"1", " (Python: RecursionError: ")]
        cases += [(b"not " * 200_000 + b"1", " (Python: MemoryError: Parser stack")]
        for middle, want in cases:  # the type of error Python raised is named
            self.assertTrue(note(b"x = " + middle).startswith("does not parse" + want))

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


class PassThroughTest(unittest.TestCase):
    def test_only_a_single_returned_call_carries_the_sentence(self):
        sentence = "This function calls one other function and returns whatever that returns."
        matches = [
            "def f():\n    return other(1, x)\n",
            "async def f():\n    return other()\n",
            "def f():\n    return (other())\n",
            "async def f():\n    return await other(1)\n",
        ]
        skips = [
            "def f():\n    return g(h())\n",
            "async def f():\n    return await g(h())\n",
            'def f():\n    """doc"""\n    return other()\n',
            "def f():\n    return other() + 1\n",
            "def f():\n    return\n",
            "class C:\n    def f(self):\n        return other()\n",
        ]
        for source in matches:
            info = python_file("t.py", source.encode())["children"][0]["asbuilt"]
            self.assertEqual(info.get("proven"), sentence, source)
        for source in skips:
            info = python_file("t.py", source.encode())["children"][0]["asbuilt"]
            self.assertNotIn("proven", info, source)


class CallArrowTest(unittest.TestCase):
    def test_a_direct_call_of_a_top_level_name_is_one_arrow_and_other_calls_are_not(self):
        lines = [
            "def load():",
            "    return 1",
            "",
            "class Box:",
            "    def hidden(self):",
            "        load()",
            "",
            "def main():",
            "    load()",
            "    print(1)",
            "    obj.save()",
            "    Box()",
            "    return load(other())",
            "",
            "def holder():",
            "    def inner():",
            "        load()",
            "    load()",
        ]
        box = python_file("shop.py", ("\n".join(lines) + "\n").encode())
        self.assertEqual(
            [(e["sources"][0], e["targets"][0]) for e in box["edges"]],
            [
                ("shop.py:Box:4", "shop.py:load:1"),  # the call in a method is the class's
                ("shop.py:main:8", "shop.py:load:1"),
                ("shop.py:main:8", "shop.py:Box:4"),
                ("shop.py:holder:15", "shop.py:load:1"),
            ],
        )


class CallBindingTest(unittest.TestCase):
    def test_a_parameter_or_local_of_the_same_name_is_not_an_arrow(self):
        lines = [
            "def load():",
            "    return 1",
            "def other():",
            "    return 2",
            "def main(load):",
            "    load()",
            "    other()",
            "def local():",
            "    load = 1",
            "    load()",
        ]
        box = python_file("t.py", ("\n".join(lines) + "\n").encode())
        self.assertEqual(
            [(e["sources"][0], e["targets"][0]) for e in box["edges"]],
            [("t.py:main:5", "t.py:other:3")],
        )

    def test_a_name_defined_twice_points_at_the_last_def(self):
        lines = [
            "def load():",
            "    return 1",
            "def load():",
            "    return 2",
            "def main():",
            "    load()",
        ]
        box = python_file("t.py", ("\n".join(lines) + "\n").encode())
        self.assertEqual(box["edges"][0]["targets"], ["t.py:load:3"])


def called(source):
    """The top-level names a file's arrows point at."""
    box = python_file("t.py", source.encode())
    return sorted(edge["targets"][0].split(":")[1] for edge in box.get("edges", []))


class ShadowTest(unittest.TestCase):
    def test_a_default_value_is_not_a_parameter_name(self):
        source = "def load():\n    return 1\ndef main(fn=load):\n    load()\n"
        self.assertEqual(called(source), ["load"])

    def test_global_keeps_the_arrow(self):
        source = "def load():\n    return 1\ndef use():\n    global load\n    load()\n"
        self.assertEqual(called(source), ["load"])

    def test_with_as_hides_the_name(self):
        source = "def load():\n    return 1\ndef use():\n    with open(1) as load:\n        load()\n"
        self.assertEqual(called(source), [])

    def test_a_comprehension_variable_hides_the_name(self):
        source = "def load():\n    return 1\ndef use():\n    return [load() for load in xs]\n"
        self.assertEqual(called(source), [])

    def test_a_local_import_hides_the_name(self):
        source = "def load():\n    return 1\ndef use():\n    import load\n    load()\n"
        self.assertEqual(called(source), [])

    def test_except_as_hides_the_name(self):
        source = "def load():\n    return 1\ndef use():\n    try:\n        pass\n    except E as load:\n        load()\n"
        self.assertEqual(called(source), [])

    def test_a_lambda_parameter_hides_the_name(self):
        source = "def load():\n    return 1\ndef use():\n    f = lambda load: load()\n"
        self.assertEqual(called(source), [])


class SourceTextTest(unittest.TestCase):
    def test_a_definition_keeps_its_own_lines_and_a_long_one_is_cut_not_summarized(self):
        source = "def one():\n    return 1\n\ndef two():\n    return 2\n"
        one = python_file("t.py", source.encode())["children"][0]["asbuilt"]
        self.assertEqual(one["text"], "def one():\n    return 1")
        self.assertNotIn("two", one["text"])
        self.assertNotIn("rest", one)
        lines = ["def long():"] + [f"    x = {i}" for i in range(45)]
        info = python_file("t.py", ("\n".join(lines) + "\n").encode())["children"][0]["asbuilt"]
        self.assertEqual(info["lines"], [1, 46])
        self.assertEqual(info["text"], "\n".join(lines[:40]))
        self.assertNotIn("x = 39", info["text"])
        self.assertEqual(info["rest"], "the rest is in the file, lines 41-46.")
        short = ["def edge():"] + [f"    x = {i}" for i in range(40)]
        edge = python_file("t.py", ("\n".join(short) + "\n").encode())["children"][0]["asbuilt"]
        self.assertEqual(edge["rest"], "the rest is in the file, line 41.")


def _ends(root_name, files):
    """Arrow ends in a chart of these files, after a successful build."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, root_name)
        root.mkdir()
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        out = Path(tmp, "out.json")
        if main([str(root), str(out)]) != 0:
            return None
        plan = json.loads(out.read_text())
    ends, todo = [], [plan]
    for box in todo:
        todo += box.get("children", [])
        ends += [edge["sources"] + edge["targets"] for edge in box.get("edges", [])]
    return ends


class NestedCallTest(unittest.TestCase):
    """A call in a method or an inner function is the enclosing top-level box's."""

    def test_a_method_calling_a_module_function_is_the_class_arrow(self):
        source = "def load():\n    return 1\nclass C:\n    def m(self):\n        return load()\n"
        self.assertEqual(called(source), ["load"])

    def test_an_inner_function_call_is_the_outer_box_arrow(self):
        source = "def load():\n    return 1\ndef outer():\n    def inner():\n        return load()\n    return inner\n"
        self.assertEqual(called(source), ["load"])

    def test_a_class_body_name_does_not_hide_the_name_in_a_method(self):
        source = "def load():\n    return 1\nclass C:\n    load = 1\n    def m(self):\n        return load()\n"
        self.assertEqual(called(source), ["load"])

    def test_an_inner_parameter_or_local_or_outer_local_hides_the_name(self):
        for body in (
            "    def inner(load):\n        return load()\n",
            "    def inner():\n        load = 2\n        return load()\n",
            "    load = 2\n    def inner():\n        return load()\n",
            "    class K:\n        def m(self, load):\n            return load()\n",
        ):
            source = "def load():\n    return 1\ndef outer():\n" + body
            self.assertEqual(called(source), [], body)

    def test_a_class_attribute_call_is_the_class_arrow_and_self_is_not(self):
        source = "class Box:\n    def make():\n        return 1\ndef main():\n    return Box.make()\n"
        self.assertEqual(called(source), ["Box"])
        source = "def load():\n    return 1\nclass Box:\n    def m(self):\n        return self.load()\n"
        self.assertEqual(called(source), [])
        source = "class Box:\n    pass\ndef main(Box):\n    return Box.make()\n"
        self.assertEqual(called(source), [])

    def test_a_callback_or_getattr_is_not_a_call(self):
        source = (
            "import threading\ndef load():\n    return 1\ndef main():\n"
            "    threading.Thread(target=load).start()\n    getattr(main, 'load')()\n    map(load, [1])\n"
        )
        self.assertEqual(called(source), [])


class InFunctionImportTest(unittest.TestCase):
    def test_an_import_under_if_in_a_function_is_the_call(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "def main(x):\n    if x:\n        from other import load\n    if x:\n        return load()\n",
        })
        self.assertIn(["app.py:main:1", "other.py:load:1"], ends)

    def test_two_imports_of_one_name_are_not_the_call(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "third.py": "def load():\n    return 2\n",
            "app.py": (
                "def main():\n    try:\n        from other import load\n"
                "    except ImportError:\n        from third import load\n    return load()\n"
            ),
        })
        self.assertEqual(ends, [])

    def test_a_method_reaches_an_imported_module_function(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "import other\nclass C:\n    def m(self):\n        return other.load()\n",
        })
        self.assertEqual(ends, [["app.py:C:2", "other.py:load:1"]])

    def test_an_inner_import_wins_over_the_file_import(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "third.py": "def load():\n    return 2\n",
            "app.py": (
                "from other import load\ndef main():\n    def inner():\n"
                "        from third import load\n        return load()\n    return inner\n"
            ),
        })
        self.assertEqual(ends, [["app.py:main:2", "third.py:load:1"]])

    def test_an_inner_parameter_hides_the_file_import(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "from other import load\ndef main():\n    def inner(load):\n        return load()\n    return inner\n",
        })
        self.assertEqual(ends, [])

    def test_an_import_under_if_in_a_class_body_is_not_the_call(self):
        # A class body falls back to the module's name when the import did not run.
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "class C:\n    if X:\n        from other import load\n    y = load()\n",
        })
        self.assertEqual(ends, [])


class BuiltCommandTest(unittest.TestCase):
    def test_a_file_is_written_as_a_plan_or_a_refusal_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp, "t.py"), Path(tmp, "out.json")
            src.write_text("def one():\n    return 1\n")
            self.assertEqual(main([str(src), str(out)]), 0)
            plan = json.loads(out.read_text())
            self.assertEqual(plan["id"], "asbuilt:built")
            self.assertEqual(plan["children"][0]["children"][0]["labels"][0]["text"], "one()")
            out.unlink()
            kept = src.read_text()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(Path(tmp, "missing.py")), str(out)]), 2)
            self.assertEqual(err.getvalue().count("\n"), 1)
            self.assertTrue(err.getvalue().startswith("asbuilt.built: "))
            self.assertFalse(out.exists())
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(src), str(src)]), 2)
            self.assertEqual(err.getvalue().count("\n"), 1)
            self.assertEqual(src.read_text(), kept)

    def test_a_from_import_draws_an_arrow_to_the_other_file(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "from other import load\ndef main():\n    return load()\n",
        })
        self.assertIn(["app.py:main:2", "other.py:load:1"], ends)

    def test_an_import_and_an_attribute_call_draw_an_arrow(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "import other\ndef main():\n    return other.load()\n",
        })
        self.assertIn(["app.py:main:2", "other.py:load:1"], ends)

    def test_a_relative_import_draws_an_arrow(self):
        ends = _ends("shop", {
            "__init__.py": "",
            "other.py": "def load():\n    return 1\n",
            "app.py": "from .other import load\ndef main():\n    return load()\n",
        })
        self.assertIn(["app.py:main:2", "other.py:load:1"], ends)

    def test_the_package_name_resolves_under_src(self):
        ends = _ends("repo", {
            "src/shop/__init__.py": "",
            "src/shop/other.py": "def load():\n    return 1\n",
            "src/shop/app.py": "from shop.other import load\ndef main():\n    return load()\n",
        })
        self.assertIn(["src/shop/app.py:main:2", "src/shop/other.py:load:1"], ends)

    def test_a_relative_import_in_init_stays_in_the_package(self):
        ends = _ends("shop", {
            "notes.py": "def f():\n    return 1\n",
            "pkg/__init__.py": "from .notes import f\ndef run():\n    return f()\n",
            "pkg/notes.py": "def f():\n    return 2\n",
        })
        self.assertEqual(ends, [["pkg/__init__.py:run:2", "pkg/notes.py:f:1"]])

    def test_a_second_import_under_try_draws_no_arrow(self):
        ends = _ends("shop", {
            "__init__.py": "",
            "_pure.py": "def escape():\n    return 1\n",
            "_speedups.py": "def escape():\n    return 2\n",
            "render.py": (
                "from ._pure import escape\n"
                "try:\n"
                "    from ._speedups import escape\n"
                "except ImportError:\n"
                "    pass\n"
                "def render():\n"
                "    return escape()\n"
            ),
        })
        self.assertEqual(ends, [])

    def test_a_module_level_assignment_draws_no_arrow(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "from other import load\nload = print\ndef main():\n    return load()\n",
        })
        self.assertEqual(ends, [])

    def test_a_target_rebound_in_its_file_is_not_an_arrow(self):
        ends = _ends("shop", {
            "__init__.py": "",
            "events.py": "def get():\n    return 1\ntry:\n    from _c import get\nexcept ImportError:\n    pass\n",
            "tasks.py": "from . import events\ndef run():\n    return events.get()\n",
        })
        self.assertEqual(ends, [])

    def test_a_from_import_of_a_rebound_name_draws_nothing(self):
        ends = _ends("shop", {
            "__init__.py": "",
            "events.py": "def get():\n    return 1\nget = print\n",
            "tasks.py": "from .events import get\ndef run():\n    return get()\n",
        })
        self.assertEqual(ends, [])

    def test_a_reexport_in_init_is_not_the_submodule(self):
        ends = _ends("shop", {
            "pkg/app.py": "app = object()\ndef run():\n    return 1\n",
            "pkg/__init__.py": "from .app import app\n",
            "main.py": "from pkg import app\ndef main():\n    return app.run()\n",
        })
        self.assertEqual(ends, [])

    def test_from_pkg_import_mod_then_mod_f_draws_an_arrow(self):
        ends = _ends("shop", {
            "__init__.py": "",
            "other.py": "def load():\n    return 1\n",
            "app.py": "from shop import other\ndef main():\n    return other.load()\n",
        })
        self.assertIn(["app.py:main:2", "other.py:load:1"], ends)

    def test_from_dot_import_mod_then_mod_f_draws_an_arrow(self):
        ends = _ends("shop", {
            "__init__.py": "",
            "other.py": "def load():\n    return 1\n",
            "app.py": "from . import other\ndef main():\n    return other.load()\n",
        })
        self.assertIn(["app.py:main:2", "other.py:load:1"], ends)

    def test_importing_the_same_module_again_keeps_the_arrow(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "import other\nimport other.sub\ndef main():\n    return other.load()\n",
        })
        self.assertIn(["app.py:main:3", "other.py:load:1"], ends)

    def test_a_relative_import_above_the_package_draws_nothing(self):
        ends = _ends("repo", {
            "other.py": "def load():\n    return 1\n",
            "pkg/__init__.py": "",
            "pkg/app.py": "from ..other import load\ndef main():\n    return load()\n",
        })
        self.assertEqual(ends, [])

    def test_an_import_under_type_checking_still_draws(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "if TYPE_CHECKING:\n    from other import load\ndef main():\n    return load()\n",
        })
        self.assertIn(["app.py:main:3", "other.py:load:1"], ends)

    def test_an_imported_name_rebound_locally_has_no_arrow(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "from other import load\ndef main():\n    load = 1\n    return load()\n",
        })
        self.assertEqual(ends, [])

    def test_an_import_of_an_unknown_name_draws_nothing(self):
        ends = _ends("shop", {
            "other.py": "def load():\n    return 1\n",
            "app.py": "from other import missing\ndef main():\n    return missing()\n",
        })
        self.assertEqual(ends, [])

    def test_a_folder_is_one_box_per_python_file_sitting_in_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "shop")
            root.mkdir()
            (root / "load.py").write_text("def read():\n    return 1\ndef load():\n    return read()\n")
            (root / "main.py").write_text("def main():\n    return load()\n")
            (root / "notes.txt").write_text("not code\n")
            (root / "sub").mkdir()
            (root / "sub" / "other.py").write_text("def other():\n    return 1\n")
            out = Path(tmp, "out.json")
            self.assertEqual(main([str(root), str(out)]), 0)
            plan = json.loads(out.read_text())
            folder = plan["children"][0]
            self.assertEqual(folder["id"], "shop")
            self.assertEqual([box["id"] for box in folder["children"]], ["load.py", "main.py", "sub/"])
            load = folder["children"][0]
            self.assertEqual(load["children"][1]["id"], "load.py:load:3")
            self.assertEqual(
                load["edges"][0]["sources"] + load["edges"][0]["targets"],
                ["load.py:load:3", "load.py:read:1"],
            )
            nested = folder["children"][2]["children"][0]
            self.assertEqual(nested["id"], "sub/other.py")
            self.assertEqual(nested["labels"][0]["text"], "other.py")
            self.assertEqual(nested["children"][0]["id"], "sub/other.py:other:1")

    def test_tests_pycache_and_hidden_directories_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "shop")
            root.mkdir()
            (root / "test_load.py").write_text("def test_load():\n    return 1\n")
            skipped = ("tests", "__pycache__", ".venv", "venv", "env", "node_modules", "build", "dist", "site-packages")
            for name in skipped:
                folder = root / name
                folder.mkdir()
                (folder / "secret.py").write_text("def secret():\n    return 1\n")
            out = Path(tmp, "out.json")
            self.assertEqual(main([str(root), str(out)]), 0)
            folder = json.loads(out.read_text())["children"][0]
            self.assertEqual([box["id"] for box in folder["children"]], ["test_load.py"])

    def test_a_python_file_in_a_subfolder_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "shop", "sub", "other.py")
            src.parent.mkdir(parents=True)
            src.write_text("def other():\n    return 1\n")
            kept = src.read_text()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(src.parents[1]), str(src)]), 2)
            self.assertEqual(err.getvalue().count("\n"), 1)
            self.assertEqual(src.read_text(), kept)

    def test_a_differently_cased_python_name_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            shop = Path(tmp, "shop")
            shop.mkdir()
            src = shop / "main.py"
            src.write_text("def main():\n    return 1\n")
            kept = src.read_text()
            with contextlib.chdir(shop), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([".", "MAIN.PY"]), 2)
            self.assertEqual(src.read_text(), kept)

    def test_a_new_python_output_says_the_chart_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp, "t.py"), Path(tmp, "out.py")
            src.write_text("def one():\n    return 1\n")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(src), str(out)]), 2)
            self.assertIn("JSON", err.getvalue())
            self.assertFalse(out.exists())
            self.assertIn("def one", src.read_text())

    def test_a_symlink_to_a_charted_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            shop = Path(tmp, "shop")
            shop.mkdir()
            src = shop / "main.py"
            src.write_text("def main():\n    return 1\n")
            (shop / "link.json").symlink_to(src)
            kept = src.read_text()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(shop), str(shop / "link.json")]), 2)
            self.assertIn("source", err.getvalue())
            self.assertEqual(src.read_text(), kept)

    def test_a_subfolder_with_the_parent_name_keeps_unique_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp, "mysite", "mysite")
            nested.mkdir(parents=True)
            (nested / "settings.py").write_text("def debug():\n    return 1\n")
            out = Path(tmp, "out.json")
            self.assertEqual(main([str(nested.parent), str(out)]), 0)
            plan = json.loads(out.read_text())
            ids, todo = [], [plan]
            for box in todo:
                todo += box.get("children", [])
                ids.append(box["id"])
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(plan["children"][0]["id"], "mysite")
            sub = plan["children"][0]["children"][0]
            self.assertEqual(sub["id"], "mysite/")
            inner = {"id": "mysite/settings.py:debug", "labels": [{"text": "debug()"}]}
            file = {"id": "mysite/settings.py", "labels": [{"text": "settings.py"}], "children": [inner]}
            sub_plan = {"id": "mysite/", "labels": [{"text": "mysite"}], "children": [file]}
            planned = {"id": "asbuilt:built", "children": [{"id": "mysite", "labels": [{"text": "mysite"}], "children": [sub_plan]}]}
            marked = compare(planned, plan)
            marks, todo = [], [marked]
            for box in todo:
                todo += box.get("children", [])
                marks += [kid["asbuilt"]["mark"] for kid in box.get("children", [])]
            self.assertEqual(marks, ["planned", "planned", "planned", "planned"])

    def test_dot_as_the_output_names_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            shop = Path(tmp, "shop")
            shop.mkdir()
            src = shop / "load.py"
            src.write_text("def load():\n    return 1\n")
            kept = src.read_text()
            with contextlib.chdir(shop), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([".", "."]), 2)
            self.assertIn("shop:", err.getvalue())
            self.assertEqual(src.read_text(), kept)

    def test_a_folder_does_not_overwrite_one_of_its_python_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "shop", "main.py")
            src.parent.mkdir()
            src.write_text("def main():\n    return 1\n")
            kept = src.read_text()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(src.parent), str(src)]), 2)
            self.assertEqual(err.getvalue().count("\n"), 1)
            self.assertEqual(src.read_text(), kept)

    def test_charting_dot_names_the_folder_and_compares_as_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            shop = Path(tmp, "shop")
            shop.mkdir()
            (shop / "load.py").write_text("def load():\n    return 1\n")
            out = Path(tmp, "built.json")
            with contextlib.chdir(shop):
                self.assertEqual(main([".", str(out)]), 0)
            built = json.loads(out.read_text())
            self.assertEqual(built["children"][0]["id"], "shop")
            load = {"id": "load.py:load", "labels": [{"text": "load()"}]}
            file = {"id": "load.py", "labels": [{"text": "load.py"}], "children": [load]}
            planned = {"id": "asbuilt:built", "children": [{"id": "shop", "labels": [{"text": "shop"}], "children": [file]}]}
            marked = compare(planned, built)
            marks, todo = [], [marked]
            for box in todo:
                todo += box.get("children", [])
                marks += [kid["asbuilt"]["mark"] for kid in box.get("children", [])]
            self.assertEqual(marks, ["planned", "planned", "planned"])

    def test_the_skip_line_leaves_out_pycache_and_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "shop")
            root.mkdir()
            (root / "load.py").write_text("def load():\n    return 1\n")
            for name in ("build", "__pycache__", ".venv"):
                folder = root / name
                folder.mkdir()
                (folder / "mod.py").write_text("def mod():\n    return 1\n")
            out = Path(tmp, "out.json")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(root), str(out)]), 0)
            self.assertEqual(err.getvalue(), "asbuilt.built: skipped build\n")

    def test_only_skipped_folders_are_named_in_the_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "shop")
            (root / "build").mkdir(parents=True)
            (root / "build" / "mod.py").write_text("def mod():\n    return 1\n")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "old.py").write_text("def old():\n    return 1\n")
            out = Path(tmp, "out.json")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(root), str(out)]), 2)
            self.assertIn("no Python files", err.getvalue())
            self.assertIn("build", err.getvalue())
            self.assertIn("__pycache__", err.getvalue())
            self.assertFalse(out.exists())

    def test_a_skipped_build_directory_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "shop")
            (root / "build").mkdir(parents=True)
            (root / "load.py").write_text("def load():\n    return 1\n")
            (root / "build" / "mod.py").write_text("def mod():\n    return 1\n")
            out = Path(tmp, "out.json")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(root), str(out)]), 0)
            self.assertEqual(err.getvalue(), "asbuilt.built: skipped build\n")
            folder = json.loads(out.read_text())["children"][0]
            self.assertEqual([box["id"] for box in folder["children"]], ["load.py"])

    def test_a_folder_with_no_python_file_in_it_prints_a_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "shop")
            root.mkdir()
            (root / "notes.txt").write_text("not code\n")
            out = Path(tmp, "out.json")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(root), str(out)]), 2)
            self.assertEqual(err.getvalue().count("\n"), 1)
            self.assertIn("no Python files", err.getvalue())
            self.assertFalse(out.exists())

    def test_a_single_file_plan_is_still_just_that_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp, "t.py"), Path(tmp, "out.json")
            src.write_text("def one():\n    return 1\n")
            self.assertEqual(main([str(src), str(out)]), 0)
            one = {
                "id": "t.py:one:1",
                "labels": [{"text": "one()"}],
                "asbuilt": {"lines": [1, 2], "text": "def one():\n    return 1"},
            }
            want = {"id": "asbuilt:built", "children": [{"id": "t.py", "labels": [{"text": "t.py"}], "children": [one]}]}
            self.assertEqual(json.loads(out.read_text()), want)

    def test_sys_path_parents_handles_bases_huge_and_negative_indices(self):
        from asbuilt.built import sys_paths, tree

        source = (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parents[0x1] / 'scripts'))\n"
            "sys.path.insert(0, str(Path(__file__).parents[0o0]))\n"
            "sys.path.insert(0, str(Path(__file__).parents[999999999999]))\n"
            "sys.path.insert(0, str(Path(__file__).parents[-1]))\n"
        )
        found = sys_paths("scripts/run.py", tree(source.encode()), Path("/proj"))
        self.assertEqual(
            [(folder, why) for _, _, folder, why in found],
            [
                ("scripts", None),
                ("scripts", None),
                (None, "the folder is outside the charted folder"),
                (None, "the folder is known only when it runs"),
            ],
        )
