import copy
import json
import random
import re
import subprocess
import unittest
from pathlib import Path

from asbuilt.compare import check, compare
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
    if depth and rand.random() < 0.3:  # an arrow from the box to itself
        aid, ends = rand.choice([f"e{len(ids)}"] * 30 + [bid, 3]), [bid]
        arrow = dict(id=aid, sources=ends, targets=ends, labels=rand.choice(LABELS))
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
        out = compare(planned, built)
        self.assertEqual((out["id"], tree(out)), ("b", "a:planned x:dropped"))
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
        for aid, s, t, top in ["caat", "taat", "ebab", "eabb"]:  # each end on the top
            planned["edges"] = [{"id": aid, "sources": [s], "targets": [t]}]
            with self.assertRaisesRegex(ValueError, f'arrow "{aid}" was planned'):
                compare(planned, box(top, box("c"), box("a")))

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
        self.assertEqual(len(kinds), 10, kinds)  # each of the 9 refusals, and None
