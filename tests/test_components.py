import contextlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from asbuilt.components import find, main

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
    places += [("unreached", result["unreached"])]
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
        self.assertEqual(boxes(result)["not.py:f"], "unreached")

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
        self.assertEqual(where["cli.py:odd"], "unreached")
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


class MembershipTest(unittest.TestCase):
    FILES = {
        "one.py": "from lib import helper, solo\nfrom mine import own\ndef main():\n    run()\n"
        "def run():\n    helper()\n    solo()\n    own()\n" + MAIN,
        "two.py": "from lib import helper\nfrom one import run\ndef main():\n    helper()\n" + MAIN,
        "lib.py": "def helper() -> int:\n    pass\ndef solo(x: int):\n    pass\ndef dead():\n    pass\n",
        "mine.py": "def own(x):\n    pass\n",
    }

    def test_a_file_one_component_reaches_is_in_it_and_one_two_reach_is_a_shared_box(self):
        result = found(self.FILES)
        one = next(c for c in result["components"] if c["name"] == "one")
        why = {m["name"]: m["why"] for m in one["members"]}
        self.assertEqual(why, {"main()": "entry point", "run()": "reached from main()", "own()": "reached from main() via run()"})
        (lib,) = result["shared"]
        self.assertEqual((lib["name"], lib["used_by"]), ("lib.py, used by one, two", ["one", "two"]))
        # solo() is reached by one alone but lives in a shared file, so it is in the file's box.
        self.assertEqual({m["name"]: sorted(m["used_by"]) for m in lib["members"]}, {"helper()": ["one", "two"], "solo()": ["one"]})
        self.assertEqual([m["name"] for m in result["unreached"]], ["dead()"])

    def test_an_entry_function_stays_in_its_component_when_its_file_is_shared(self):
        result = found(
            {
                "a.py": "def main():\n    helper()\ndef helper():\n    pass\n" + MAIN,
                "b.py": "from a import helper\ndef main():\n    helper()\n" + MAIN,
            }
        )
        where = boxes(result)
        self.assertEqual((where["a.py:main"], where["b.py:main"]), ("a", "b"))
        self.assertEqual(where["a.py:helper"], "a.py, used by a, b")

    def test_arrows_run_between_boxes_and_name_data_only_from_annotations(self):
        result = found(self.FILES)
        arrows = {(a["from"], a["to"]): a["calls"] for a in result["arrows"]}
        self.assertEqual(
            arrows[("one", "lib.py, used by one, two")],
            [{"from": "run()", "to": "helper()", "data": "-> int"}, {"from": "run()", "to": "solo()", "data": "x: int"}],
        )
        self.assertEqual(arrows[("two", "lib.py, used by one, two")], [{"from": "main()", "to": "helper()", "data": "-> int"}])
        self.assertEqual(len(arrows), 2)  # run() -> own() stays inside one

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
            self.assertEqual(boxes(result)["k.py:K"], "unreached")


class MainTest(unittest.TestCase):
    def test_writes_json_and_prints_the_summary(self):
        tmp, root = tree(MembershipTest.FILES)
        with tmp:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([str(root), str(Path(tmp.name) / "c.json")])
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(len(json.loads((Path(tmp.name) / "c.json").read_text())["components"]), 2)
            self.assertTrue(out.getvalue().startswith("2 components, 1 shared files, unreached 1\n"))
            with contextlib.redirect_stderr(err):
                self.assertEqual(main([str(root), str(Path(tmp.name) / "c.py")]), 2)
            self.assertFalse((Path(tmp.name) / "c.py").exists())


if __name__ == "__main__":
    unittest.main()
