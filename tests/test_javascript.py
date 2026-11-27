"""JS/TS source stays data; definitions and arrows require unambiguous syntax."""

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from asbuilt.compare import check
from asbuilt.javascript import _module, chart, main
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
            for box in JavaScript.boxes(plan).values()
            for e in box.get("edges", [])
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
                # Components reads definition calls from each file, not the root.
                self.assertEqual(len(boxes[f"app.{suffix}"]["edges"]), 3)
                self.assertTrue(all(e.get("labels") for e in plan["edges"]))
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

    def test_type_only_exports_never_establish_runtime_targets(self):
        for suffix in ("ts", "tsx"):
            for exported, present in (
                ("export type {Model};", False),
                ("export type {Model as Public};", False),
                ("export {type Model};", False),
                ("export {type Model as Public};", False),
                ("export {Model};", True),
                ("export {Model as Public};", True),
                ("export default Model;", True),
            ):
                with self.subTest(suffix=suffix, exported=exported):
                    folder = self.root / suffix
                    self.put(f"{suffix}/lib.{suffix}", f"class Model {{}}\n{exported}")
                    imported = (
                        "Model"
                        if "default" in exported
                        else "{Public as Model}"
                        if "Public" in exported
                        else "{Model}"
                    )
                    self.put(
                        f"{suffix}/app.{suffix}",
                        f"import {imported} from './lib';\nfunction run() {{return new Model();}}",
                    )
                    plan = self.read(folder)
                    self.assertIn(f"lib.{suffix}:Model:1", self.boxes(plan))
                    expected = (
                        {(f"app.{suffix}:run:2", f"lib.{suffix}:Model:1")}
                        if present
                        else set()
                    )
                    self.assertEqual(self.calls(plan), expected)

    def test_object_methods_are_not_calls_by_the_enclosing_function(self):
        for suffix in ("js", "jsx", "ts", "tsx"):
            for method, outer, present in (
                ("method(){read();}", "", False),
                ("async method(){read();}", "", False),
                ("*method(){read();}", "", False),
                ("get value(){read();}", "", False),
                ("set value(x){read();}", "", False),
                ("method(read){read();}", "", False),
                ("method(x=read()){return x;}", "", False),
                ("method(){const read=other;}", "read();", True),
                ("read(){}", "read();", True),
                ("[read()](){}", "", True),
            ):
                with self.subTest(suffix=suffix, method=method, outer=outer):
                    path = self.put(
                        f"entry.{suffix}",
                        f"function read(){{}}\nfunction run(){{{outer} return {{{method}}};}}",
                    )
                    expected = (
                        {(f"entry.{suffix}:run:2", f"entry.{suffix}:read:1")}
                        if present
                        else set()
                    )
                    self.assertEqual(self.calls(self.read(path)), expected)

    def test_jsx_literal_ampersands_preserve_source_and_reject_broken_expressions(self):
        for suffix in ("js", "jsx", "tsx"):
            path = self.root / f"app.{suffix}"
            out = self.root / "chart.json"
            for literal in ("B&O", "B & O", "B&amp;O", "B&", "é & résumé"):
                source = (
                    "function read(){}\nfunction App(){\n"
                    f' const obj = <div title="{literal}">{literal}</div>;\n'
                    " if (left && right) read(); return obj;\n}"
                )
                with self.subTest(suffix=suffix, literal=literal):
                    path.write_text(source)
                    plan = self.read(path)
                    self.assertEqual(
                        self.calls(plan),
                        {(f"app.{suffix}:App:2", f"app.{suffix}:read:1")},
                    )
                    self.assertEqual(
                        self.boxes(plan)[f"app.{suffix}:App:2"]["asbuilt"]["text"],
                        source.split("\n", 1)[1],
                    )
                    self.assertEqual(main([str(path), str(out)]), 0)
            good = out.read_bytes()
            for jsx in (
                "<div>B&O{foo &}</div>",
                '<div title="B&O" broken={foo &}/>',
                '<div title="B&O/>',
                "<div>B&O</span>",
                "<div>B&O",
            ):
                with self.subTest(suffix=suffix, broken=jsx):
                    path.write_text(f"function App(){{return {jsx};}}")
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main([str(path), str(out)]), 2)
                    self.assertEqual(out.read_bytes(), good)

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

    def test_source_only_below_chart_root_and_for_direct_test_filenames(self):
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
        (self.root / "alias").symlink_to(own.parent, target_is_directory=True)
        boxes = self.boxes(self.read())
        self.assertEqual(
            set(boxes),
            {"asbuilt:built", self.root.name, "src/app.ts", "src/app.ts:run:1"},
        )
        for name in ("src/app.test.tsx", "src/app.spec.js", "link.js", "alias"):
            with self.subTest(name=name), self.assertRaises(PlanError):
                self.read(self.root / name)

    def test_direct_test_and_dependency_inputs_are_refused(self):
        test_file = self.put("proj/tests/t.js", "function testHelper(){}")
        dependency = self.put("proj/node_modules/x/c.js", "function dependency(){}")
        self.put("proj/node_modules/x/package.json", "{}")
        for marker in (None, "package.json", ".git"):
            if marker:
                self.put(f"proj/{marker}", "{}")
            for path in (test_file, dependency, test_file.parent):
                with (
                    self.subTest(path=path, marker=marker),
                    self.assertRaises(PlanError),
                ):
                    self.read(path)

    def test_excluded_ancestor_names_do_not_reject_a_project_or_its_file(self):
        for ancestor in (
            "build",
            "test",
            "dist",
            "env",
            "venv",
            "vendor",
            "coverage",
            "tests",
            "node_modules",
        ):
            own = self.put(f"{ancestor}/repo/src/app.ts", "function own(){}")
            project = own.parent.parent
            self.put(f"{ancestor}/repo/package.json", "{}")
            if ancestor == "node_modules":
                self.put(f"{ancestor}/repo/.git", "gitdir: own-checkout")
            self.put(f"{ancestor}/repo/{ancestor}/hidden.ts", "function hidden(){}")
            for path in (project, own):
                with self.subTest(ancestor=ancestor, path=path):
                    plan = self.read(path)
                    self.assertEqual(
                        [
                            box["labels"][0]["text"]
                            for box in self.boxes(plan).values()
                            if "asbuilt" in box
                        ],
                        ["own()"],
                    )
            with contextlib.chdir(project):
                for path in (".", "src/app.ts"):
                    with self.subTest(ancestor=ancestor, relative=path):
                        self.assertEqual(main([path, str(self.root / "out.json")]), 0)

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

    def test_duplicate_declarations_on_same_line_get_unique_ids_and_calls(self):
        path = self.put(
            "dup.js",
            "function helper() {}\n"
            "function f() { helper(); } function f() { helper(); }\n"
            "var g = () => helper(), g = () => helper();\n",
        )
        plan = self.read(path)
        children = self.boxes(plan)["dup.js"]["children"]
        ids = [c["id"] for c in children]
        self.assertEqual(len(ids), len(set(ids)))
        f_ids = [c["id"] for c in children if c["labels"] == [{"text": "f()"}]]
        g_ids = [c["id"] for c in children if c["labels"] == [{"text": "g()"}]]
        self.assertEqual(len(f_ids), 2)
        self.assertEqual(len(g_ids), 2)
        self.assertEqual(
            self.calls(plan),
            {(src, "dup.js:helper:1") for src in (*f_ids, *g_ids)},
        )

    def test_relative_import_of_dotted_filename_resolves(self):
        for suffix in ("js", "ts"):
            with self.subTest(suffix=suffix):
                folder = self.root / f"dotted_{suffix}"
                self.put(
                    f"dotted_{suffix}/foo.bar.{suffix}",
                    "export function hi() { return 1; }\n",
                )
                self.put(
                    f"dotted_{suffix}/app.{suffix}",
                    "import { hi } from './foo.bar';\nexport function run() { return hi(); }\n",
                )
                plan = self.read(folder)
                self.assertEqual(
                    self.calls(plan),
                    {(f"app.{suffix}:run:2", f"foo.bar.{suffix}:hi:1")},
                )
                imports = [e for e in plan["edges"] if e.get("labels")]
                self.assertEqual(
                    [(e["sources"], e["targets"]) for e in imports],
                    [([f"app.{suffix}"], [f"foo.bar.{suffix}"])],
                )

    def test_folder_skips_and_lists_unparseable_files_while_charting_valid_ones(self):
        self.put("good.ts", "export function helper() { return 1; }\n")
        self.put(
            "app.ts",
            "import { helper } from './good';\n"
            "import { ghost } from './broken';\n"
            "export function run() { helper(); ghost(); }\n",
        )
        self.put("broken.ts", "export function bad( {\n")
        (self.root / "bad_utf8.js").write_bytes(b"\xff\xfe")

        skipped = []
        plan = chart(self.root, skipped=skipped)
        check(plan, "chart.json")
        self.assertEqual(skipped, ["bad_utf8.js", "broken.ts"])
        boxes = self.boxes(plan)
        self.assertIn("good.ts", boxes)
        self.assertIn("app.ts", boxes)
        self.assertNotIn("broken.ts", boxes)
        self.assertNotIn("bad_utf8.js", boxes)
        self.assertEqual(self.calls(plan), {("app.ts:run:3", "good.ts:helper:1")})

        out = self.root / "chart.json"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(self.root), str(out)]), 0)
        self.assertEqual(
            err.getvalue(), "asbuilt.javascript: skipped bad_utf8.js, broken.ts\n"
        )
        self.assertTrue(out.is_file())

    def test_single_unparseable_file_and_all_unparseable_folder_raise_plan_error(self):
        broken_ts = self.put("broken.ts", "export function bad( {\n")
        bad_utf8 = self.root / "bad.js"
        bad_utf8.write_bytes(b"\xff")
        for path in (broken_ts, bad_utf8):
            with self.subTest(path=path.name), self.assertRaises(PlanError):
                chart(path)
        with self.assertRaises(PlanError) as ctx:
            chart(self.root)
        self.assertIn("bad.js", str(ctx.exception))
        self.assertIn("broken.ts", str(ctx.exception))

    def test_ts_esm_js_specifiers_resolve_to_ts_or_tsx_when_unambiguous(self):
        for target_ext in ("ts", "tsx"):
            with self.subTest(target_ext=target_ext):
                folder = self.root / f"esm_{target_ext}"
                body = "<div/>" if target_ext == "tsx" else "1"
                self.put(
                    f"esm_{target_ext}/sub/bar.{target_ext}",
                    f"export function foo() {{ return {body}; }}\n",
                )
                self.put(
                    f"esm_{target_ext}/app.ts",
                    "import { foo } from './sub/bar.js';\n"
                    "export function run() { return foo(); }\n",
                )
                plan = self.read(folder)
                self.assertEqual(
                    self.calls(plan),
                    {("app.ts:run:2", f"sub/bar.{target_ext}:foo:1")},
                )
                imports = [e for e in plan["edges"] if e.get("labels")]
                self.assertEqual(
                    [(e["sources"], e["targets"]) for e in imports],
                    [(["app.ts"], [f"sub/bar.{target_ext}"])],
                )

    def test_ts_esm_prefers_existing_js_and_rejects_ambiguous_ts_tsx(self):
        # When bar.js exists alongside bar.ts, './bar.js' must resolve to bar.js.
        prefer = self.root / "prefer"
        self.put("prefer/bar.js", "export function foo() { return 1; }\n")
        self.put("prefer/bar.ts", "export function foo() { return 2; }\n")
        self.put(
            "prefer/app.ts",
            "import { foo } from './bar.js';\nexport function run() { return foo(); }\n",
        )
        self.assertEqual(
            self.calls(self.read(prefer)),
            {("app.ts:run:2", "bar.js:foo:1")},
        )

        # When bar.js does not exist and both bar.ts and bar.tsx exist, it is ambiguous.
        ambig = self.root / "ambig"
        self.put("ambig/bar.ts", "export function foo() { return 1; }\n")
        self.put("ambig/bar.tsx", "export function foo() { return <div/>; }\n")
        self.put(
            "ambig/app.ts",
            "import { foo } from './bar.js';\nexport function run() { return foo(); }\n",
        )
        self.assertFalse(self.calls(self.read(ambig)))

    def test_ts_esm_jsx_mjs_cjs_fallback_resolution(self):
        folder = self.root / "jsx_fallback"
        self.put("jsx_fallback/card.tsx", "export function Card() { return <div/>; }\n")
        self.put(
            "jsx_fallback/app.tsx",
            "import { Card } from './card.jsx';\nexport function App() { return Card(); }\n",
        )
        self.assertEqual(
            self.calls(self.read(folder)),
            {("app.tsx:App:2", "card.tsx:Card:1")},
        )
        # Unit checks for .mjs -> .mts and .cjs -> .cts in _module.
        self.assertEqual(
            _module("src/app.ts", "./util.mjs", {"src/util.mts": None}),
            "src/util.mts",
        )
        self.assertEqual(
            _module(
                "src/app.ts",
                "./util.mjs",
                {"src/util.mjs": None, "src/util.mts": None},
            ),
            "src/util.mjs",
        )
        self.assertEqual(
            _module("src/app.ts", "../lib/mod.cjs", {"lib/mod.cts": None}),
            "lib/mod.cts",
        )
        self.assertIsNone(
            _module("src/app.ts", "./only.ts", {"src/only.js": None}),
        )
