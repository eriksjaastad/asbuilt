import copy
import unittest

from asbuilt.compare import compare


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
