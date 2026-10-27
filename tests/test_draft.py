import json
import random
import sys
import unittest

from asbuilt.draft import PAGE, page_takes
from asbuilt.state import PlanError
from test_compare import made, page_says

BAD = [0, -1, "1", True, None, [], {}]  # sizes the page refuses
STACK, EMPTY = "Maximum call stack size exceeded", "Unexpected end of JSON input"
BROKEN = "Unexpected token 'w', \"{" + chr(10) + '"id":wat}" is not valid JSON'
SIZES = [{k: v} for k in ("width", "height") for v in BAD]


def sized(box, rand):  # now and then a width or height, often one the page refuses
    if isinstance(box, dict) and rand.random() < 0.15:
        box[rand.choice(["width", "height"])] = rand.choice([*BAD, 1, 2.5, 1e308])
    [sized(kid, rand) for kid in isinstance(box, dict) and box.get("children") or []]
    return box


def py(code):  # stands in for node
    return (sys.executable, "-c", f"import json, sys\n{code}")


class PageTakesTest(unittest.TestCase):
    def test_it_says_what_the_page_says_about_named_and_generated_plans(self):
        plans = [{"id": "r", "children": [{"id": "a", **s}]} for s in SIZES]
        plans += [sized(made(r := random.Random(n), []), r) for n in range(200)]
        plans.append({"id": "r", "children": [{"id": "é" * 40000}] * 2})  # split reads
        for plan, says in zip(plans, page_says(plans)):
            with self.subTest(plan=plan):
                try:
                    got = page_takes(json.dumps(plan, ensure_ascii=False).encode(), "R")
                except PlanError as e:
                    got = str(e)
                self.assertEqual(got, says and f"R: {says}")
        deep = b'{"id": "r", "children": [' + b"[" * 99999 + b"]" * 99999 + b"]}"
        for out, says in [(deep, STACK), (b"", EMPTY), (b'{\n"id":wat}', BROKEN)]:
            with self.subTest(says), self.assertRaises(PlanError) as caught:
                page_takes(out, "R")
            self.assertEqual(str(caught.exception), f"R: {says}")

    def test_the_plan_goes_on_stdin_and_the_script_in_argv(self):
        said = "[sys.argv[1:], sys.stdin.buffer.read().decode()]"
        with self.assertRaises(PlanError) as caught:
            page_takes("plan é".encode(), "R", py(f"print(json.dumps(str({said})))"))
        args = [["--input-type=module", "-e", PAGE], "plan é"]
        self.assertEqual(str(caught.exception), f"R: {args}")

    def test_node_that_cannot_check_is_one_line(self):
        c = "b" * 200  # the last stderr line is cut to 200
        for node, why in [
            (("/no/such/node",), "was not found: install Node.js"),
            (("/dev/null",), "cannot be started: Permission denied"),
            (py("import time; time.sleep(5)"), "gave no answer in 0.5 s"),
            (py(f"print('null'); sys.exit('a\\nb\\r{c}b')"), f"stopped, exit 1: {c}"),
            (py("print('{')"), "stopped, exit 0: no message"),
            (py("print(5)"), "stopped, exit 0: no message"),
        ]:
            with self.subTest(why), self.assertRaises(PlanError) as caught:
                page_takes(b"{}", "R", node, 0.5)
            self.assertEqual(str(caught.exception), f"R cannot be checked: node {why}.")

    def test_page_message_keeps_its_line_breaks(self):
        # fail() will join later; page_takes must not strip them itself
        with self.assertRaises(PlanError) as caught:
            page_takes(b"{}", "R", py("print(json.dumps('a'+chr(10)+'b'+chr(13)+'c'))"))
        self.assertEqual(str(caught.exception), "R: a" + chr(10) + "b" + chr(13) + "c")
