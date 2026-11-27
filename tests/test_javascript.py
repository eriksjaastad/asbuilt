"""JS/TS source stays data; definitions and arrows require unambiguous syntax."""

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from asbuilt.built import PASSES
from asbuilt.compare import check
from asbuilt.javascript import _module, chart, find_components, main
from asbuilt.marks import _ORDER as MARK_ORDER
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
            self.calls(self.read()), {("entry.js:run:2", "lib.js:lib:1")}
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

    def test_commonjs_require_default_destructured_bare_and_ignored_forms(self):
        self.put(
            "single.js",
            "function work() { return 1; }\nmodule.exports = work;\n",
        )
        self.put(
            "named.js",
            "function alpha() {}\nfunction beta() {}\n"
            "module.exports = { alpha, raw: beta };\n",
        )
        self.put(
            "props.js",
            "function delta() {}\nexports.delta = delta;\n"
            "module.exports.gamma = () => 2;\n",
        )
        self.put(
            "side.js",
            "function init() {}\n",
        )
        self.put(
            "app.js",
            "const fs = require('fs');\n"
            "const work = require('./single');\n"
            "const { alpha, raw: renamed } = require('./named');\n"
            "const { delta, gamma } = require('./props');\n"
            "require('./side');\n"
            "const dyn = require(fs);\n"
            "const expr = require('./sin' + 'gle');\n"
            "const tpl = require(`./single`);\n"
            "function run() { work(); alpha(); renamed(); delta(); gamma(); dyn(); expr(); tpl(); }\n",
        )
        plan = self.read()
        self.assertEqual(
            self.calls(plan),
            {
                ("app.js:run:9", "single.js:work:1"),
                ("app.js:run:9", "named.js:alpha:1"),
                ("app.js:run:9", "named.js:beta:2"),
                ("app.js:run:9", "props.js:delta:1"),
                ("app.js:run:9", "props.js:gamma:3"),
            },
        )
        imports = [
            (e["sources"][0], e["targets"][0])
            for e in plan["edges"]
            if e.get("labels")
        ]
        self.assertEqual(
            sorted(imports),
            [
                ("app.js", "named.js"),
                ("app.js", "props.js"),
                ("app.js", "side.js"),
                ("app.js", "single.js"),
            ],
        )

    def test_commonjs_require_against_esm_and_safety_guards(self):
        # Destructured require reaches ES named exports; bare require of an ES
        # module returns its namespace object, so main() proves nothing.
        esm = self.root / "esm_target"
        self.put(
            "esm_target/lib.js",
            "export default function main() {}\nexport function helper() {}\n",
        )
        self.put(
            "esm_target/app.js",
            "const main = require('./lib');\n"
            "const { helper: assist } = require('./lib');\n"
            "function run() { main(); assist(); }\n",
        )
        self.assertEqual(
            self.calls(self.read(esm)),
            {("app.js:run:3", "lib.js:helper:2")},
        )

        # Shadowed require, reassigned binding, and duplicate CJS exports refuse edges.
        guarded = self.root / "guarded"
        self.put(
            "guarded/dup.js",
            "function one() {}\nfunction two() {}\nexports.a = one;\nexports.a = two;\n",
        )
        self.put(
            "guarded/good.js",
            "function ok() {}\nmodule.exports = ok;\n",
        )
        self.put(
            "guarded/reassigned.js",
            "let ok = require('./good');\nok = () => {};\nfunction run() { ok(); }\n",
        )
        self.put(
            "guarded/shadowed.js",
            "function require() {}\nconst ok = require('./good');\nfunction run() { ok(); }\n",
        )
        self.put(
            "guarded/uses_dup.js",
            "const { a } = require('./dup');\nfunction run() { a(); }\n",
        )
        self.assertFalse(self.calls(self.read(guarded)))

    def test_commonjs_replaced_deleted_or_shadowed_exports_prove_nothing(self):
        cases = {
            "replaced_object": "function f() {}\nexports.f = f;\nmodule.exports = {};\n",
            "replaced_require": "function f() {}\nexports.f = f;\nmodule.exports = require('./other');\n",
            "replaced_if": "function f() {}\nexports.f = f;\nif (x) { module.exports = {}; }\n",
            "replaced_in_fn": "function f() {}\nexports.f = f;\nfunction reset() { module.exports = {}; }\n",
            "deleted": "function f() {}\nexports.f = f;\ndelete exports.f;\n",
            "rebound": "function f() {}\nexports = {};\nexports.f = f;\n",
            "shadowed": "function f() {}\nexports.f = f;\nfunction g(exports) { exports.f = 1; }\n",
            "aliased": "function f() {}\nexports.f = f;\nObject.assign(module.exports, {});\n",
            "after_replace": "function f() {}\nmodule.exports = {};\nexports.f = f;\n",
        }
        for label, lib in cases.items():
            with self.subTest(label=label):
                folder = self.root / label
                self.put(f"{label}/other.js", "module.exports = {};\n")
                self.put(f"{label}/lib.js", lib)
                self.put(
                    f"{label}/app.js",
                    "const { f } = require('./lib');\nfunction run() { f(); }\n",
                )
                self.assertFalse(
                    {c for c in self.calls(self.read(folder)) if c[0].startswith("app.js")}
                )

    def test_commonjs_properties_are_not_lexical_and_default_is_not_callable(self):
        self.put(
            "lib.js",
            "exports.a = () => 1;\nexports.default = function d() {};\n"
            "function local() { a(); }\n",
        )
        self.put(
            "app.js",
            "const lib = require('./lib');\n"
            "import d from './lib';\n"
            "const { a } = require('./lib');\n"
            "function run() { lib(); d(); a(); }\n",
        )
        # Controls: replacement then property, module.exports = { a, b }.
        self.put(
            "fn.js",
            "module.exports = function main() {};\nmodule.exports.helper = () => 2;\n",
        )
        self.put("obj.js", "function x() {}\nfunction y() {}\nmodule.exports = { x, y };\n")
        self.put(
            "use.js",
            "const main = require('./fn');\nconst { helper } = require('./fn');\n"
            "const { x, y } = require('./obj');\n"
            "function go() { main(); helper(); x(); y(); }\n",
        )
        self.assertEqual(
            self.calls(self.read()),
            {
                ("app.js:run:4", "lib.js:a:1"),
                ("use.js:go:4", "fn.js:main:1"),
                ("use.js:go:4", "fn.js:helper:2"),
                ("use.js:go:4", "obj.js:x:1"),
                ("use.js:go:4", "obj.js:y:2"),
            },
        )

    def test_directory_index_resolution_and_ambiguous_refusal(self):
        # Unambiguous directory index resolution for both ./utils and ./utils/
        clean = self.root / "clean"
        self.put("clean/utils/index.ts", "export function help() { return 1; }\n")
        self.put("clean/cjs/index.js", "function tool() {}\nmodule.exports = { tool };\n")
        self.put(
            "clean/app.ts",
            "import { help } from './utils';\n"
            "import { help as helpSlash } from './utils/';\n"
            "const { tool } = require('./cjs');\n"
            "export function run() { help(); helpSlash(); tool(); }\n",
        )
        plan = self.read(clean)
        self.assertEqual(
            self.calls(plan),
            {
                ("app.ts:run:4", "utils/index.ts:help:1"),
                ("app.ts:run:4", "cjs/index.js:tool:1"),
            },
        )
        imports = [
            (e["sources"][0], e["targets"][0])
            for e in plan["edges"]
            if e.get("labels")
        ]
        self.assertEqual(
            sorted(imports),
            [("app.ts", "cjs/index.js"), ("app.ts", "utils/index.ts")],
        )

        # Ambiguous directory index (multiple index.* files) refuses both import and call edges.
        ambig = self.root / "ambig_idx"
        self.put("ambig_idx/utils/index.js", "export function help() {}\n")
        self.put("ambig_idx/utils/index.ts", "export function help() {}\n")
        self.put(
            "ambig_idx/app.ts",
            "import { help } from './utils';\nexport function run() { help(); }\n",
        )
        ambig_plan = self.read(ambig)
        self.assertFalse(self.calls(ambig_plan))
        self.assertFalse(ambig_plan.get("edges"))

        # Unit checks across all 8 index extensions, trailing slash, and root '.' / './'.
        for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"):
            with self.subTest(ext=ext):
                target = f"src/utils/index{ext}"
                self.assertEqual(
                    _module("src/app.ts", "./utils", {target: None}), target
                )
                self.assertEqual(
                    _module("src/app.ts", "./utils/", {target: None}), target
                )
        self.assertIsNone(
            _module(
                "src/app.ts",
                "./utils",
                {"src/utils/index.mjs": None, "src/utils/index.mts": None},
            )
        )
        self.assertEqual(
            _module(
                "src/app.ts",
                "./utils/",
                {"src/utils.ts": None, "src/utils/index.ts": None},
            ),
            "src/utils/index.ts",
        )
        self.assertIsNone(
            _module("src/app.ts", "./utils/", {"src/utils.ts": None})
        )
        self.assertEqual(
            _module("app.ts", "./", {"index.ts": None}),
            "index.ts",
        )
        self.assertEqual(
            _module("app.ts", ".", {"index.ts": None}),
            "index.ts",
        )

    def test_directory_with_package_json_proves_no_directory_import(self):
        self.put("differ/package.json", '{"main": "entry.js"}')
        self.put("differ/entry.js", "function e() {}\nmodule.exports = e;\n")
        self.put("differ/index.js", "function i() {}\nmodule.exports = i;\n")
        self.put("only_entry/package.json", '{"main": "entry.js"}')
        self.put("only_entry/entry.js", "function e() {}\nmodule.exports = e;\n")
        self.put("plain/index.js", "function p() {}\nmodule.exports = p;\n")
        self.put(
            "app.js",
            "const a = require('./differ');\nconst b = require('./only_entry');\n"
            "const c = require('./plain');\nfunction run() { a(); b(); c(); }\n",
        )
        plan = self.read()
        self.assertEqual(self.calls(plan), {("app.js:run:4", "plain/index.js:p:1")})
        self.assertEqual(
            [e["targets"][0] for e in plan["edges"]], ["plain/index.js"]
        )

    def test_nested_or_extended_configs_withhold_root_aliases(self):
        nested = self.root / "nested"
        self.put(
            "nested/tsconfig.json",
            '{"compilerOptions": {"paths": {"@/*": ["src/*"]}}}',
        )
        self.put(
            "nested/frontend/tsconfig.json",
            '{"compilerOptions": {"paths": {"@/*": ["web/*"]}}}',
        )
        self.put("nested/src/util.ts", "export function util() {}\n")
        self.put("nested/frontend/web/util.ts", "export function util() {}\n")
        self.put(
            "nested/frontend/app.ts",
            "import { util } from '@/util';\nexport function front() { util(); }\n",
        )
        self.put(
            "nested/src/app.ts",
            "import { util } from '@/util';\nexport function back() { util(); }\n",
        )
        self.assertEqual(
            self.calls(self.read(nested)),
            {("src/app.ts:back:2", "src/util.ts:util:1")},
        )
        extended = self.root / "extended"
        self.put("extended/base.json", '{"compilerOptions": {"paths": {"@/*": ["alt/*"]}}}')
        self.put(
            "extended/tsconfig.json",
            '{"extends": "./base.json", "compilerOptions": {"paths": {"@/*": ["src/*"]}}}',
        )
        self.put("extended/src/util.ts", "export function util() {}\n")
        self.put(
            "extended/app.ts",
            "import { util } from '@/util';\nexport function run() { util(); }\n",
        )
        self.assertFalse(self.calls(self.read(extended)))

    def test_tsconfig_jsonc_path_aliases_and_baseurl(self):
        proj = self.root / "ts_aliases"
        self.put(
            "ts_aliases/tsconfig.json",
            """{
              // Line comment with "quotes"
              "compilerOptions": {
                /* Multi-line block comment
                   covering baseUrl and paths */
                "baseUrl": ".",
                "paths": {
                  "@/*": ["src/*"], // inline comment
                  "~components/*": ["components/*"],
                  "@exact": ["src/exact/entry"],
                  "@url-safe/*": ["src/*"], /* comment after comma */
                },
              },
            }
            """,
        )
        self.put("ts_aliases/src/utils/math.ts", "export function add() { return 1; }\n")
        self.put("ts_aliases/components/Button/index.tsx", "export function Button() { return <div/>; }\n")
        self.put("ts_aliases/src/exact/entry.ts", "export function boot() {}\n")
        self.put("ts_aliases/src/direct.ts", "export function direct() {}\n")
        self.put(
            "ts_aliases/src/app.tsx",
            "import { add } from '@/utils/math';\n"
            "import { Button } from '~components/Button';\n"
            "import { boot } from '@exact';\n"
            "import { direct } from 'src/direct';\n"
            "export function App() { add(); Button(); boot(); direct(); }\n",
        )
        plan = self.read(proj)
        self.assertEqual(
            self.calls(plan),
            {
                ("src/app.tsx:App:5", "src/utils/math.ts:add:1"),
                ("src/app.tsx:App:5", "components/Button/index.tsx:Button:1"),
                ("src/app.tsx:App:5", "src/exact/entry.ts:boot:1"),
                ("src/app.tsx:App:5", "src/direct.ts:direct:1"),
            },
        )

    def test_jsconfig_aliases_without_baseurl_and_ambiguous_or_broken_configs(self):
        # jsconfig.json with paths (no baseUrl) + CommonJS require
        js_proj = self.root / "js_aliases"
        self.put(
            "js_aliases/jsconfig.json",
            '{"compilerOptions": {"paths": {"@lib/*": ["lib/*"]}}}',
        )
        self.put("js_aliases/lib/fmt.js", "function fmt() {}\nmodule.exports = fmt;\n")
        self.put(
            "js_aliases/index.js",
            "const fmt = require('@lib/fmt');\nfunction run() { fmt(); }\n",
        )
        self.assertEqual(
            self.calls(self.read(js_proj)),
            {("index.js:run:2", "lib/fmt.js:fmt:1")},
        )

        # Ambiguous multi-target path alias refuses when both targets exist, resolves when one exists.
        ambig = self.root / "ambig_alias"
        self.put(
            "ambig_alias/tsconfig.json",
            '{"compilerOptions": {"paths": {"@/*": ["src/*", "alt/*"]}}}',
        )
        self.put("ambig_alias/src/one.ts", "export function one() {}\n")
        self.put("ambig_alias/src/both.ts", "export function both() {}\n")
        self.put("ambig_alias/alt/both.ts", "export function both() {}\n")
        self.put(
            "ambig_alias/app.ts",
            "import { one } from '@/one';\n"
            "import { both } from '@/both';\n"
            "export function run() { one(); both(); }\n",
        )
        self.assertEqual(
            self.calls(self.read(ambig)),
            {("app.ts:run:3", "src/one.ts:one:1")},
        )

        # Unparseable or root-escaping configs are ignored without crashing.
        for label, content in (
            ("syntax_err", "{ broken json"),
            ("unclosed_comment", '{"compilerOptions": /* unclosed }'),
            ("escape_root", '{"compilerOptions": {"baseUrl": "../outside", "paths": {"@/*": ["*"]}}}'),
            ("not_object", '["invalid"]'),
        ):
            with self.subTest(label=label):
                folder = self.root / f"broken_{label}"
                self.put(f"broken_{label}/tsconfig.json", content)
                self.put(f"broken_{label}/lib.ts", "export function ok() {}\n")
                self.put(
                    f"broken_{label}/app.ts",
                    "import { ok } from './lib';\n"
                    "import { nope } from '@/lib';\n"
                    "export function run() { ok(); nope(); }\n",
                )
                self.assertEqual(
                    self.calls(self.read(folder)),
                    {("app.ts:run:3", "lib.ts:ok:1")},
                )

    def badges_by_name(self, path):
        plan = self.read(path)
        return {
            box["labels"][0]["text"]: box["asbuilt"].get("badges")
            for box in self.boxes(plan).values()
            if "asbuilt" in box
        }

    def test_work_marks_env_detection_and_shadowing(self):
        path = self.put(
            "env.ts",
            "export function propEnv() { return process.env.API_KEY; }\n"
            "export function indexEnv() { return process.env['SECRET']; }\n"
            "export function destructEnv() { const { PORT } = process.env; return PORT; }\n"
            "export function viteEnv() { return import.meta.env.VITE_URL; }\n"
            "export function viteIndexEnv() { return import.meta.env['VITE_KEY']; }\n"
            "export function denoEnv() { return Deno.env.get('TOKEN'); }\n"
            "export function shadowProcParam(process: any) { return process.env.API_KEY; }\n"
            "export function shadowProcLocal() { const process = { env: { A: 1 } }; return process.env.A; }\n"
            "export function shadowDeno(Deno: any) { return Deno.env.get('X'); }\n"
            "export function metaUrlOnly() { return import.meta.url; }\n"
            "export function typeQueryOnly(x: typeof process.env) { return x; }\n"
            "export function procCheckOnly() { return typeof process !== 'undefined'; }\n",
        )
        self.assertEqual(
            self.badges_by_name(path),
            {
                "propEnv()": ["env"],
                "indexEnv()": ["env"],
                "destructEnv()": ["env"],
                "viteEnv()": ["env"],
                "viteIndexEnv()": ["env"],
                "denoEnv()": ["env"],
                "shadowProcParam()": None,
                "shadowProcLocal()": None,
                "shadowDeno()": None,
                "metaUrlOnly()": None,
                "typeQueryOnly()": None,
                "procCheckOnly()": None,
            },
        )
        # Named import from node:process also proves env; top-level const process shadows it.
        node_proc = self.put(
            "node_proc.ts",
            "import { env } from 'node:process';\n"
            "export function readImported() { return env.HOME; }\n",
        )
        self.assertEqual(
            self.badges_by_name(node_proc),
            {"readImported()": ["env"]},
        )
        top_shadow = self.put(
            "top_shadow.js",
            "const process = { env: {} };\n"
            "export function readFake() { return process.env.HOME; }\n",
        )
        self.assertEqual(
            self.badges_by_name(top_shadow),
            {"readFake()": None},
        )

    def test_work_marks_input_detection_and_shadowing(self):
        path = self.put(
            "input.ts",
            "import readline from 'node:readline';\n"
            "import { createInterface as makeRl } from 'readline';\n"
            "const rlCjs = require('readline');\n"
            "export function readArgv() { return process.argv.slice(2); }\n"
            "export function readStdin() { process.stdin.resume(); }\n"
            "export function useRl() { return readline.createInterface({}); }\n"
            "export function useNamedRl() { return makeRl({}); }\n"
            "export function useCjsRl() { return rlCjs.createInterface({}); }\n"
            "export function askUser() { return prompt('Name?'); }\n"
            "export function shadowPromptParam(prompt: any) { return prompt('Name?'); }\n"
            "export function promptValueOnly(prompt: string) { return prompt.trim(); }\n"
            "export function shadowProc(process: any) { return process.argv; }\n"
            "export function shadowRl(readline: any) { return readline.createInterface({}); }\n",
        )
        self.assertEqual(
            self.badges_by_name(path),
            {
                "readArgv()": ["input"],
                "readStdin()": ["input"],
                "useRl()": ["input"],
                "useNamedRl()": ["input"],
                "useCjsRl()": ["input"],
                "askUser()": ["input"],
                "shadowPromptParam()": None,
                "promptValueOnly()": None,
                "shadowProc()": None,
                "shadowRl()": None,
            },
        )
        fake_rl = self.put(
            "fake_rl.js",
            "const readline = { createInterface() {} };\n"
            "function prompt() {}\n"
            "export function run() { readline.createInterface({}); prompt('hi'); }\n",
        )
        self.assertEqual(
            self.badges_by_name(fake_rl),
            {"prompt()": None, "run()": None},
        )

    def test_work_marks_writes_fs_storage_and_cookies(self):
        fs_methods = (
            "writeFile",
            "writeFileSync",
            "appendFile",
            "appendFileSync",
            "createWriteStream",
            "mkdir",
            "mkdirSync",
            "rm",
            "rmSync",
            "unlink",
            "unlinkSync",
            "rename",
            "renameSync",
            "copyFile",
            "copyFileSync",
        )
        lines = [
            "import fs from 'fs';",
            "import * as nfs from 'node:fs';",
            "import { writeFile as wf } from 'fs/promises';",
            "import * as fsp from 'node:fs/promises';",
            "import { promises as fsPromises } from 'node:fs';",
        ]
        expected = {}
        for idx, method in enumerate(fs_methods):
            fn = f"m{idx}"
            lines.append(f"export function {fn}() {{ fs.{method}('a', 'b'); }}")
            expected[f"{fn}()"] = ["writes"]
        lines.extend(
            [
                "export function viaNodeFs() { nfs.promises.writeFile('a', 'b'); }",
                "export function viaNamedPromises() { wf('a', 'b'); }",
                "export function viaNsPromises() { fsp.rm('a'); }",
                "export function viaFsPromisesProp() { fsPromises.appendFile('a', 'b'); }",
                "export function readOnlyFs() { fs.readFileSync('a'); nfs.existsSync('a'); }",
                "export function setLocal() { localStorage.setItem('k', 'v'); }",
                "export function setSession() { sessionStorage.setItem('k', 'v'); }",
                "export function getLocal() { return localStorage.getItem('k'); }",
                "export function setCookie() { document.cookie = 'a=1'; }",
                "export function appendCookie() { document.cookie += '; path=/'; }",
                "export function readCookie() { return document.cookie; }",
                "export function shadowFs(fs) { fs.writeFileSync('a', 'b'); }",
                "export function shadowStorage(localStorage, sessionStorage, document) {",
                "  localStorage.setItem('k', 'v');",
                "  sessionStorage.setItem('k', 'v');",
                "  document.cookie = 'a=1';",
                "}",
            ]
        )
        expected.update(
            {
                "viaNodeFs()": ["writes"],
                "viaNamedPromises()": ["writes"],
                "viaNsPromises()": ["writes"],
                "viaFsPromisesProp()": ["writes"],
                "readOnlyFs()": None,
                "setLocal()": ["writes"],
                "setSession()": ["writes"],
                "getLocal()": None,
                "setCookie()": ["writes"],
                "appendCookie()": ["writes"],
                "readCookie()": None,
                "shadowFs()": None,
                "shadowStorage()": None,
            }
        )
        path = self.put("writes.js", "\n".join(lines) + "\n")
        self.assertEqual(self.badges_by_name(path), expected)

        # CommonJS require and negative unimported fs cases.
        cjs = self.put(
            "writes_cjs.js",
            "const { writeFileSync: save } = require('node:fs');\n"
            "const fsp = require('fs/promises');\n"
            "export function cjsSave() { save('a', 'b'); }\n"
            "export function cjsFsp() { fsp.mkdir('d'); }\n",
        )
        self.assertEqual(
            self.badges_by_name(cjs),
            {"cjsSave()": ["writes"], "cjsFsp()": ["writes"]},
        )
        fake_fs = self.put(
            "fake_fs.js",
            "const fs = { writeFile() {}, writeFileSync() {} };\n"
            "export function notReal() { fs.writeFile('a', 'b'); fs.writeFileSync('a', 'b'); }\n",
        )
        self.assertEqual(self.badges_by_name(fake_fs), {"notReal()": None})

    def test_work_marks_calls_out_network_and_subprocess(self):
        path = self.put(
            "out.ts",
            "import http from 'http';\n"
            "import * as https from 'node:https';\n"
            "import { get as httpGet } from 'node:http';\n"
            "import axios from 'axios';\n"
            "import cp, { execSync, spawn as spawnProc } from 'node:child_process';\n"
            "const api = axios.create({ baseURL: 'https://example.com' });\n"
            "export function useFetch() { return fetch('https://example.com'); }\n"
            "export function useNestedFetch() { return () => fetch('https://example.com'); }\n"
            "export function useXhr() { return new XMLHttpRequest(); }\n"
            "export function useWs() { return new WebSocket('wss://example.com'); }\n"
            "export function useHttpReq() { return http.request('http://example.com'); }\n"
            "export function useHttpsGet() { return https.get('https://example.com'); }\n"
            "export function useNamedHttpGet() { return httpGet('http://example.com'); }\n"
            "export function useAxiosGet() { return axios.get('https://example.com'); }\n"
            "export function useAxiosCall() { return axios('https://example.com'); }\n"
            "export function useAxiosClient() { return api.post('/items', {}); }\n"
            "export function useExec() { return cp.exec('ls'); }\n"
            "export function useExecSync() { return execSync('ls'); }\n"
            "export function useSpawn() { return spawnProc('ls'); }\n"
            "export function useSpawnSync() { return cp.spawnSync('ls'); }\n"
            "export function useExecFile() { return cp.execFile('ls'); }\n"
            "export function useExecFileSync() { return cp.execFileSync('ls'); }\n"
            "export function httpServerOnly() { return http.createServer(() => {}); }\n"
            "export function shadowFetchParam(fetch: any) { return fetch('https://example.com'); }\n"
            "export function shadowFetchLocal() { const fetch = () => 1; return fetch(); }\n"
            "export function shadowXhr(XMLHttpRequest: any, WebSocket: any) { new XMLHttpRequest(); new WebSocket(); }\n"
            "export function checkWsOnly() { return typeof WebSocket !== 'undefined'; }\n"
            "export function methodFetch(obj: any) { return obj.fetch('https://example.com'); }\n",
        )
        self.assertEqual(
            self.badges_by_name(path),
            {
                "useFetch()": ["calls out"],
                "useNestedFetch()": ["calls out"],
                "useXhr()": None,
                "useWs()": ["calls out"],
                "useHttpReq()": ["calls out"],
                "useHttpsGet()": ["calls out"],
                "useNamedHttpGet()": ["calls out"],
                "useAxiosGet()": ["calls out"],
                "useAxiosCall()": ["calls out"],
                "useAxiosClient()": ["calls out"],
                "useExec()": ["calls out"],
                "useExecSync()": ["calls out"],
                "useSpawn()": ["calls out"],
                "useSpawnSync()": ["calls out"],
                "useExecFile()": ["calls out"],
                "useExecFileSync()": ["calls out"],
                "httpServerOnly()": None,
                "shadowFetchParam()": None,
                "shadowFetchLocal()": None,
                "shadowXhr()": None,
                "checkWsOnly()": None,
                "methodFetch()": None,
            },
        )
        # Local function fetch() and unimported axios/child_process must not mark.
        shadowed = self.put(
            "shadow_out.js",
            "function fetch() { return 1; }\n"
            "const axios = { get() {} };\n"
            "const child_process = { execSync() {} };\n"
            "export function run() { fetch(); axios.get('x'); child_process.execSync('ls'); }\n",
        )
        self.assertEqual(
            self.badges_by_name(shadowed),
            {"fetch()": None, "run()": None},
        )

    def test_network_badge_needs_a_request_not_a_helper(self):
        path = self.put(
            "helpers.ts",
            "import axios from 'axios';\n"
            "const api = axios.create({ baseURL: 'https://example.com' });\n"
            "export function isErr(e: any) { return axios.isAxiosError(e); }\n"
            "export function uri() { return api.getUri(); }\n"
            "export function make() { return axios.create(); }\n"
            "export function bare() { const x = new XMLHttpRequest(); x.open('GET', '/a'); return x; }\n"
            "export function sent() { const x = new XMLHttpRequest(); x.open('GET', '/a'); x.send(); }\n"
            "export function clientCall() { return api('/items'); }\n"
            "export function clientReq() { return api.request({}); }\n",
        )
        self.assertEqual(
            self.badges_by_name(path),
            {
                "isErr()": None,
                "uri()": None,
                "make()": None,
                "bare()": None,
                "sent()": ["calls out"],
                "clientCall()": ["calls out"],
                "clientReq()": ["calls out"],
            },
        )

    def test_work_marks_resolve_writes_and_switch_declarations_per_scope(self):
        path = self.put(
            "scoped.js",
            "import OpenAI from 'openai';\n"
            "const openai = new OpenAI();\n"
            "export function wrap(fetch) { fetch = fetch.bind(null); return fetch('/x'); }\n"
            "export function real() { return fetch('https://example.com'); }\n"
            "export function retry(openai) { openai = null; return openai; }\n"
            "export function ask() { return openai.chat.completions.create({}); }\n"
            "export function pick(k) { switch (k) { case 1: const fetch = () => 1; return fetch(); } }\n"
            "export function env(k) { switch (k) { default: let process = { env: {} }; return process.env.A; } }\n"
            "export function client(k) { switch (k) { case 1: const openai = { chat: {} }; return openai.chat.completions.create({}); } }\n"
            "export function caseValue(k) { switch (k) { case fetch('/a'): return 1; } }\n"
            "export function rebind() { fetch = null; }\n"
            "export function later() { return fetch('/b'); }\n",
        )
        self.assertEqual(
            self.badges_by_name(path),
            {
                "wrap()": None,
                "real()": None,
                "retry()": None,
                "ask()": ["model"],
                "pick()": None,
                "env()": None,
                "client()": None,
                "caseValue()": None,
                "rebind()": None,
                "later()": None,
            },
        )
        without_rebind = self.put(
            "scoped2.js",
            "export function wrap(fetch) { fetch = fetch.bind(null); return fetch('/x'); }\n"
            "export function real() { return fetch('https://example.com'); }\n"
            "export function caseValue(k) { switch (k) { case fetch('/a'): return 1; } }\n",
        )
        self.assertEqual(
            self.badges_by_name(without_rebind),
            {"wrap()": None, "real()": ["calls out"], "caseValue()": ["calls out"]},
        )

    def test_work_marks_model_sdks_and_client_instances(self):
        path = self.put(
            "models.ts",
            "import OpenAI from 'openai';\n"
            "import Anthropic from '@anthropic-ai/sdk';\n"
            "import { GoogleGenAI } from '@google/genai';\n"
            "import { GoogleGenerativeAI } from '@google/generative-ai';\n"
            "import { generateText, streamText as stream } from 'ai';\n"
            "const openai = new OpenAI();\n"
            "let reassigned = new OpenAI();\n"
            "reassigned = {} as any;\n"
            "const gen = new GoogleGenerativeAI('k');\n"
            "const geminiModel = gen.getGenerativeModel({ model: 'gemini-2.0-flash' });\n"
            "export function makeOpenAI() { return new OpenAI(); }\n"
            "export function makeAnthropic() { return new Anthropic(); }\n"
            "export function makeGoogleGenAI() { return new GoogleGenAI({}); }\n"
            "export function callGenerateText() { return generateText({ prompt: 'hi' }); }\n"
            "export function callStreamText() { return stream({ prompt: 'hi' }); }\n"
            "export function useModuleClient() { return openai.chat.completions.create({}); }\n"
            "export function useDerivedClient() { return geminiModel.generateContent('hi'); }\n"
            "export function returnClientOnly() { return openai; }\n"
            "export function useReassignedClient() { return reassigned.chat.completions.create({}); }\n"
            "export function shadowClientParam(openai: any) { return openai.chat.completions.create({}); }\n"
            "export function shadowCtorParam(OpenAI: any) { return new OpenAI(); }\n"
            "export function typeOnlyParam(client: OpenAI) { return client; }\n",
        )
        self.assertEqual(
            self.badges_by_name(path),
            {
                "makeOpenAI()": ["model"],
                "makeAnthropic()": ["model"],
                "makeGoogleGenAI()": ["model"],
                "callGenerateText()": ["model"],
                "callStreamText()": ["model"],
                "useModuleClient()": ["model"],
                "useDerivedClient()": ["model"],
                "returnClientOnly()": None,
                "useReassignedClient()": None,
                "shadowClientParam()": None,
                "shadowCtorParam()": None,
                "typeOnlyParam()": None,
            },
        )
        # Unimported OpenAI / generateText must not produce model marks.
        fake_ai = self.put(
            "fake_ai.js",
            "class OpenAI {}\n"
            "function generateText() {}\n"
            "export function run() { generateText(); return new OpenAI(); }\n",
        )
        self.assertEqual(
            self.badges_by_name(fake_ai),
            {"OpenAI": None, "generateText()": None, "run()": None},
        )

    def test_work_marks_canonical_order_classes_scoping_and_all_grammars(self):
        # Canonical order matches marks._ORDER regardless of source order; pure box omits badges.
        for suffix in ("js", "jsx", "ts", "tsx"):
            with self.subTest(suffix=suffix):
                jsx = "<div/>" if suffix in ("jsx", "tsx") else "1"
                path = self.put(
                    f"order.{suffix}",
                    "import OpenAI from 'openai';\n"
                    "import { writeFileSync } from 'node:fs';\n"
                    "const client = new OpenAI();\n"
                    "export function everything() {\n"
                    "  client.responses.create({});\n"
                    "  fetch('https://example.com');\n"
                    "  writeFileSync('out.txt', 'ok');\n"
                    "  prompt('continue?');\n"
                    "  return process.env.MODE;\n"
                    "}\n"
                    f"export const Pure = () => {jsx};\n"
                    "export class Worker {\n"
                    "  fetch() { return 1; }\n"
                    "  run() { fetch('https://example.com'); return process.env.NODE_ENV; }\n"
                    "}\n"
                    "export function blockScoping() {\n"
                    "  { const fetch = () => 1; fetch(); }\n"
                    "  return fetch('https://example.com');\n"
                    "}\n"
                    "export function hoistedVarShadows() {\n"
                    "  if (false) { var fetch = () => 1; }\n"
                    "  return fetch('https://example.com');\n"
                    "}\n",
                )
                plan = self.read(path)
                boxes = {
                    b["labels"][0]["text"]: b["asbuilt"]
                    for b in self.boxes(plan).values()
                    if "asbuilt" in b
                }
                self.assertEqual(boxes["everything()"]["badges"], list(MARK_ORDER))
                self.assertNotIn("badges", boxes["Pure()"])
                self.assertEqual(boxes["Worker"]["badges"], ["env", "calls out"])
                self.assertEqual(boxes["blockScoping()"]["badges"], ["calls out"])
                self.assertNotIn("badges", boxes["hoistedVarShadows()"])

    def test_component_wrappers_memo_forwardref_definecomponent_and_local_refs(self):
        self.put(
            "helpers.ts",
            "export function formatLabel(s: string) { return s; }\n",
        )
        self.put(
            "components.tsx",
            "import React, { memo, forwardRef } from 'react';\n"
            "import { defineComponent } from 'vue';\n"
            "import { formatLabel } from './helpers';\n"
            "function BaseIcon() {\n"
            "  formatLabel('icon');\n"
            "  return <span/>;\n"
            "}\n"
            "export const MemoBtn = React.memo(function(props: any) {\n"
            "  formatLabel(props.label);\n"
            "  fetch('https://example.com');\n"
            "  return <button/>;\n"
            "});\n"
            "export const ShortMemo = memo((props: any) => {\n"
            "  formatLabel(props.label);\n"
            "  return <div/>;\n"
            "});\n"
            "export const ForwardInput = React.forwardRef<HTMLInputElement, any>((props, ref) => {\n"
            "  formatLabel('input');\n"
            "  return <input ref={ref}/>;\n"
            "});\n"
            "export const ShortForward = forwardRef(function InnerInput(props: any, ref: any) {\n"
            "  return <input ref={ref}/>;\n"
            "});\n"
            "export const ChainedCard = React.memo(React.forwardRef((props: any, ref: any) => {\n"
            "  formatLabel(process.env.CARD_TITLE || '');\n"
            "  return <section ref={ref}/>;\n"
            "}));\n"
            "export const ShortChained = memo(forwardRef(function(props: any, ref: any) {\n"
            "  MemoBtn(props);\n"
            "  return <article ref={ref}/>;\n"
            "}));\n"
            "export const VueWidget = defineComponent((props: any) => {\n"
            "  formatLabel('vue');\n"
            "  return () => <div/>;\n"
            "});\n"
            "export const WrappedIcon = React.memo(BaseIcon);\n"
            "export const NonFnDefined = defineComponent({ name: 'Ignored' });\n",
        )
        self.put(
            "app.tsx",
            "import { MemoBtn, ChainedCard, WrappedIcon } from './components';\n"
            "export function App() {\n"
            "  MemoBtn({});\n"
            "  ChainedCard({});\n"
            "  WrappedIcon();\n"
            "  return <main/>;\n"
            "}\n",
        )
        plan = self.read()
        boxes = self.boxes(plan)
        for expected_id in (
            "components.tsx:BaseIcon:4",
            "components.tsx:MemoBtn:8",
            "components.tsx:ShortMemo:13",
            "components.tsx:ForwardInput:17",
            "components.tsx:ShortForward:21",
            "components.tsx:ChainedCard:24",
            "components.tsx:ShortChained:28",
            "components.tsx:VueWidget:32",
            "components.tsx:WrappedIcon:36",
        ):
            self.assertIn(expected_id, boxes)
        self.assertNotIn("components.tsx:NonFnDefined:37", boxes)
        self.assertEqual(
            boxes["components.tsx:MemoBtn:8"]["asbuilt"].get("badges"),
            ["calls out"],
        )
        self.assertEqual(
            boxes["components.tsx:ChainedCard:24"]["asbuilt"].get("badges"),
            ["env"],
        )
        self.assertEqual(
            boxes["components.tsx:WrappedIcon:36"]["labels"],
            [{"text": "WrappedIcon()"}],
        )
        self.assertEqual(
            self.calls(plan),
            {
                ("components.tsx:BaseIcon:4", "helpers.ts:formatLabel:1"),
                ("components.tsx:MemoBtn:8", "helpers.ts:formatLabel:1"),
                ("components.tsx:ShortMemo:13", "helpers.ts:formatLabel:1"),
                ("components.tsx:ForwardInput:17", "helpers.ts:formatLabel:1"),
                ("components.tsx:ChainedCard:24", "helpers.ts:formatLabel:1"),
                ("components.tsx:ShortChained:28", "components.tsx:MemoBtn:8"),
                ("components.tsx:VueWidget:32", "helpers.ts:formatLabel:1"),
                ("components.tsx:WrappedIcon:36", "helpers.ts:formatLabel:1"),
                ("app.tsx:App:2", "components.tsx:MemoBtn:8"),
                ("app.tsx:App:2", "components.tsx:ChainedCard:24"),
                ("app.tsx:App:2", "components.tsx:WrappedIcon:36"),
            },
        )

    def test_anonymous_default_exports_use_file_stem_or_default_fallback(self):
        self.put(
            "Button.tsx",
            "export default function(props: any) {\n"
            "  fetch('https://example.com');\n"
            "  return <button/>;\n"
            "}\n",
        )
        self.put(
            "Card.tsx",
            "export default (props: any) => <div/>;\n",
        )
        self.put(
            "Panel/index.tsx",
            "export default () => <section/>;\n",
        )
        self.put(
            "Taken.tsx",
            "function Taken() { return 1; }\n"
            "export default () => Taken();\n",
        )
        self.put(
            "app.tsx",
            "import Button from './Button';\n"
            "import Card from './Card';\n"
            "import Panel from './Panel';\n"
            "import MakeTaken from './Taken';\n"
            "export function App() {\n"
            "  Button({});\n"
            "  Card({});\n"
            "  Panel();\n"
            "  MakeTaken();\n"
            "}\n",
        )
        plan = self.read()
        boxes = self.boxes(plan)
        self.assertIn("Button.tsx:Button:1", boxes)
        self.assertEqual(
            boxes["Button.tsx:Button:1"]["labels"], [{"text": "Button()"}]
        )
        self.assertEqual(
            boxes["Button.tsx:Button:1"]["asbuilt"].get("badges"), ["calls out"]
        )
        self.assertIn("Card.tsx:Card:1", boxes)
        self.assertIn("Panel/index.tsx:default:1", boxes)
        self.assertIn("Taken.tsx:Taken:1", boxes)
        self.assertIn("Taken.tsx:default:2", boxes)
        self.assertEqual(
            self.calls(plan),
            {
                ("Taken.tsx:default:2", "Taken.tsx:Taken:1"),
                ("app.tsx:App:5", "Button.tsx:Button:1"),
                ("app.tsx:App:5", "Card.tsx:Card:1"),
                ("app.tsx:App:5", "Panel/index.tsx:default:1"),
                ("app.tsx:App:5", "Taken.tsx:default:2"),
            },
        )

    def test_p3_main_validates_input_before_output_and_allows_skip_named_project_root(self):
        # 1. Invalid/excluded input path reports the input path error even if OUT is invalid.
        missing = self.root / "missing.ts"
        bad_out = self.root / "bad.txt"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(missing), str(bad_out)]), 2)
        self.assertIn("missing.ts", err.getvalue())
        self.assertNotIn("OUT must be JSON", err.getvalue())

        excluded_file = self.put("src/app.test.ts", "function t() {}\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(excluded_file), str(bad_out)]), 2)
        self.assertIn("app.test.ts", err.getvalue())
        self.assertNotIn("OUT must be JSON", err.getvalue())

        # 2. A directory root whose own name is in SKIP (e.g. 'test' or 'vendor')
        # is allowed when it is itself a standalone project root (.git or package.json),
        # while node_modules and nested proj/tests are still refused.
        for folder_name, marker in (("test", "package.json"), ("vendor", ".git")):
            repo = self.root / f"standalone_{marker}" / folder_name
            self.put(f"standalone_{marker}/{folder_name}/{marker}", "{}")
            self.put(
                f"standalone_{marker}/{folder_name}/src/app.ts",
                "export function boot() { return 1; }\n",
            )
            plan = self.read(repo)
            self.assertIn("src/app.ts:boot:1", self.boxes(plan))

        nm_root = self.root / "standalone_nm" / "node_modules"
        self.put("standalone_nm/node_modules/package.json", "{}")
        self.put("standalone_nm/node_modules/index.js", "function dep() {}\n")
        with self.assertRaises(PlanError):
            self.read(nm_root)

    def test_find_components_cli_web_scripts_shared_and_no_path_found(self):
        self.put(
            "package.json",
            '{"name": "acme-app", "bin": {"acme-cli": "./bin/cli.ts", "missing-bin": "./bin/nope.js"}}\n',
        )
        self.put(
            "src/shared/format.ts",
            "export function formatLabel(text: string) {\n"
            "  return normalize(text);\n"
            "}\n"
            "function normalize(s: string) {\n"
            "  return s.trim();\n"
            "}\n"
            "export function cliOnlyHelper() {\n"
            "  return 'cli';\n"
            "}\n"
            "export function webOnlyHelper() {\n"
            "  return 'web';\n"
            "}\n"
            "export function orphanHelper() {\n"
            "  return 'unused';\n"
            "}\n",
        )
        self.put(
            "bin/cli.ts",
            "#!/usr/bin/env node\n"
            "import { formatLabel, cliOnlyHelper } from '../src/shared/format';\n"
            "export function runCli() {\n"
            "  const args = process.argv.slice(2);\n"
            "  cliOnlyHelper();\n"
            "  return formatLabel(args.join(' '));\n"
            "}\n",
        )
        self.put(
            "src/pages/dashboard.tsx",
            "import { formatLabel, webOnlyHelper } from '../shared/format';\n"
            "export function DashboardPage() {\n"
            "  webOnlyHelper();\n"
            "  return <section>{formatLabel('Dashboard')}</section>;\n"
            "}\n",
        )
        self.put(
            "src/sync.ts",
            "import { formatLabel } from './shared/format';\n"
            "function runSync() {\n"
            "  return formatLabel('sync');\n"
            "}\n"
            "runSync();\n",
        )

        found = find_components(self.root)
        self.assertEqual(
            found["groups"],
            [
                {"kind": "commands", "components": ["acme-cli"]},
                {"kind": "web pages", "components": ["DashboardPage"]},
                {"kind": "scripts", "components": ["runSync"]},
            ],
        )

        by_name = {c["name"]: c for c in found["components"]}
        self.assertEqual(by_name["acme-cli"]["kind"], "commands")
        self.assertEqual(
            {m["name"] for m in by_name["acme-cli"]["members"]},
            {"runCli()", "cliOnlyHelper()"},
        )
        self.assertEqual(by_name["DashboardPage"]["kind"], "web pages")
        self.assertEqual(
            {m["name"] for m in by_name["DashboardPage"]["members"]},
            {"DashboardPage()", "webOnlyHelper()"},
        )
        self.assertEqual(by_name["runSync"]["kind"], "scripts")
        self.assertEqual(
            {m["name"] for m in by_name["runSync"]["members"]},
            {"runSync()"},
        )

        self.assertEqual(len(found["shared"]), 1)
        shared_file = found["shared"][0]
        self.assertEqual(shared_file["file"], "src/shared/format.ts")
        self.assertEqual(
            shared_file["used_by"],
            ["DashboardPage", "acme-cli", "runSync"],
        )
        self.assertEqual(
            {m["name"]: m["used_by"] for m in shared_file["members"]},
            {
                "formatLabel()": ["DashboardPage", "acme-cli", "runSync"],
                "normalize()": ["DashboardPage", "acme-cli", "runSync"],
            },
        )

        self.assertEqual(
            found["no_path_found"],
            [
                {
                    "id": "src/shared/format.ts:orphanHelper:13",
                    "name": "orphanHelper()",
                    "file": "src/shared/format.ts",
                    "lines": [13, 15],
                }
            ],
        )
        self.assertEqual(
            found["unproven"],
            ["package.json bin missing-bin -> ./bin/nope.js: not a file charted here"],
        )

    def test_find_components_react_dom_mount_http_routes_and_dom_listeners(self):
        self.put(
            "src/shared.ts",
            "export function logEvent(name: string) {\n  return name;\n}\n"
            "export function unreachedFn() {\n  return 0;\n}\n",
        )
        self.put(
            "src/App.tsx",
            "import { logEvent } from './shared';\n"
            "export function App() {\n"
            "  return <div>{logEvent('render')}</div>;\n"
            "}\n",
        )
        self.put(
            "src/client.tsx",
            "import { createRoot } from 'react-dom/client';\n"
            "import { App } from './App';\n"
            "export function mountClient() {\n"
            "  const root = createRoot(document.getElementById('root')!);\n"
            "  root.render(<App />);\n"
            "}\n",
        )
        self.put(
            "src/server.ts",
            "import express from 'express';\n"
            "import { logEvent } from './shared';\n"
            "const app = express();\n"
            "function handleHealth() {\n"
            "  return logEvent('health');\n"
            "}\n"
            "app.get('/health', handleHealth);\n",
        )

        found = find_components(self.root)
        self.assertEqual(
            found["groups"],
            [{"kind": "web pages", "components": ["mountClient", "server"]}],
        )
        by_name = {c["name"]: c for c in found["components"]}
        self.assertEqual(
            {m["name"] for m in by_name["mountClient"]["members"]},
            {"mountClient()", "App()"},
        )
        self.assertEqual(
            {m["name"] for m in by_name["server"]["members"]},
            {"handleHealth()"},
        )
        self.assertEqual(len(found["shared"]), 1)
        self.assertEqual(
            found["shared"][0]["members"][0]["used_by"],
            ["mountClient", "server"],
        )
        self.assertEqual(
            [m["name"] for m in found["no_path_found"]],
            ["unreachedFn()"],
        )

        # Verify on asBuilt's own src/asbuilt/page JS modules (main.js and overview.js)
        page_dir = Path(__file__).resolve().parent.parent / "src" / "asbuilt" / "page"
        page_found = find_components(page_dir)
        self.assertEqual([g["kind"] for g in page_found["groups"]], ["web pages"])
        self.assertIn("main", page_found["groups"][0]["components"])

    def test_proven_single_call_return_on_js_and_ts_functions(self):
        path = self.put(
            "proven.ts",
            "function helper(x: number) { return x + 1; }\n"
            "export function passBlock(x: number) { return helper(x); }\n"
            "export const passArrow = (x: number) => helper(x);\n"
            "export async function passAwait(x: number) { return await (helper(x)); }\n"
            "export function nestedCall(x: number) { return helper(helper(x)); }\n"
            "export function twoStmts(x: number) { const y = x; return helper(y); }\n"
            "export function nonCall(x: number) { return x + 1; }\n"
            "export class Box { run(x: number) { return helper(x); } }\n",
        )
        plan = self.read(path)
        by_label = {
            box["labels"][0]["text"]: box["asbuilt"]
            for box in self.boxes(plan).values()
            if "asbuilt" in box
        }
        self.assertEqual(by_label["passBlock()"].get("proven"), PASSES)
        self.assertEqual(by_label["passArrow()"].get("proven"), PASSES)
        self.assertEqual(by_label["passAwait()"].get("proven"), PASSES)
        self.assertNotIn("proven", by_label["helper()"])
        self.assertNotIn("proven", by_label["nestedCall()"])
        self.assertNotIn("proven", by_label["twoStmts()"])
        self.assertNotIn("proven", by_label["nonCall()"])
        self.assertNotIn("proven", by_label["Box"])

    def test_security_and_plain_facts_env_net_proc_file_and_throws(self):
        path = self.put(
            "security_notes.ts",
            "import fs from 'node:fs';\n"
            "import { execSync } from 'node:child_process';\n"
            "import axios from 'axios';\n"
            "export function readEnv() {\n"
            "  const a = process.env.API_KEY;\n"
            "  const b = process.env['SECRET_TOKEN'];\n"
            "  const c = import.meta.env.VITE_URL;\n"
            "  const d = Deno.env.get('DENO_KEY');\n"
            "  return [a, b, c, d];\n"
            "}\n"
            "export function callNet() {\n"
            "  fetch('https://api.example.com/v1/users');\n"
            "  return axios.get('https://auth.example.org:8443/token');\n"
            "}\n"
            "export function runProc() {\n"
            "  return execSync('ls -la');\n"
            "}\n"
            "export function writeDisk() {\n"
            "  fs.writeFileSync('out.txt', 'hello');\n"
            "}\n"
            "export function failFast(x: string) {\n"
            "  if (!x) {\n"
            "    throw new Error('missing input');\n"
            "  }\n"
            "  return x;\n"
            "}\n"
            "export function caughtThrow() {\n"
            "  try { throw new Error('swallowed'); } catch (e) { return 1; }\n"
            "}\n"
            "export function shadowAll(process: any, Deno: any, fetch: any, fs: any) {\n"
            "  process.env.API_KEY;\n"
            "  Deno.env.get('DENO_KEY');\n"
            "  fetch('https://api.example.com');\n"
            "  fs.writeFileSync('out.txt', 'no');\n"
            "}\n",
        )
        plan = self.read(path)
        by_label = {
            box["labels"][0]["text"]: box["asbuilt"]
            for box in self.boxes(plan).values()
            if "asbuilt" in box
        }

        self.assertEqual(
            by_label["readEnv()"]["security"],
            [
                {
                    "fact": {"kind": "env", "how": "read", "name": "API_KEY"},
                    "plain": "This reads API_KEY from the environment.",
                    "expert": "process.env.API_KEY reads API_KEY from the environment.",
                },
                {
                    "fact": {"kind": "env", "how": "read", "name": "SECRET_TOKEN"},
                    "plain": "This reads SECRET_TOKEN from the environment.",
                    "expert": "process.env['SECRET_TOKEN'] reads SECRET_TOKEN from the environment.",
                },
                {
                    "fact": {"kind": "env", "how": "read", "name": "VITE_URL"},
                    "plain": "This reads VITE_URL from the environment.",
                    "expert": "import.meta.env.VITE_URL reads VITE_URL from the environment.",
                },
                {
                    "fact": {"kind": "env", "how": "read", "name": "DENO_KEY"},
                    "plain": "This reads DENO_KEY from the environment.",
                    "expert": "Deno.env.get('DENO_KEY') reads DENO_KEY from the environment.",
                },
            ],
        )
        # plain is capped at 3 everyday sentences
        self.assertEqual(
            by_label["readEnv()"]["plain"],
            [
                "Reads API_KEY from the environment.",
                "Reads SECRET_TOKEN from the environment.",
                "Reads VITE_URL from the environment.",
            ],
        )

        self.assertEqual(
            by_label["callNet()"]["security"],
            [
                {
                    "fact": {"kind": "net", "how": "http", "host": "api.example.com"},
                    "plain": "This connects to api.example.com over the network.",
                    "expert": "Connects to api.example.com via fetch.",
                },
                {
                    "fact": {"kind": "net", "how": "http", "host": "auth.example.org"},
                    "plain": "This connects to auth.example.org over the network.",
                    "expert": "Connects to auth.example.org via axios.get.",
                },
            ],
        )
        self.assertEqual(
            by_label["callNet()"]["plain"],
            [
                "Talks to api.example.com over the network.",
                "Talks to auth.example.org over the network.",
            ],
        )

        self.assertEqual(
            by_label["runProc()"]["security"],
            [
                {
                    "fact": {"kind": "proc", "how": "run"},
                    "plain": "This runs another program on the computer.",
                    "expert": "Runs a subprocess via execSync.",
                }
            ],
        )
        self.assertEqual(
            by_label["runProc()"]["plain"],
            ["Runs another program on the computer."],
        )

        self.assertEqual(
            by_label["writeDisk()"]["security"],
            [
                {
                    "fact": {"kind": "file", "how": "write"},
                    "plain": "This writes or changes files on disk.",
                    "expert": "Writes to disk via fs.writeFileSync.",
                }
            ],
        )
        self.assertEqual(
            by_label["writeDisk()"]["plain"],
            ["Writes or changes files on disk."],
        )

        self.assertNotIn("security", by_label["failFast()"])
        self.assertEqual(
            by_label["failFast()"]["plain"],
            ['Can stop with an error: "missing input".'],
        )
        self.assertNotIn("plain", by_label["caughtThrow()"])
        self.assertNotIn("security", by_label["shadowAll()"])
        self.assertNotIn("plain", by_label["shadowAll()"])

    def test_find_components_reuses_provided_chart_data_without_recharting(self):
        self.put(
            "src/index.ts",
            "export function main() { return process.argv.slice(2); }\n",
        )
        skipped: list[str] = []
        precomputed = chart(self.root, skipped=skipped)
        with mock.patch("asbuilt.javascript.chart") as mocked_chart:
            comp = find_components(self.root, skipped=skipped, chart_data=precomputed)
            mocked_chart.assert_not_called()
        self.assertEqual(
            comp["groups"],
            [{"kind": "commands", "components": ["main"]}],
        )


    def test_mts_and_cts_fallbacks_chart_through_the_folder_reader(self):
        folder = self.root / "esm"
        self.put("esm/util.mts", "export function util() { return 1; }\n")
        self.put("esm/mod.cts", "export function mod() { return 2; }\n")
        self.put("esm/types.d.mts", "export declare function t(): void;\n")
        self.put(
            "esm/app.mts",
            "import { util } from './util.mjs';\nimport { mod } from './mod.cjs';\n"
            "export function app() { return util() + mod(); }\n",
        )
        self.assertEqual(
            self.calls(self.read(folder)),
            {("app.mts:app:3", "util.mts:util:1"), ("app.mts:app:3", "mod.cts:mod:1")},
        )
        names = {box["id"] for box in self.read(folder)["children"]}
        self.assertNotIn("types.d.mts", names)

    def test_a_skipped_file_still_blocks_a_fallback_or_breaks_a_tie(self):
        broken = "export function (\n"
        good = "export function util() { return 1; }\n"
        app = "import { util } from './%s';\nexport function app() { return util(); }\n"
        # Explicit ./util.js names a skipped file: no arrow to util.ts.
        self.put("exact/util.js", broken)
        self.put("exact/util.ts", good)
        self.put("exact/app.ts", app % "util.js")
        # Extensionless ./util has two candidates; skipping one must not make it unique.
        self.put("tie/util.js", broken)
        self.put("tie/util.ts", good)
        self.put("tie/app.ts", app % "util")
        # Control: the only candidate parses, so the fallback still works.
        self.put("ok/util.ts", good)
        self.put("ok/app.ts", app % "util.js")
        for folder in ("exact", "tie"):
            with self.subTest(folder=folder):
                with contextlib.redirect_stderr(io.StringIO()):
                    plan = self.read(self.root / folder)
                self.assertFalse(self.calls(plan))
                self.assertFalse([e for e in plan.get("edges", []) if "import:" in e["id"]])
        self.assertEqual(
            self.calls(self.read(self.root / "ok")), {("app.ts:app:2", "util.ts:util:1")}
        )
