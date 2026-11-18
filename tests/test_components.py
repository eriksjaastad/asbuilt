import contextlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from asbuilt.components import NO_PATH, find, main, summary

MAIN = 'if __name__ == "__main__":\n    main()\n'


def tree(files):
    """A temporary folder holding these {relative path: text} files."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "proj"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return tmp, root


def found(files):
    tmp, root = tree(files)
    with tmp:
        return find(root)


def boxes(result):
    """{"file:name": box name} for every function and class, failing if one is in two boxes."""
    seen = {}
    places = [(c["name"], c["members"]) for c in result["components"]]
    places += [(s["name"], s["members"]) for s in result["shared"]]
    places += [(NO_PATH, result["no_path_found"])]
    for box, members in places:
        for m in members:
            assert m["id"] not in seen, m
            seen[m["id"]] = box
    return {k.rpartition(":")[0]: v for k, v in seen.items()}


class EntryPointTest(unittest.TestCase):
    def test_console_script_and_dunder_main_with_the_same_function_are_one_component(self):
        result = found(
            {
                "pyproject.toml": '[project]\nname = "app"\n[project.scripts]\napp = "app.cli:main"\nbad = "app.cli:nope"\n',
                "app/__init__.py": "",
                "app/__main__.py": "from app.cli import main\nmain()\n",
                "app/cli.py": "def main():\n    pass\n",
            }
        )
        (comp,) = result["components"]
        self.assertEqual(comp["name"], "app")
        self.assertEqual(comp["ways_in"], ["python -m app", "console script app = app.cli:main"])
        self.assertEqual(result["not_proven"], ["console script bad = app.cli:nope: not a function charted here"])

    def test_a_main_block_starts_a_component_and_one_reaching_nothing_is_listed(self):
        result = found(
            {
                "tool.py": "def main():\n    pass\n" + MAIN,
                "other.py": 'import unittest\nif "__main__" == __name__:\n    unittest.main()\n',
                "not.py": 'def f():\n    pass\nif __name__ != "__main__":\n    f()\n',
            }
        )
        self.assertEqual([(c["name"], c["ways_in"]) for c in result["components"]], [("tool", ["python -m tool"])])
        self.assertEqual(result["entries_reaching_nothing"], ["python -m other"])
        self.assertEqual(boxes(result)["not.py:f"], NO_PATH)

    def test_a_click_group_its_subgroups_and_the_main_block_calling_it_are_one_component(self):
        result = found(
            {
                "cli.py": "import click\nfrom more import extra\n\n@click.group()\ndef cli():\n    pass\n\n"
                "@cli.command()\ndef add():\n    pass\n\n@cli.group()\ndef sub():\n    pass\n\n"
                "@sub.command()\ndef deep():\n    pass\n\ncli.add_command(extra)\n\n"
                "@click.command()\ndef alone():\n    pass\n\n@thing.command()\ndef odd():\n    pass\n\n"
                "def main():\n    cli()\n" + MAIN,
                "more.py": "import click\n\n@click.command()\ndef extra():\n    pass\n",
            }
        )
        where = boxes(result)
        self.assertEqual(sorted(c["name"] for c in result["components"]), ["alone", "cli"])
        for name in ("cli", "add", "sub", "deep", "main"):
            self.assertEqual(where[f"cli.py:{name}"], "cli", name)
        self.assertEqual(where["more.py:extra"], "cli")  # added with add_command
        self.assertEqual(where["cli.py:odd"], NO_PATH)
        self.assertEqual(
            result["not_proven"], ["@thing.command on odd() in cli.py: thing is not proven to be a click group"]
        )

    def test_routes_on_an_app_and_the_routers_it_includes_are_one_component(self):
        result = found(
            {
                "web.py": "from fastapi import FastAPI\nfrom part import router\nimport part2\napp = FastAPI()\n"
                'app.include_router(router)\napp.include_router(part2.router)\n\n@app.get("/a")\ndef a():\n    pass\n',
                "part.py": 'from fastapi import APIRouter\nrouter = APIRouter()\n\n@router.post("/b")\ndef b():\n    pass\n',
                "part2.py": 'from fastapi import APIRouter\nrouter = APIRouter()\n\n@router.get("/c")\ndef c():\n    pass\n',
                "alone.py": 'from fastapi import APIRouter\nrouter = APIRouter()\n\n@router.get("/d")\ndef d():\n    pass\n',
                "more.py": 'from web import app as other\n\n@other.get("/e")\ndef e():\n    pass\n',
            }
        )
        comps = {c["name"]: c["ways_in"] for c in result["components"]}
        self.assertEqual(
            sorted(comps["web app app in web.py"]),
            ['GET "/a" on app in web.py: a()', 'GET "/c" on router in part2.py: c()', 'POST "/b" on router in part.py: b()'],
        )
        self.assertEqual(len(comps), 2)  # alone.py's router is included by nothing
        self.assertIn("other is not proven to be an app or router made in this file", result["not_proven"][0])

    def test_bare_click_decorators_are_entry_points_and_a_bare_one_on_an_unknown_name_is_not_proven(self):
        result = found(
            {
                "cli.py": "import click\n\n@click.group\ndef cli():\n    pass\n\n@cli.command\ndef add():\n    pass\n\n"
                "@thing.command\ndef odd():\n    pass\n",
            }
        )
        (comp,) = result["components"]
        self.assertEqual(sorted(comp["ways_in"]), ["click command add() under cli() in cli.py", "click group cli() in cli.py"])
        self.assertEqual(result["not_proven"], ["@thing.command on odd() in cli.py: thing is not proven to be a click group"])

    def test_flask_blueprint_hooks_and_a_router_named_through_its_module(self):
        result = found(
            {
                "web.py": "from flask import Flask\nimport views\napp = Flask(__name__)\n"
                "app.register_blueprint(views.bp)\n\n@app.before_request\ndef check():\n    pass\n\n"
                "@app.errorhandler(404)\ndef missing(e):\n    pass\n",
                "views.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__)\n\n@bp.route("/v")\ndef v():\n    pass\n',
            }
        )
        (comp,) = result["components"]
        self.assertEqual(
            sorted(comp["ways_in"]),
            [
                'ROUTE "/v" on bp in views.py: v()',
                "before_request on app in web.py: check()",
                "errorhandler on app in web.py: missing()",
            ],
        )

    def test_a_route_like_decorator_on_something_not_an_app_is_not_proven(self):
        result = found({"x.py": 'import cache\n\n@cache.get("/a")\ndef a():\n    pass\n'})
        self.assertEqual(result["components"], [])
        self.assertEqual(
            result["not_proven"], ["@cache.get on a() in x.py: cache is not proven to be an app or router made in this file"]
        )
        self.assertEqual(boxes(result)["x.py:a"], NO_PATH)

    def test_a_console_script_pointing_nowhere_is_not_proven_and_starts_nothing(self):
        result = found(
            {
                "pyproject.toml": '[project]\nname = "app"\n[project.scripts]\ngone = "app.missing:main"\n',
                "app/__init__.py": "",
                "app/cli.py": "def main():\n    pass\n",
            }
        )
        self.assertEqual(result["components"], [])
        self.assertEqual(result["not_proven"], ["console script gone = app.missing:main: not a function charted here"])


class MembershipTest(unittest.TestCase):
    FILES = {
        "one.py": "from lib import helper, solo\nfrom mine import own\ndef main():\n    run()\n"
        "def run():\n    helper()\n    solo()\n    own()\n" + MAIN,
        "two.py": "from lib import helper\nfrom one import run\ndef main():\n    helper()\n" + MAIN,
        "lib.py": "def helper() -> int:\n    pass\ndef solo(x: int):\n    pass\ndef dead():\n    pass\n",
        "mine.py": "def own(x):\n    pass\n",
    }

    def test_code_one_component_reaches_is_in_it_and_code_two_reach_is_a_shared_box_by_file(self):
        result = found(self.FILES)
        one = next(c for c in result["components"] if c["name"] == "one")
        why = {m["name"]: m["why"] for m in one["members"]}
        self.assertEqual(
            why,
            {
                "main()": "entry point",
                "run()": "reached from main()",
                "solo()": "reached from main() via run()",  # its file is shared, but only one reaches it
                "own()": "reached from main() via run()",
            },
        )
        (lib,) = result["shared"]
        self.assertEqual((lib["name"], lib["used_by"]), ("lib.py", ["one", "two"]))
        # helper() is reached by two components, so it stays shared.
        self.assertEqual({m["name"]: sorted(m["used_by"]) for m in lib["members"]}, {"helper()": ["one", "two"]})
        self.assertEqual([m["name"] for m in result["no_path_found"]], ["dead()"])

    def test_a_shared_box_is_named_by_its_file_and_each_member_lists_only_who_reaches_it(self):
        result = found(
            {
                "one.py": "from lib import both, only\ndef main():\n    both()\n    only()\n" + MAIN,
                "two.py": "from lib import both, also\ndef main():\n    both()\n    also()\n" + MAIN,
                "lib.py": "def both():\n    inner()\ndef inner():\n    pass\ndef only():\n    pass\ndef also():\n    pass\n",
            }
        )
        (lib,) = result["shared"]
        self.assertEqual(lib["name"], "lib.py")
        self.assertEqual({m["name"]: sorted(m["used_by"]) for m in lib["members"]}, {"both()": ["one", "two"], "inner()": ["one", "two"]})
        where = boxes(result)
        self.assertEqual((where["lib.py:only"], where["lib.py:also"]), ("one", "two"))

    def test_an_entry_function_stays_in_its_component_when_its_file_is_shared(self):
        result = found(
            {
                "a.py": "def main():\n    helper()\ndef helper():\n    pass\n" + MAIN,
                "b.py": "from a import helper\ndef main():\n    helper()\n" + MAIN,
            }
        )
        where = boxes(result)
        self.assertEqual((where["a.py:main"], where["b.py:main"]), ("a", "b"))
        self.assertEqual(where["a.py:helper"], "a.py")

    def test_arrows_run_between_boxes_and_give_only_what_the_callee_declares(self):
        result = found(self.FILES)
        arrows = {(a["from"], a["to"]): a["calls"] for a in result["arrows"]}
        self.assertEqual(arrows[("one", "lib.py")], [{"from": "run()", "to": "helper()", "callee_declares": "-> int"}])
        self.assertEqual(
            arrows[("two", "lib.py")], [{"from": "main()", "to": "helper()", "callee_declares": "-> int"}]
        )
        self.assertEqual(len(arrows), 2)  # run() -> solo() and run() -> own() stay inside one
        text = summary(result)
        self.assertIn("run() -> helper() [callee declares -> int]", text)
        self.assertNotIn("data", text)

    def test_a_call_in_module_level_code_counts_once_its_file_is_reached(self):
        result = found(
            {
                "tool.py": "from lib import use\ndef main():\n    use()\n" + MAIN,
                "lib.py": "def _dotted(x):\n    return x\nTABLE = _dotted(1)\ndef use():\n    pass\n",
                "lone.py": "def made():\n    pass\nTHING = made()\n",
            }
        )
        (tool,) = result["components"]
        why = {m["name"]: m["why"] for m in tool["members"]}
        self.assertEqual(why["_dotted()"], "reached from main() via use(), loading lib.py")
        # lone.py is loaded by nothing found, so made() has no path, though a call to it exists.
        self.assertEqual([m["name"] for m in result["no_path_found"]], ["made()"])

    def test_code_with_no_found_path_is_never_called_unreached_or_unused(self):
        result = found(
            {
                "tool.py": "def _open(u):\n    pass\nclass Opener:\n    opener = None\n"
                "    def go(self):\n        return (self.opener or _open)(1)\n"
                "def main():\n    Opener().go()\n" + MAIN,
            }
        )
        self.assertEqual([m["name"] for m in result["no_path_found"]], ["_open()"])
        text = summary(result)
        self.assertIn(f"{NO_PATH}: 1\n  tool.py: _open()", text)
        self.assertNotIn("unreached", text)
        self.assertNotIn("unused", text)

    def test_every_box_lands_in_exactly_one_box_on_random_call_graphs(self):
        rand = random.Random(9089)
        for _ in range(40):
            n = rand.randrange(1, 8)
            files = {}
            for i in range(n):
                calls = [f"    m{j}.f{j}()" for j in rand.sample(range(n), rand.randrange(n + 1)) if j != i]
                imports = "".join(f"import m{j}\n" for j in range(n) if j != i)
                main_block = MAIN.replace("main()", f"f{i}()") if rand.random() < 0.4 else ""
                files[f"m{i}.py"] = imports + f"def f{i}():\n" + ("\n".join(calls) or "    pass") + "\n" + main_block
            files["k.py"] = "class K:\n    pass\n"
            result = found(files)
            self.assertEqual(len(boxes(result)), n + 1)
            self.assertEqual(boxes(result)["k.py:K"], NO_PATH)


class MainTest(unittest.TestCase):
    def test_writes_json_and_prints_the_summary(self):
        tmp, root = tree(MembershipTest.FILES)
        with tmp:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([str(root), str(Path(tmp.name) / "c.json")])
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(len(json.loads((Path(tmp.name) / "c.json").read_text())["components"]), 2)
            self.assertTrue(out.getvalue().startswith(f"2 components, 1 shared files, {NO_PATH} 1\n"))
            with contextlib.redirect_stderr(err):
                self.assertEqual(main([str(root), str(Path(tmp.name) / "c.py")]), 2)
            self.assertFalse((Path(tmp.name) / "c.py").exists())

    def test_a_missing_path_exits_2_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main([str(Path(tmp) / "nope"), str(Path(tmp) / "c.json")])
            self.assertEqual(code, 2)
            self.assertIn("nope: no such file or folder", err.getvalue())
            self.assertFalse((Path(tmp) / "c.json").exists())

    def test_a_file_that_does_not_parse_is_named_not_dropped(self):
        result = found({"bad.py": "def f(:\n", "good.py": "def main():\n    pass\n" + MAIN})
        self.assertEqual([c["name"] for c in result["components"]], ["good"])
        (note,) = result["files_not_read"]
        self.assertTrue(note.startswith("bad.py: does not parse: line 1"), note)
        self.assertIn("files not read: 1\n  bad.py: does not parse", summary(result))


PKG = {"scripts/__init__.py": "", "scripts/commands/__init__.py": "", "scripts/commands/common.py": "def f():\n    pass\n"}


class SysPathTest(unittest.TestCase):
    """A folder the program puts on sys.path is an import root, when the parser proves it."""

    def run_file(self, insert, imports="from commands.common import f"):
        head = f"import os\nimport sys\nfrom pathlib import Path\n{insert}\n{imports}\n\n\ndef main():\n    f()\n\n\n"
        return found({**PKG, "scripts/run.py": head + MAIN})

    def test_a_path_from_file_is_followed(self):
        result = self.run_file("sys.path.insert(0, str(Path(__file__).resolve().parent))")
        self.assertEqual(boxes(result)["scripts/commands/common.py:f"], "run")
        self.assertEqual(result["not_proven"], [])

    def test_a_dirname_chain_is_followed(self):
        result = self.run_file("sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scripts'))")
        self.assertEqual(boxes(result)["scripts/commands/common.py:f"], "run")

    def test_a_path_computed_when_it_runs_is_not_followed(self):
        result = self.run_file('sys.path.insert(0, os.environ["ROOT"])')
        self.assertEqual(boxes(result)["scripts/commands/common.py:f"], NO_PATH)
        self.assertEqual(result["not_proven"], ['scripts/run.py:4: sys.path.insert(0, os.environ["ROOT"]): the folder is known only when it runs'])

    def test_a_module_two_roots_give_to_different_files_is_not_guessed(self):
        result = found(
            {
                **PKG,
                "commands/__init__.py": "",
                "commands/common.py": "def f():\n    pass\n",
                "scripts/run.py": "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n"
                "from commands.common import f\n\n\ndef main():\n    f()\n\n\n" + MAIN,
            }
        )
        self.assertEqual(boxes(result)["scripts/commands/common.py:f"], NO_PATH)
        self.assertEqual(boxes(result)["commands/common.py:f"], NO_PATH)

    def test_parents_n_is_followed(self):
        result = self.run_file("sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))")
        self.assertEqual(boxes(result)["scripts/commands/common.py:f"], "run")

    def test_a_relative_string_is_not_followed(self):
        result = self.run_file('sys.path.insert(0, ".")')
        self.assertEqual(boxes(result)["scripts/commands/common.py:f"], NO_PATH)
        self.assertEqual(result["not_proven"], ['scripts/run.py:4: sys.path.insert(0, "."): the folder is known only when it runs'])

    def test_a_root_reaches_the_files_its_imports_load_transitively(self):
        files = {
            **PKG,
            "scripts/commands/tasks.py": "from commands.common import f\n\n\ndef t():\n    f()\n",
            "scripts/run.py": "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n"
            "from commands.tasks import t\n\n\ndef main():\n    t()\n\n\n" + MAIN,
        }
        self.assertEqual(boxes(found(files))["scripts/commands/common.py:f"], "run")

    def test_a_root_is_not_used_by_a_file_the_inserting_file_does_not_load(self):
        result = found(
            {
                "main.py": 'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent / "lib"))\n',
                "lib/helper.py": "def run():\n    pass\n",
                "svc/other.py": "import helper\n\n\ndef serve():\n    helper.run()\n\n\n" + MAIN.replace("main()", "serve()"),
            }
        )
        self.assertEqual(boxes(result)["lib/helper.py:run"], NO_PATH)
        self.assertEqual(result["arrows"], [])

    def test_other_sys_path_changes_are_listed_as_not_proven(self):
        body = "sys.path[:0] = ['lib']\nsys.path.extend(['lib'])\nsys.path += ['lib']\nsys.path = []\ndel sys.path[0]\n"
        body += "if 'lib' in sys.path:\n    print(sys.path.index('lib'), sys.path[0])\n"
        result = found({"run.py": "import sys\n" + body})
        why = "asBuilt follows only sys.path.insert and append"
        stmts = ["sys.path[:0] = ['lib']", "sys.path.extend(['lib'])", "sys.path += ['lib']", "sys.path = []", "del sys.path[0]"]
        self.assertEqual(result["not_proven"], [f"run.py:{i}: {s}: {why}" for i, s in enumerate(stmts, 2)])

    def test_a_file_imported_above_the_insert_does_not_use_the_root(self):
        result = found(
            {
                "m.py": 'import sys\nfrom pathlib import Path\nimport early\nsys.path.insert(0, str(Path(__file__).parent / "lib"))\n',
                "early.py": "import helper\n\n\ndef e():\n    helper.go()\n\n\n" + MAIN.replace("main()", "e()"),
                "lib/helper.py": "def go():\n    pass\n",
            }
        )
        self.assertEqual(boxes(result)["lib/helper.py:go"], NO_PATH)
        self.assertEqual(result["arrows"], [])

    def test_an_insert_that_may_not_run_is_not_followed(self):
        for insert, why in (
            ("def setup():\n    sys.path.insert(0, str(Path(__file__).parent))", "it runs only when its function is called"),
            ("if __name__ == '__main__':\n    sys.path.insert(0, str(Path(__file__).parent))", "it runs only under an if, try, loop or with"),
        ):
            result = self.run_file(insert)
            self.assertEqual(boxes(result)["scripts/commands/common.py:f"], NO_PATH)
            self.assertEqual(result["not_proven"], [f"scripts/run.py:5: sys.path.insert(0, str(Path(__file__).parent)): {why}"])

    def test_a_folder_outside_the_charted_one_is_listed(self):
        result = self.run_file("sys.path.insert(0, str(Path(__file__).parent.parent.parent))")
        line = "scripts/run.py:4: sys.path.insert(0, str(Path(__file__).parent.parent.parent))"
        self.assertEqual(result["not_proven"], [f"{line}: the folder is outside the charted folder"])

    def test_naming_a_method_is_not_a_change_and_another_name_is_not_proven(self):
        result = found({"run.py": "import sys\nprint(sys.path.append)\np = sys.path\n"})
        self.assertEqual(result["not_proven"], ["run.py:3: p = sys.path: asBuilt does not follow sys.path under another name"])

    def test_a_module_only_the_extra_root_names_is_followed(self):
        run = "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent / 'lib'))\n"
        run += "from helpers import f\n\n\ndef main():\n    f()\n\n\n" + MAIN
        result = found({"lib/helpers.py": "def f():\n    pass\n", "run.py": run})
        self.assertEqual(boxes(result)["lib/helpers.py:f"], "run")
        self.assertEqual(result["components"][0]["ways_in"], ["python -m run"])

    def test_the_same_file_under_both_roots_is_one_box(self):
        result = self.run_file("sys.path.insert(0, str(Path(__file__).parent.parent))", "from scripts.commands.common import f")
        self.assertEqual(boxes(result)["scripts/commands/common.py:f"], "run")


if __name__ == "__main__":
    unittest.main()
