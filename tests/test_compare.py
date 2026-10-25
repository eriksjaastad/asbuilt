import copy
import unittest

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


class CompareTest(unittest.TestCase):
    def test_each_mark_and_where_built_and_dropped_boxes_sit(self):
        x = box("x", box("y"))
        x["edges"] = [{"id": "e", "sources": ["y"], "targets": ["y"]}]  # arrows: later
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

    def test_only_what_the_page_draws_counts_as_a_change(self):
        same = [None, [], ["a"], ["a", ""], ["a", "", "x"]]
        for labels in same + [[""], ["b"], ["a", "n"], ["b", ""]]:
            want = "a:planned" if labels in same else "a:changed"
            for p, b in [(None, labels), (labels, None)]:
                out = compare(
                    box("r", box("a", labels=p)), box("r", box("a", labels=b))
                )
                self.assertEqual(tree(out), want, (p, b))

    def test_check_refuses_what_the_page_refuses_naming_the_file_and_box(self):
        arrow = {
            "id": "e",
            "sources": ["a"],
            "targets": ["a"],
            "labels": [{"text": "x"}],
        }
        check({**box("r", box("a", labels=["A", "n"])), "edges": [arrow]}, "p.json")
        a, top = {"id": "a"}, box("r", box("a"))
        for why, plan in [
            ("plan is not an object", []),
            ("plan has no boxes", box("r")),
            ('box "r".children[0] is not an object', box("r", 1)),
            ('box "r" has children or edges that are not', {"id": "r", "children": {}}),
            ('box "r" has children or edges that are not', {**top, "edges": None}),
            ("plan or one of its arrows has a non-text id", {"id": 1, "children": []}),
            ('box "r" or one of its arrows has a non-text', {**top, "edges": ["e"]}),
            ('box "r" or one of its arrows has a non-text', {**top, "edges": [{}]}),
            ('box "a": id "a" is used twice', box("r", a, box("b", a))),
            ('box "a": id "a" is used twice', {**top, "edges": [a]}),
            ('box "a": "a" has labels unlike', box("r", {**a, "labels": None})),
            ('box "a": "a" has labels unlike', box("r", {**a, "labels": ["A"]})),
            ('box "r": "r" has labels unlike', box("r", a, labels=[None])),
            (
                'box "r": "e" has labels unlike',
                {**top, "edges": [{**a, "id": "e", "labels": [{}]}]},
            ),
        ]:
            with self.subTest(why), self.assertRaises(PlanError) as caught:
                check(plan, "p.json")
            self.assertTrue(str(caught.exception).startswith(f"p.json: {why}"), why)
