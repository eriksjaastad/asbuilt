import assert from "node:assert/strict";
import test from "node:test";
import { byMarks, carries, hiddenBoxes, markCounts, showing, shown } from "../src/asbuilt/page/marks.js";
import { pipeline } from "../src/asbuilt/page/pipeline.js";

const badge = (id, badges) => ({ id, asbuilt: { badges } });
const plan = {
  id: "app",
  children: [{
    id: "a.py",
    children: [badge("read", ["env", "input"]), badge("post", ["calls out"]), { id: "local" }],
    edges: [
      { id: "uses", sources: ["read"], targets: ["post"] },
      { id: "aside", sources: ["read"], targets: ["local"] },
    ],
  }, {
    id: "b.py",
    children: [badge("ask", ["model"])],
  }, badge("far", ["model"])],
  edges: [{ id: "out", sources: ["post"], targets: ["ask"] }],
};

const ids = (box, into = []) => (into.push(box.id), (box.children ?? []).forEach((child) => ids(child, into)), into);
const arrows = (box, into = []) => ((box.edges ?? []).forEach((edge) => into.push(edge.id)), (box.children ?? []).forEach((child) => arrows(child, into)), into);

test("no chosen mark is the plan itself", () => {
  assert.equal(byMarks(plan, []), plan);
  assert.equal(byMarks(plan, null), plan);
  assert.equal(byMarks(plan, ["nope"]), plan);
  assert.equal(shown(plan, null, []), null);
  assert.equal(shown(plan, null, ["nope"]), null);
});

test("chosen marks keep every matching box and the arrows between them, and nothing else", () => {
  const full = ids(plan);
  const fullArrows = arrows(plan);
  const out = byMarks(plan, ["env", "calls out"]);
  const kept = ids(out);
  const joined = arrows(out);
  assert.deepEqual(kept.sort(), ["a.py", "app", "post", "read"]);
  assert.deepEqual(joined, ["uses"]);
  assert.ok(kept.every((id) => full.includes(id)));
  assert.ok(joined.every((id) => fullArrows.includes(id)));
  for (const id of ["read", "post"]) assert.ok(kept.includes(id), id);
  assert.equal(kept.includes("local"), false);
  assert.equal(kept.includes("ask"), false);
  assert.equal(joined.includes("aside"), false);
  assert.equal(joined.includes("out"), false);
});

test("a mark on its own still keeps that box when another mark is not chosen", () => {
  const out = byMarks(plan, ["model"]);
  assert.deepEqual(ids(out).sort(), ["app", "ask", "b.py", "far"]);
  assert.deepEqual(arrows(out), []);
});

test("marks and the pipeline both apply: a box has to be reached and carry a chosen mark", () => {
  const reached = pipeline(plan, "read");
  const out = byMarks(reached, ["calls out"]);
  assert.ok(ids(reached).includes("local") && ids(reached).includes("ask"));
  assert.equal(ids(reached).includes("far"), false);
  assert.deepEqual(ids(out).sort(), ["a.py", "app", "post"]);
  assert.deepEqual(arrows(out), []);
  const both = shown(plan, "read", ["calls out", "model"]);
  assert.deepEqual(arrows(both), ["out"]);
  assert.equal(ids(both).includes("ask"), true);
  assert.equal(ids(both).includes("far"), false);
  assert.equal(ids(both).includes("local"), false);
});

test("the line says in plain words what is shown, which boxes are hidden and why", () => {
  const fn = (name) => ({ name, checked: true });
  assert.equal(showing(["env"], [fn("a()"), fn("b()")], 1), "Showing functions and classes whose own code uses environment settings, and the boxes that hold them. Hiding 2 functions and classes whose own code does not: a(), b(). Also hiding 1 arrow that does not join two such functions or classes.");
  assert.equal(showing(["calls out", "env"], [fn("a()")], 0), "Showing functions and classes whose own code uses environment settings or talks to the network or runs programs, and the boxes that hold them. Hiding 1 function or class whose own code does not: a().");
  assert.equal(showing(["env"], [], 0), "Showing functions and classes whose own code uses environment settings, and the boxes that hold them. Hiding nothing.");
  assert.equal(showing(["writes"], ["a", "b", "c", "d", "e", "f", "g", "h"].map(fn), 2), "Showing functions and classes whose own code writes files, and the boxes that hold them. Hiding 8 functions and classes whose own code does not: a, b, c, d, e, f, and 2 more. Also hiding 2 arrows that do not join two such functions or classes.");
  assert.equal(carries(["input"]), "No function or class here has its own code that asks a person to type. Code outside a function or class is not checked.");
  assert.equal(carries(["env", "calls out"]), "No function or class here has its own code that uses environment settings or talks to the network or runs programs. Code outside a function or class is not checked.");
  assert.equal(carries(["model"], "main()"), "No function or class in what main() reaches has its own code that uses the OpenAI or Anthropic library. Code outside a function or class is not checked.");
});

test("a file whose top-level code was never checked is not said to lack the mark", () => {
  // top.py has KEY = os.environ["K"] at top level: no box carries that code, so no mark is proven for it.
  const files = {
    id: "app",
    children: [
      { id: "top.py", labels: [{ text: "top.py" }], children: [{ id: "f", labels: [{ text: "f()" }], asbuilt: { text: "def f():\n    pass\n" } }] },
      { id: "env.py", labels: [{ text: "env.py" }], children: [{ id: "g", labels: [{ text: "g()" }], asbuilt: { text: "def g():\n    os.environ['K']\n", badges: ["env"] } }] },
    ],
  };
  const hidden = hiddenBoxes(files, ["env.py", "g"]);
  assert.deepEqual(hidden, [{ name: "top.py", checked: false }, { name: "f()", checked: true }]);
  const line = showing(["env"], hidden, 0);
  assert.equal(line, "Showing functions and classes whose own code uses environment settings, and the boxes that hold them. Hiding 1 function or class whose own code does not: f(). Hiding 1 other box holding no such function or class: top.py. Code outside a function or class is not checked.");
  assert.equal(line.includes("does not: top.py"), false);
});

test("hidden boxes that share a label are told apart by the box that holds them", () => {
  const run = (id) => ({ id, labels: [{ text: "run()" }], asbuilt: { text: "def run():\n    pass\n" } });
  const files = { id: "app", children: [{ id: "a.py", children: [run("a")] }, { id: "b.py", children: [run("b"), { id: "solo", labels: [{ text: "solo()" }], asbuilt: { text: "" } }] }] };
  assert.deepEqual(hiddenBoxes(files, ["a.py", "b.py"]).map((h) => h.name), ["run() in a.py", "run() in b.py", "solo()"]);
});

test("a mark button's count is how many boxes in this view prove it", () => {
  assert.deepEqual(markCounts(plan), { env: 1, input: 1, writes: 0, "calls out": 1, model: 2 });
  assert.deepEqual(markCounts(pipeline(plan, "read")).model, 1);
});
