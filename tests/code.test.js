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

test("a model note is labeled model, and the proven sentence is not", () => {
  const out = html({
    id: "f",
    labels: [{ text: "f()" }],
    asbuilt: {
      text: "def f():\n    return g()\n",
      proven: sentence,
      model: [{ label: "tricky part", text: "the name hides the call" }],
    },
  });
  const [before] = out.split('<p class="model">');
  assert.ok(before.includes(sentence));
  assert.equal(before.includes("model"), false);
  assert.match(out, /<b>model<\/b> tricky part: the name hides the call/);
});

test("plain shows the parser's plain sentence and not the expert one", () => {
  const out = html({
    id: "f",
    labels: [{ text: "f()" }],
    asbuilt: {
      text: "def f():\n    pass\n",
      security: [{ plain: "This reads <K> from the environment.", expert: "os.environ.get(KEY) reads <K>." }],
    },
  });
  assert.match(out, /<p class="security">This reads &#60;K&#62; from the environment\.<\/p>/);
  assert.equal(out.includes("os.environ.get(KEY)"), false);
  assert.equal(out.includes("<K>"), false);
});
