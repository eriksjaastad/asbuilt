import assert from "node:assert/strict";
import test from "node:test";
import { byMarks, carries, markCounts, showing, shown } from "../src/asbuilt/page/marks.js";
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
  assert.equal(showing(["env"], ["a()", "b()"], 1), "Showing boxes whose own code uses environment settings. Hiding 2 boxes whose own code does not: a(), b(). Also hiding 1 arrow to or from a box whose own code does not.");
  assert.equal(showing(["calls out", "env"], ["a()"], 0), "Showing boxes whose own code uses environment settings or talks to the network or runs programs. Hiding 1 box whose own code does not: a().");
  assert.equal(showing(["env"], [], 0), "Showing boxes whose own code uses environment settings. Hiding nothing.");
  assert.equal(showing(["writes"], ["a", "b", "c", "d", "e", "f", "g", "h"], 2), "Showing boxes whose own code writes files. Hiding 8 boxes whose own code does not: a, b, c, d, e, f, and 2 more. Also hiding 2 arrows to or from a box whose own code does not.");
  assert.equal(carries(["input"]), "No box here has its own code that asks a person to type.");
  assert.equal(carries(["env", "calls out"]), "No box here has its own code that uses environment settings or talks to the network or runs programs.");
  assert.equal(carries(["model"], "main()"), "No box in what main() reaches has its own code that uses an AI library.");
});

test("a mark button's count is how many boxes in this view prove it", () => {
  assert.deepEqual(markCounts(plan), { env: 1, input: 1, writes: 0, "calls out": 1, model: 2 });
  assert.deepEqual(markCounts(pipeline(plan, "read")).model, 1);
});
