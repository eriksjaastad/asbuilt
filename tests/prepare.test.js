import assert from "node:assert/strict";
import test from "node:test";
import { prepare } from "../src/asbuilt/page/prepare.js";

const leaf = (id, extra) => ({ id, width: 40, height: 20, ...extra });
const arrow = (id, a, b) => ({ id, sources: [a], targets: [b] });
const two = (extra) => ({ id: "r", children: [leaf("a"), leaf("b")], ...extra });

test("a good plan is copied: unchanged by the call, arrows moved to the root, a bare leaf sized", () => {
  const plan = { id: "r", children: [{ id: "g", children: [{ id: "a" }, leaf("b")], edges: [arrow("e", "a", "b")] }], x: 1 };
  const before = structuredClone(plan);
  const out = prepare(plan);
  assert.deepEqual(plan, before);
  assert.deepEqual(out.edges, [arrow("e", "a", "b")]);
  assert.equal(out.children[0].edges, undefined);
  assert.deepEqual(out.children[0].children[0], { id: "a", width: 190, height: 70, children: undefined });
  assert.deepEqual(out.children[0].children[1], leaf("b", { children: undefined }));
  assert.equal(out.x, 1);
});

test("a box is made wide enough for its label: a bare leaf grows, a given width stays, a box with children gets a minimum", () => {
  const text = "x".repeat(40);
  const [a, b, c, g, h] = prepare({
    id: "r",
    children: [
      { id: "a", labels: [{ text }] },
      { id: "😀".repeat(30) },
      leaf("c", { labels: [{ text }] }),
      { id: "g", labels: [{ text }], children: [leaf("k")] },
      { id: "h", layoutOptions: { "elk.nodeSize.minimum": "(1,1)" }, labels: [{ text }], children: [leaf("m")] },
    ],
  }).children;
  assert.deepEqual([a.width, b.width, c.width], [344, 264, 40]);
  assert.deepEqual(g.layoutOptions, { "elk.nodeSize.constraints": "MINIMUM_SIZE", "elk.nodeSize.minimum": "(0,344)" });
  assert.equal(h.layoutOptions["elk.nodeSize.minimum"], "(1,1)");
});

test("a refusal names where the problem is", () => {
  const plan = { id: "r", children: [leaf("a"), { id: "g", children: [leaf("b", { width: 0 })] }] };
  assert.throws(() => prepare(plan), { message: "plan.children[1].children[0]: width is not a number above 0" });
});

const BAD = {
  "not an object": [[null, 5, []], /is not an object/],
  "no boxes": [[{ id: "r" }, { id: "r", children: [] }], /has no boxes/],
  "children or edges not a list": [[{ id: "r", children: "x" }, { id: "r", children: null }, two({ edges: "x" })], /is not a list/],
  "an id that is not text": [[{ children: [leaf("a")] }, two({ edges: [{ sources: ["a"], targets: ["b"] }] })], /id that is not text/],
  "an id used twice": [
    [{ id: "r", children: [leaf("a"), { id: "g", children: [leaf("a")] }] }, two({ edges: [arrow("a", "a", "b")] })],
    /used twice/,
  ],
  "a width or height not a number above 0": [
    [0, "9", Infinity].map((width) => ({ id: "r", children: [leaf("a", { width })] })).concat({ id: "r", children: [leaf("a", { height: 0 })] }),
    /above 0/,
  ],
};
for (const [name, [plans, reason]] of Object.entries(BAD)) {
  test(`refused, with the reason: ${name}`, () => {
    for (const plan of plans) assert.throws(() => prepare(plan), reason, JSON.stringify(plan));
  });
}
