import contextlib
import copy
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from asbuilt.compare import check, compare, main
from asbuilt.state import PlanError


def box(bid, *kids, labels=None):
    out = {"id": bid, "children": list(kids)} if kids else {"id": bid}
    return out if labels is None else {**out, "labels": [{"text": s} for s in labels]}


def tree(plan):
    return " ".join(
        f"{kid['id'] or '_'}:{kid['asbuilt']['mark']}{kid['asbuilt']['why']}"
        + (f"[{tree(kid)}]" if kid.get("children") else "")
        for kid in plan.get("children", [])
    )


PAGE = """import { prepare } from "./src/asbuilt/page/prepare.js"; let text = "";
for await (const part of process.stdin) text += part;
const says = (plan) => { try { prepare(plan); return null; } catch (e) { return e.message; } };
console.log(JSON.stringify(JSON.parse(text).map(says)));"""
LABELS = [[{"text": ""}, {"text": "A"}]] * 9 + [[], None, ["A"], [{}], "A"]


def page_says(plans):
    """prepare.js's refusal for each plan, or None where the page takes it."""
    run, root = ["node", "--input-type=module", "-e", PAGE], Path(__file__).resolve()
    out = subprocess.check_output(
        run, input=json.dumps(plans), text=True, cwd=root.parents[1], timeout=60
    )
    return json.loads(out)


def made(rand, ids, depth=0):
    """A seeded random box, now and then malformed in one of the ways prepare.js refuses."""
    if rand.random() < 0.02:
        return rand.choice([1, "a", None, []])
    bid = rand.choice(ids) if ids and rand.random() < 0.02 else f"b{len(ids)}é"
    ids.append(bid)
    out = {"id": rand.choice([bid] * 60 + [7, None])}
    if rand.random() < 0.3:
        out["labels"] = rand.choice(LABELS)
    if depth and rand.random() < 0.3:  # an arrow to a box made before, or a bad end
        aid = rand.choice([f"e{len(ids)}"] * 30 + [bid, 3])
        s, t = [
            rand.choice([[rand.choice(ids)]] * 20 + [[], "a", [bid] * 2, [3], [aid]])
            for _ in "st"
        ]
        arrow = dict(id=aid, sources=s, targets=t, labels=rand.choice(LABELS))
        out["edges"] = rand.choice([[arrow]] * 30 + [[arrow, "e"], {}])
    if rand.random() < 0.95 - depth * 0.3:
        kids = [made(rand, ids, depth + 1) for _ in range(rand.randint(not depth, 3))]
        out["children"] = rand.choice([kids] * 40 + [[], {}, None])
    return out


def items(plan):
    """{id: mark} for each box under the root and each arrow, wherever it sits."""
    found, todo = {}, [plan]
    for at in todo:
        todo += at.get("children", [])
        for item in at.get("children", []) + at.get("edges", []):
            found[item["id"]] = item.get("asbuilt", {}).get("mark")
    return found


class CompareTest(unittest.TestCase):
    def test_each_mark_and_where_built_and_dropped_boxes_sit(self):
        x = box("x", box("y"))
        x["edges"] = [{"id": "e", "sources": ["y"], "targets": ["y"]}]
        a, b = box("a", x), box("b", labels=["B"])
        planned = box("r", a, b, box("", box("m")))
        built = box("r", box("a", box("m")), box("b", labels=["B2"]), box("c"))
        built["k"] = 1  # other keys stay
        before = copy.deepcopy((planned, built))
        out = compare(planned, built)
        self.assertEqual((planned, built), before)
        want = "a:planned[m:changed x:dropped[y:dropped]] b:changed c:added _:dropped"
        self.assertEqual(tree(out), want)
        self.assertEqual(
            (out["children"][1]["labels"], out["k"]), ([{"text": "B2"}], 1)
        )
        self.assertNotIn("edges", out["children"][0]["children"][1])

    def test_a_planned_id_that_built_uses_for_its_root_or_an_arrow(self):
        planned, built = box("p", box("b", box("x")), box("a")), box("b", box("a"))
        with self.assertRaisesRegex(
            ValueError, 'box "b" was planned; built has it as its top'
        ):
            compare(planned, built)
        planned = box(
            "b", box("x"), box("a")
        )  # the same top in both is the normal case
        self.assertEqual(tree(compare(planned, built)), "a:planned x:dropped")
        for at in built, built["children"][0]:  # an arrow at the top, then in a box
            at["edges"] = [{"id": "x", "sources": ["a"], "targets": ["a"]}]
            with self.assertRaisesRegex(ValueError, 'box "x" was planned; built has'):
                compare(planned, built)
            del at["edges"]

    def test_each_arrow_mark_by_its_ends_and_first_label_and_dropped_ones_joined(self):
        def arrow(aid, source, target, *texts):
            labels = [{"text": x} for x in texts]
            return dict(id=aid, sources=[source], targets=[target], labels=labels)

        planned = box("r", box("a"), box("b"), box("c"))
        built = box("r", box("a"), box("b", box("d")))
        names = "same moved to from words gone new".split()
        planned["edges"] = [arrow(n, "a", "b", "x", "y") for n in names[:5]]
        planned["edges"] += [arrow("gone", "a", "c")]
        built["children"][1]["edges"] = [
            arrow("same", "a", "b", "x"),
            arrow("moved", "a", "b", "x", ""),
        ]
        built["edges"] = [arrow("to", "a", "d", "x"), arrow("from", "b", "b", "x")]
        built["edges"] += [arrow("words", "a", "b", "z"), arrow("new", "d", "a")]
        before = copy.deepcopy((planned, built))
        out = compare(planned, built)
        self.assertEqual((planned, built), before)
        marks = ["planned"] * 2 + ["changed"] * 3 + ["dropped", "added"]
        self.assertEqual(
            {k: v for k, v in items(out).items() if k in names}, dict(zip(names, marks))
        )
        self.assertEqual(
            out["edges"][-1]["sources"] + out["edges"][-1]["targets"], ["a", "c"]
        )
        self.assertEqual(page_says([out]), [None])

    def test_a_planned_arrow_whose_id_or_end_built_uses_for_a_box_or_its_top(self):
        planned = box("p", box("a"), box("b"))
        for aid, s, t, top in [
            "caat",
            "taat",
        ]:  # built uses its id for a box, then its top
            planned["edges"] = [{"id": aid, "sources": [s], "targets": [t]}]
            with self.assertRaisesRegex(ValueError, f'arrow "{aid}" was planned'):
                compare(planned, box(top, box("c"), box("a")))
        for s, t in [
            "ab",
            "ba",
        ]:  # an end on built's top is a planned box built has as its top
            planned["edges"] = [{"id": "e", "sources": [s], "targets": [t]}]
            with self.assertRaisesRegex(
                ValueError, 'box "b" was planned; built has it as its top'
            ):
                compare(planned, box("b", box("c"), box("a")))

    def test_generated_pairs_give_a_plan_the_page_takes_holding_both(self):
        plans = [made(random.Random(n), []) for n in range(400)]
        good = [plan for plan, says in zip(plans, page_says(plans)) if not says]
        outs = [compare(planned, built) for planned, built in zip(good, good[1:])]
        self.assertEqual(page_says(outs), [None] * len(outs))
        for planned, built, out in zip(good, good[1:], outs):
            old, new, got = items(planned), items(built), items(out)
            self.assertEqual(got.keys(), (old.keys() | new.keys()) - {built["id"]})
            for key, mark in got.items():
                both = key in old and key in new
                ok = (
                    ["planned", "changed"]
                    if both
                    else ["added" if key in new else "dropped"]
                )
                self.assertIn(mark, ok, key)

    def test_only_what_the_page_draws_counts_as_a_change(self):
        same = [None, [], ["a"], ["a", ""], ["a", "", "x"]]
        for labels in same + [[""], ["b"], ["a", "n"], ["b", ""]]:
            want = "a:planned" if labels in same else "a:changed"
            for p, b in [(None, labels), (labels, None)]:
                out = compare(
                    box("r", box("a", labels=p)), box("r", box("a", labels=b))
                )
                self.assertEqual(tree(out), want, (p, b))

    def test_check_says_what_the_page_says_about_generated_plans(self):
        # Seeded, so the same plans every run.
        plans = [made(random.Random(n), []) for n in range(400)]
        page = page_says(plans)
        for plan, says in zip(plans, page):
            try:
                check(plan, "p.json")
                said = None
            except PlanError as error:
                said = str(error)
            self.assertEqual(said, says and f"p.json: {says}", plan)
        kinds = {
            says and re.sub('"[^"]*"', "", says.split(": ", 1)[1]) for says in page
        }
        self.assertEqual(len(kinds), 15, kinds)  # each of the 14 refusals, and None


class CommandTest(unittest.TestCase):
    def run_main(self, planned, built, out="out.json"):
        """(exit code, stderr, files after) for the plans saved as planned.json, built.json."""
        self.dir, saved = Path(self.enterContext(tempfile.TemporaryDirectory())), {}
        for name, plan in [("planned.json", planned), ("built.json", built)]:
            if plan is not None:
                saved[name] = plan if isinstance(plan, str) else json.dumps(plan)
                (self.dir / name).write_text(saved[name], encoding="utf-8")
        args = [str(self.dir / n) for n in ("planned.json", "built.json", out)]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = main(args)
        files = {p.name: p.read_text(encoding="utf-8") for p in self.dir.iterdir()}
        self.assertEqual({k: files.get(k) for k in saved}, saved)  # inputs only read
        return code, err.getvalue(), files

    def test_out_is_the_compare_of_the_two_files_and_the_page_takes_it(self):
        planned = box("r", box("a", labels=["A"]), box("b"))
        built = box("r", box("a", labels=["A2"]), box("c", labels=["é"]))
        code, err, files = self.run_main(planned, built)
        self.assertEqual((code, err, len(files)), (0, "", 3))
        out = json.loads(files["out.json"])
        self.assertEqual(out, compare(planned, built))
        self.assertEqual(page_says([out]), [None])

    def test_each_refusal_names_the_file_exits_2_and_writes_nothing(self):
        good, arrow = box("r", box("a")), box("r", box("b"))
        arrow["edges"] = [{"id": "a", "sources": ["b"], "targets": ["b"]}]
        for planned, built, out, says in [
            (None, good, "out.json", "planned.json was not found"),
            (good, "{", "out.json", "built.json is not valid JSON"),
            (good, box("r", box("a"), box("a")), "out.json", 'built.json: box "a" at'),
            (good, arrow, "out.json", 'built.json: box "a" was planned; built has an'),
            (good, good, "planned.json", "planned.json: OUT is PLANNED; inputs are"),
            (good, good, "built.json", "built.json: OUT is BUILT; inputs are only"),
            (good, good, "no/out.json", "out.json cannot be written: No such file"),
        ]:
            code, err, files = self.run_main(planned, built, out)
            self.assertEqual((code, err.count("\n")), (2, 1), err)
            self.assertTrue(err.startswith(f"asbuilt.compare: {says}"), err)
            self.assertNotIn("out.json", files)

    def test_a_samefile_permission_error_is_a_named_refusal(self):
        good = box("r", box("a"))
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        planned = self.dir / "planned.json"
        built = self.dir / "built.json"
        out = self.dir / "out.json"
        for path, plan in ((planned, good), (built, good)):
            path.write_text(json.dumps(plan), encoding="utf-8")
        out.write_text("{}", encoding="utf-8")
        with mock.patch.object(
            Path, "samefile", side_effect=PermissionError(13, "Permission denied")
        ):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = main([str(planned), str(built), str(out)])
        self.assertEqual((code, err.getvalue().count("\n")), (2, 1), err.getvalue())
        self.assertTrue(
            err.getvalue().startswith(
                "asbuilt.compare: planned.json cannot be read: Permission denied"
            ),
            err.getvalue(),
        )

    def test_an_existing_out_stays_byte_identical_when_compare_refuses(self):
        good = box("r", box("a"))
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        planned = self.dir / "planned.json"
        built = self.dir / "built.json"
        out = self.dir / "out.json"
        planned.write_text(json.dumps(good), encoding="utf-8")
        built.write_text("{", encoding="utf-8")  # not JSON: refuse after OUT exists
        old = '{"id": "keep-me"}\n'
        out.write_text(old, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = main([str(planned), str(built), str(out)])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue().count("\n"), 1)
        self.assertTrue(
            err.getvalue().startswith("asbuilt.compare: built.json is not valid JSON")
        )
        self.assertEqual(out.read_text(encoding="utf-8"), old)

    def test_bad_sizes_nonfinite_numbers_and_surrogates_are_refused_as_classes(self):
        good = box("r", box("a"))
        json_safe = (0, -1, True, "1", None)  # page_says round-trips these through JSON
        for key in ("width", "height"):
            for bad in (*json_safe, float("nan"), float("inf"), float("-inf")):
                built = box("r", box("a"))
                built["children"][0][key] = bad
                code, err, files = self.run_main(good, built)
                self.assertEqual(
                    (code, err.count("\n"), "out.json" in files), (2, 1, False)
                )
                want = f'asbuilt.compare: built.json: box "a" at plan.children[0]: {key} is not'
                self.assertTrue(err.startswith(want), (key, bad, err))
                if bad in json_safe:
                    self.assertEqual(
                        page_says([built])[0].split(": ", 1)[1],
                        f"{key} is not a number above 0",
                    )
        for sizes in ({}, {"width": 10}, {"height": 20.5}, {"width": 10, "height": 20}):
            built = box("r", box("a"))
            built["children"][0].update(sizes)
            code, err, files = self.run_main(good, built)
            self.assertEqual(
                (code, err, page_says([json.loads(files["out.json"])])), (0, "", [None])
            )
        for bad in (float("nan"), float("inf"), float("-inf")):
            planned = box("r", box("a"))
            planned["meta"] = bad
            code, err, files = self.run_main(planned, good)
            self.assertEqual((code, "out.json" in files), (2, False))
            self.assertTrue(
                err.startswith(
                    "asbuilt.compare: planned.json: plan.meta: is not a finite"
                ),
                err,
            )
            built = box("r", box("a"))
            built["children"][0]["meta"] = bad
            code, err, files = self.run_main(good, built)
            self.assertTrue(
                err.startswith("asbuilt.compare: built.json: plan.children[0].meta:"),
                err,
            )
        surr = "\ud800"
        for where, make in (
            ("plan.children[0].id", lambda: box("r", box(surr))),
            (
                "plan.children[0].labels[0].text",
                lambda: box("r", box("a", labels=[surr])),
            ),
        ):
            built = make()
            code, err, files = self.run_main(good, built)
            self.assertEqual((code, "out.json" in files), (2, False), err)
            self.assertTrue(
                err.startswith(
                    f"asbuilt.compare: built.json: {where}: holds text that is not Unicode"
                ),
                err,
            )

    def test_python_m_runs_it(self):
        src = str(Path(__file__).resolve().parents[1] / "src")
        env = dict(os.environ, PYTHONPATH=src)
        run = [sys.executable, "-m", "asbuilt.compare", "p.json", "b.json", "o.json"]
        at = self.enterContext(tempfile.TemporaryDirectory())
        done = subprocess.run(
            run, cwd=at, env=env, capture_output=True, text=True, timeout=60
        )
        self.assertEqual((done.returncode, done.stdout), (2, ""))
        self.assertRegex(done.stderr, r"\Aasbuilt.compare: p.json was not found.*\n\Z")
