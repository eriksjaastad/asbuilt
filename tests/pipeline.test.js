import assert from "node:assert/strict";
import test from "node:test";
import { pipeline } from "../src/asbuilt/page/pipeline.js";

const plan = {
  id: "app",
  children: [{
    id: "shop",
    children: [
      { id: "a.py", children: [{ id: "load" }, { id: "save" }, { id: "skip" }], edges: [
        { id: "on", sources: ["load"], targets: ["save"] },
        { id: "back", sources: ["skip"], targets: ["load"] },
      ] },
      { id: "b.py", children: [{ id: "other" }, { id: "secret" }] },
    ],
  }],
  edges: [{ id: "out", sources: ["save"], targets: ["other"] }],
};

const ids = (box, into = []) => (into.push(box.id), (box.children ?? []).forEach((child) => ids(child, into)), into);
const arrows = (box, into = []) => (into.push(...(box.edges ?? []).map((edge) => edge.id)), (box.children ?? []).forEach((child) => arrows(child, into)), into);

test("a box keeps what its arrows reach and the boxes around them, and drops the rest", () => {
  const out = pipeline(plan, "load");
  assert.deepEqual(ids(out).sort(), ["a.py", "app", "b.py", "load", "other", "save", "shop"]);
  assert.deepEqual(arrows(out).sort(), ["on", "out"]);
});

test("starting on a file starts from the boxes inside it", () => {
  const out = pipeline(plan, "a.py");
  assert.ok(ids(out).includes("skip") && ids(out).includes("other"));
  assert.equal(ids(out).includes("secret"), false);
  assert.deepEqual(arrows(out).sort(), ["back", "on", "out"]);
});

test("a start the plan does not have leaves the plan unchanged", () => {
  assert.equal(pipeline(plan, "missing"), plan);
  assert.equal(pipeline(plan, null), plan);
});
