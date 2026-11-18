"""JS/TS source stays data; definitions and arrows require unambiguous syntax."""

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from asbuilt.compare import check
from asbuilt.javascript import chart, main
from asbuilt.state import PlanError


class JavaScript(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def read(self, path=None):
        plan = chart(path or self.root)
        check(plan, "chart.json")
        return plan

    @staticmethod
    def boxes(plan):
        todo, found = [plan], {}
        while todo:
            node = todo.pop()
            todo.extend(node.get("children", []))
            found[node["id"]] = node
        return found

    @staticmethod
    def calls(plan):
        return {
            (e["sources"][0], e["targets"][0])
            for e in plan.get("edges", [])
            if not e.get("labels")
        }

    def test_four_grammars_keep_definitions_source_and_relative_import_calls(self):
        for suffix in ("js", "jsx", "ts", "tsx"):
            with self.subTest(suffix=suffix):
                folder = self.root / suffix
                self.put(
                    f"{suffix}/lib.{suffix}",
                    "export function read() { return 1; }\nexport default class Model {}\n",
                )
                value = "<div/>" if suffix in ("jsx", "tsx") else "1"
                self.put(
                    f"{suffix}/app.{suffix}",
                    f"import Model, {{read as load}} from './lib';\nconst Component = () => {value};\nexport function run() {{ load(); Component(); return new Model(); }}\n",
                )
                plan = self.read(folder)
                boxes = self.boxes(plan)
                self.assertEqual(
                    self.calls(plan),
                    {
                        (f"app.{suffix}:run:3", target)
                        for target in (
                            f"lib.{suffix}:read:1",
                            f"lib.{suffix}:Model:2",
                            f"app.{suffix}:Component:2",
                        )
                    },
                )
                self.assertEqual(
                    boxes[f"lib.{suffix}:Model:2"]["labels"], [{"text": "Model"}]
                )
                info = boxes[f"app.{suffix}:run:3"]["asbuilt"]
                self.assertEqual(
                    info,
                    {
                        "lines": [3, 3],
                        "text": "export function run() { load(); Component(); return new Model(); }",
                    },
                )
                imports = [e for e in plan["edges"] if e.get("labels")]
                self.assertEqual(
                    [(e["sources"], e["targets"], e["labels"]) for e in imports],
                    [([f"app.{suffix}"], [f"lib.{suffix}"], [{"text": "imports"}])],
                )

    def test_renaming_and_shadowing_property_across_grammars(self):
        # The graph survives renaming, but a parameter/local of that name removes its call.
        for suffix in ("js", "jsx", "ts", "tsx"):
            for ident in ("read", "load_2", "$work", "café"):
                for params, body, present in [
                    ("", f"{ident}();", True),
                    (f"value = {ident}", f"{ident}();", True),
                    (ident, f"{ident}();", False),
                    (f"{{key: {ident}}}", f"{ident}();", False),
                    (f"{{{ident} = replacement}}", f"{ident}();", False),
                    ("", f"const {ident} = other; {ident}();", False),
                    ("", f"const inner = () => {ident}();", False),
                    ("", f"function inner() {{ {ident}(); }}", False),
                ]:
                    with self.subTest(
                        suffix=suffix, ident=ident, params=params, body=body
                    ):
                        path = self.put(
                            f"entry.{suffix}",
                            f"function {ident}() {{}}\nfunction run({params}) {{ {body} }}\n",
                        )
                        expected = (
                            {(f"entry.{suffix}:run:2", f"entry.{suffix}:{ident}:1")}
                            if present
                            else set()
                        )
                        self.assertEqual(self.calls(self.read(path)), expected)

    def test_writes_dynamic_scope_and_duplicate_exports_never_guess_targets(self):
        for write in (
            "read = other;",
            "(read) = other;",
            "[read] = values;",
            "({read} = obj);",
            "read++;",
            "for (read of values) {}",
            "eval('read = other');",
        ):
            with self.subTest(write=write):
                path = self.put(
                    "write.js",
                    f"function read() {{}}\nfunction run() {{read();}}\nfunction change() {{{write}}}\n",
                )
                self.assertFalse(self.calls(self.read(path)))
        self.put(
            "lib.ts",
            "function one() {}\nfunction two() {}\nexport {one as read};\nexport {two as read};\n",
        )
        self.put(
            "entry.ts", "import {read} from './lib';\nfunction run() { read(); }\n"
        )
        self.assertFalse(self.calls(self.read()))

    def test_exports_must_be_runtime_unique_and_local(self):
        self.put(
            "lib.ts",
            "function hidden() {}\nfunction read() {}\nexport {read as load};\nexport default read;\n",
        )
        self.put(
            "entry.ts",
            "import pick, {load, hidden} from './lib';\nimport type {Ghost} from './missing';\nfunction run() { pick(); load(); hidden(); Ghost(); }\n",
        )
        self.assertEqual(self.calls(self.read()), {("entry.ts:run:3", "lib.ts:read:2")})
        self.put("lib.js", "export function load() {}\n")
        self.assertFalse(
            self.calls(self.read()), "two possible extensions are ambiguous"
        )

    def test_anonymous_default_function_and_class_methods(self):
        self.put(
            "lib.js",
            "export default () => 1;\nexport class Model { run() { helper(); } }\nfunction helper() {}\n",
        )
        self.put(
            "entry.js",
            "import work from './lib.js';\nfunction run() { return work(); }\n",
        )
        self.assertEqual(
            self.calls(self.read()), {("entry.js:run:2", "lib.js:default:1")}
        )

    def test_source_only_even_when_a_test_folder_or_file_is_named_directly(self):
        own = self.put("src/app.ts", "export function run() {}")
        for name in (
            "tests/x.js",
            "__tests__/x.ts",
            "test/x.jsx",
            "src/app.test.tsx",
            "src/app.spec.js",
            "src/test_helper.js",
            "src/helper_test.ts",
            "node_modules/pkg/index.js",
            "vendor/lib.js",
            "dist/app.js",
            ".cache/app.js",
            "src/types.d.ts",
        ):
            self.put(name, "function mustNotAppear() {}")
        (self.root / "link.js").symlink_to(own)
        boxes = self.boxes(self.read())
        self.assertEqual(
            set(boxes),
            {"asbuilt:built", self.root.name, "src/app.ts", "src/app.ts:run:1"},
        )
        for name in ("tests", "__tests__/x.ts", "src/app.test.tsx"):
            with self.subTest(name=name), self.assertRaises(PlanError):
                self.read(self.root / name)

    def test_command_is_atomic_repeatable_and_cannot_overwrite_source(self):
        source = self.put("own.ts", "export function run() { return 7; }\n")
        out = self.root / "chart.json"
        self.assertEqual(main([str(self.root), str(out)]), 0)
        good = out.read_bytes()
        self.assertEqual(main([str(self.root), str(out)]), 0)
        self.assertEqual(out.read_bytes(), good)
        for content in (b"function broken( {", b"\xff"):
            source.write_bytes(content)
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(main([str(self.root), str(out)]), 2)
            self.assertIn("own.ts", error.getvalue())
            self.assertEqual(out.read_bytes(), good)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(source), str(source)]), 2)
        self.assertEqual(source.read_bytes(), b"\xff")

    def test_source_preview_uses_the_existing_cap_and_real_lines(self):
        source = "function long() {\n" + "  doSomething();\n" * 50 + "}\n"
        path = self.put("long.js", source)
        info = self.boxes(self.read(path))["long.js:long:1"]["asbuilt"]
        self.assertEqual(info["lines"], [1, 52])
        self.assertEqual(len(info["text"].splitlines()), 40)
        self.assertIn("41-52", info["rest"])
