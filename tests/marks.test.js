import assert from "node:assert/strict";
import test from "node:test";
import { byMarks } from "../src/asbuilt/page/marks.js";
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
  }],
  edges: [{ id: "out", sources: ["post"], targets: ["ask"] }],
};

const ids = (box, into = []) => (into.push(box.id), (box.children ?? []).forEach((child) => ids(child, into)), into);
const arrows = (box, into = []) => ((box.edges ?? []).forEach((edge) => into.push(edge.id)), (box.children ?? []).forEach((child) => arrows(child, into)), into);

test("no chosen mark is the plan itself", () => {
  assert.equal(byMarks(plan, []), plan);
  assert.equal(byMarks(plan, null), plan);
  assert.equal(byMarks(plan, ["nope"]), plan);
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
  assert.deepEqual(ids(out).sort(), ["app", "ask", "b.py"]);
  assert.deepEqual(arrows(out), []);
});

test("marks and the pipeline both apply: a box has to be reached and carry a chosen mark", () => {
  const reached = pipeline(plan, "read");
  const out = byMarks(reached, ["calls out"]);
  assert.ok(ids(reached).includes("local") && ids(reached).includes("ask"));
  assert.deepEqual(ids(out).sort(), ["a.py", "app", "post"]);
  assert.deepEqual(arrows(out), []);
  const both = byMarks(reached, ["calls out", "model"]);
  assert.deepEqual(arrows(both), ["out"]);
  assert.equal(ids(both).includes("ask"), true);
  assert.equal(ids(both).includes("local"), false);
});
