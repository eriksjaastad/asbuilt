import assert from "node:assert/strict";
import test from "node:test";
import { find, html } from "../src/asbuilt/page/code.js";

const sentence = "This function calls one other function and returns whatever that returns.";

test("the panel is that function's lines and the proven sentence, and a box with no lines is empty", () => {
  const plan = { id: "r", children: [{ id: "t.py", children: [{ id: "one", labels: [{ text: "one()" }], asbuilt: {
    text: "def one():\n    return other()\n", proven: sentence, rest: "the rest is in the file, line 9.",
  } }] }] };
  const out = html(find(plan, "one"));
  assert.ok(out.includes("def one():") && out.includes("return other()") && out.includes(sentence));
  assert.ok(out.includes("line 9"));
  assert.equal(html(find(plan, "t.py")), "");
  assert.equal(find(plan, "missing"), null);
  assert.equal(html({ id: "e", asbuilt: { text: "<script>" } }).includes("<script>"), false);
});
