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

test("a label is the first label's text and a note the second's, or empty", async () => {
  const plan = { id: "r", children: [{ ...leaf("a"), labels: [{ text: "fn()" }, { text: "other" }] }, leaf("b")] };
  assert.deepEqual((await layout(plan, elk)).boxes.map((b) => [b.label, b.note]), [["fn()", "other"], ["b", ""]]);
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

test("a box that holds others and has a note keeps its top 54 px clear for both lines", async () => {
  const plan = { id: "r", children: [{ id: "p", labels: [{ text: "p" }, { text: "note" }], children: [leaf("k")] }] };
  const byId = Object.fromEntries((await layout(plan, elk)).boxes.map((b) => [b.id, b]));
  assert.equal(byId.k.y - byId.p.y, 54);
});

const gap = ([x, y], [a, b], [c, d]) => {
  const t = Math.max(0, Math.min(1, ((x - a) * (c - a) + (y - b) * (d - b)) / ((c - a) ** 2 + (d - b) ** 2 || 1)));
  return Math.hypot(x - a - t * (c - a), y - b - t * (d - b));
};

test("an arrow that ends on a box lands well clear of an arrow crossing its border to a box inside", async () => {
  const plan = { id: "r", children: [leaf("f"), { id: "s", children: [leaf("k")] }, leaf("a")],
    edges: [arrow("in", "f", "k"), arrow("back", "a", "s"), arrow("out", "s", "a")] };
  const { arrows } = await layout(plan, elk);
  const [head, line] = [arrows.find((x) => x.id === "back").points.at(-1), arrows.find((x) => x.id === "in").points];
  assert.ok(Math.min(...line.slice(1).map((p, i) => gap(head, line[i], p))) >= 24);
});

// Every pair of level runs, from different arrows, that overlap by more than 20 px: [overlap, distance apart].
function sideBySide(arrows) {
  const flat = arrows.flatMap((a) =>
    a.points.slice(1).map((p, i) => [a.id, a.points[i], p]).filter(([, [, y0], [, y1]]) => Math.abs(y0 - y1) < 0.5));
  return flat.flatMap(([id, p, q], i) => flat.slice(i + 1).filter(([other]) => other !== id).map(([, r, s]) => [
    Math.min(Math.max(p[0], q[0]), Math.max(r[0], s[0])) - Math.max(Math.min(p[0], q[0]), Math.min(r[0], s[0])), Math.abs(p[1] - r[1])]))
    .filter(([overlap]) => overlap > 20);
}

test("arrows that run side by side, inside a box and across the plan, stay 20 px apart", async () => {
  const inside = { id: "r", children: [{ id: "g", children: [leaf("a"), leaf("b"), leaf("c")],
    edges: [arrow("e0", "a", "c"), arrow("e1", "a", "b"), arrow("e2", "a", "b")] }] };
  const across = { id: "r", children: [{ id: "g", children: [leaf("a")] }, leaf("x"), { id: "h", children: [leaf("p"), leaf("q"), leaf("s")] }],
    edges: [arrow("e1", "a", "s"), arrow("e2", "a", "q"), arrow("e3", "a", "p"), arrow("e4", "a", "x")] };
  for (const plan of [inside, across]) {
    const side = sideBySide((await layout(plan, elk)).arrows);
    assert.ok(side.length >= 1);
    for (const [, apart] of side) assert.ok(apart >= 19.5, `${apart} px apart`);
  }
});
