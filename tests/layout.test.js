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

test("an arrow that skips a box runs clear of it, and arrows keep their bend points", async () => {
  const plan = { id: "r", children: [leaf("a"), leaf("b"), leaf("c")], edges: [arrow("e1", "a", "b"), arrow("e2", "b", "c"), arrow("e3", "a", "c")] };
  const laid = await layout(plan, elk);
  assertSound(plan, laid, 3, 3);
  const b = laid.boxes.find((k) => k.id === "b");
  const { points } = laid.arrows.find((a) => a.id === "e3");
  const clear = (lo, hi, at, size) => Math.max(lo, hi) <= at || Math.min(lo, hi) >= at + size;
  assert.ok(points.slice(1).every((q, i) => clear(q[0], points[i][0], b.x, b.w) || clear(q[1], points[i][1], b.y, b.h)));
  assert.ok(laid.arrows.some((a) => a.points.length > 2));
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
  const across = { id: "r", children: [{ id: "g", children: [leaf("a")] }, leaf("x"), leaf("y"), { id: "h", children: [leaf("p"), leaf("q"), leaf("s")] }],
    edges: [arrow("e1", "a", "s"), arrow("e2", "a", "p"), arrow("e3", "a", "x"), arrow("e4", "a", "y")] };
  for (const plan of [inside, across]) {
    const side = sideBySide((await layout(plan, elk)).arrows);
    assert.ok(side.length >= 1);
    for (const [, apart] of side) assert.ok(apart >= 19.5, `${apart} px apart`);
  }
});

test("an arrow's label comes back beside its arrow, in absolute coordinates, clear of the boxes", async () => {
  const plan = { id: "r", children: [leaf("a"), { id: "g", children: [leaf("b"), leaf("c")] }],
    edges: [{ ...arrow("e1", "a", "b"), labels: [{ text: "daily" }] }, { ...arrow("e2", "b", "c"), labels: [{ text: "email link" }] }, arrow("e3", "a", "c")] };
  const { boxes, arrows } = await layout(plan, elk);
  const [a, g, b, c] = boxes;
  const clear = (l, k) => l.x + l.w <= k.x || l.x >= k.x + k.w || l.y + 16 <= k.y || l.y >= k.y + k.h;
  for (const [id, text, holder] of [["e1", "daily", { x: 0, y: 0, w: Infinity, h: Infinity }], ["e2", "email link", g]]) {
    const { label, points } = arrows.find((x) => x.id === id);
    assert.deepEqual([label.text, label.w], [text, 7 * text.length + 8]);
    assert.ok([a, b, c].every((k) => clear(label, k)) && inside([label.x, label.y], holder) && inside([label.x + label.w, label.y + 16], holder));
    assert.ok(points.some(([x]) => Math.abs(x - label.x) < 20 || Math.abs(x - label.x - label.w) < 20), id);
  }
  assert.equal(arrows.find((x) => x.id === "e3").label, undefined);
});

test("arrows into a group from a bare box and from another group do not cross", async () => {
  const plan = { id: "r", children: [leaf("c"), { id: "in", children: [leaf("p"), leaf("f")] }, { id: "out", children: [leaf("x"), leaf("w")] }],
    edges: [arrow("e0", "c", "x"), arrow("e1", "c", "w"), arrow("e2", "p", "x"), arrow("e3", "f", "w")] };
  const segs = (await layout(plan, elk)).arrows.flatMap((a) => a.points.slice(1).map((q, i) => [a.id, a.points[i], q]));
  const across = (a, b, c) => c > Math.min(a, b) && c < Math.max(a, b);
  const crossings = segs.filter(([a, p, q]) => p[1] === q[1] && segs.some(([b, s, t]) =>
    a !== b && s[0] === t[0] && across(p[0], q[0], s[0]) && across(s[1], t[1], p[1])));
  assert.deepEqual(crossings, []);
});

test("generated labels on a box, a group and an arrow: drawn with their words, or refused naming the first holder, never an ELK error", async () => {
  // fast-check is not a dependency; a seeded Park-Miller generator picks the inputs (same cases every run)
  let seed = 31_415;
  const rand = (n) => ((seed = (seed * 48271) % 2147483647), seed % n);
  const list = (make) => Array.from({ length: rand(4) }, make);
  const any = (d) => [() => `w${rand(9)}`, () => "", () => rand(201) - 100, () => rand(2) === 1, () => null,
    () => list(() => any(d + 1)), () => ({ text: any(d + 1) }), () => ({})][rand(d > 2 ? 5 : 8)]();
  const labels = () => (rand(5) ? list(() => (rand(10) ? { text: `t${rand(99)}` } : any(1))) : any(0));
  const readable = (l) => l === undefined || (Array.isArray(l) && l.every((x) => typeof x?.text === "string"));
  for (let i = 0; i < 400; i++) {
    const [la, lg, le] = [0, 0, 0].map(() => (rand(4) ? labels() : undefined));
    const plan = JSON.parse(JSON.stringify({ id: "r", children: [{ ...leaf("a"), labels: la }, { id: "g", labels: lg, children: [leaf("b")] }],
      edges: [{ ...arrow("e", "a", "b"), labels: le }] }));
    const why = JSON.stringify(plan);
    const refusal = [[le, 'box "r" at plan: has an arrow "e" with'], [la, 'box "a" at plan.children[0]: has'], [lg, 'box "g" at plan.children[1]: has']]
      .find(([l]) => !readable(l));
    if (refusal) {
      await assert.rejects(layout(plan, elk), { message: `${refusal[1]} labels that are not a list like [{"text": "..."}]` }, why);
      continue;
    }
    const { boxes, arrows } = await layout(plan, elk);
    for (const [b, l] of [[boxes[0], la], [boxes[1], lg]]) assert.deepEqual([b.label, b.note], [l?.[0]?.text ?? b.id, l?.[1]?.text ?? ""], why);
    assert.equal(arrows[0].label?.text, le?.[0]?.text || undefined, why);
  }
  assert.equal((await layout({ id: "r", children: [{ ...leaf("a"), labels: [] }] }, elk)).boxes[0].label, "a");
});
