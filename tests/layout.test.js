import assert from "node:assert/strict";
import test from "node:test";
import ELK from "elkjs/lib/elk.bundled.js";
import { layout } from "../src/asbuilt/page/layout.js";
import { arrow, big, leaf } from "./plans.js";

const elk = new ELK();
const inside = (p, b) => p[0] >= b.x - 1 && p[0] <= b.x + b.w + 1 && p[1] >= b.y - 1 && p[1] <= b.y + b.h + 1;

// Holds for any plan: every box and arrow comes back, children sit inside their parent, and each
// arrow starts on its source and ends on its target (all in absolute coordinates).
function assertSound(plan, laid, boxCount, arrowCount) {
  assert.equal(laid.boxes.length, boxCount);
  assert.equal(laid.arrows.length, arrowCount);
  const byId = Object.fromEntries(laid.boxes.map((b) => [b.id, b]));
  const parents = {};
  const mark = (box) => (box.children ?? []).forEach((c) => ((parents[c.id] = box.id), mark(c)));
  mark(plan);
  for (const b of laid.boxes) {
    const p = byId[parents[b.id]];
    assert.equal(b.level, p ? p.level + 1 : 1, b.id);
    if (p) assert.ok(inside([b.x, b.y], p) && inside([b.x + b.w, b.y + b.h], p), `${b.id} inside ${p.id}`);
  }
  for (const a of laid.arrows) {
    assert.ok(inside(a.points[0], byId[a.from]), `${a.id} starts on ${a.from}`);
    assert.ok(inside(a.points.at(-1), byId[a.to]), `${a.id} ends on ${a.to}`);
  }
}

test("a 49-box plan with arrows inside and across groups comes back whole, nested and joined", async () => {
  assertSound(big, await layout(big, elk), 49, 41);
});

test("a box with arrows inside it, a bare leaf and a label without text are drawn", async () => {
  const plan = { id: "r", children: [{ id: "g", children: [{ id: "a" }, leaf("b")], edges: [arrow("e", "a", "b")] }] };
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 3, 1);
  assert.deepEqual(laid.boxes.filter((b) => b.id === "a").map((b) => [b.w, b.h, b.label]), [[190, 70, "a"]]);
});

test("a label is the first label's text", async () => {
  const plan = { id: "r", children: [{ ...leaf("a"), labels: [{ text: "fn()" }, { text: "other" }] }] };
  assert.equal((await layout(plan, elk)).boxes[0].label, "fn()");
});

test("an arrow that skips a box bends around it and keeps its bend points", async () => {
  const plan = { id: "r", children: [leaf("a"), leaf("b"), leaf("c")], edges: [arrow("e1", "a", "b"), arrow("e2", "b", "c"), arrow("e3", "a", "c")] };
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 3, 3);
  assert.ok(laid.arrows.find((a) => a.id === "e3").points.length > 2);
});

test("a long label fits inside its box, whether the box is a bare leaf or holds other boxes", async () => {
  const text = "validate_credentials(user, password)";
  const plan = { id: "r", children: [{ id: "a", labels: [{ text }] }, { id: "g", labels: [{ text }], children: [leaf("b")] }] };
  const laid = await layout(plan, elk);
  assert.deepEqual(laid.boxes.filter((b) => b.label === text).map((b) => b.w >= 8 * text.length + 24), [true, true]);
});

test("every box that holds others, the plan included, keeps its top 36 px clear for the label", async () => {
  const plan = { id: "r", children: [{ id: "p", children: [{ id: "c", children: [leaf("k")] }] }] };
  const byId = Object.fromEntries((await layout(plan, elk)).boxes.map((b) => [b.id, b]));
  assert.deepEqual([byId.p.y, byId.c.y - byId.p.y, byId.k.y - byId.c.y], [36, 36, 36]);
});
