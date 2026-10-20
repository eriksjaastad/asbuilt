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
  assert.deepEqual(g.layoutOptions, { "elk.padding": "[top=36,left=14,bottom=14,right=14]", "elk.nodeSize.constraints": "MINIMUM_SIZE", "elk.nodeSize.minimum": "(0,344)" });
  assert.equal(h.layoutOptions["elk.nodeSize.minimum"], "(1,1)");
});

test("a refusal names the box by its id when it has one, and where it is", () => {
  const plan = { id: "r", children: [leaf("a"), { id: "g", children: [leaf("b", { width: 0 })] }] };
  assert.throws(() => prepare(plan), { message: 'box "b" at plan.children[1].children[0]: width is not a number above 0' });
  assert.throws(() => prepare({ id: "r", children: [{ children: [] }] }), { message: "plan.children[0]: has an id that is not text" });
  assert.throws(() => prepare({ id: "r", children: [leaf("a"), { id: "g", children: "x" }] }), { message: 'box "g" at plan.children[1]: children is not a list' });
  assert.throws(() => prepare(two({ edges: [{ sources: ["a"], targets: ["b"] }] })), { message: 'box "r" at plan: has an arrow whose id is not text' });
  assert.throws(() => prepare({ id: "r", children: [leaf("a"), leaf("a")] }), { message: 'box "a" at plan.children[1]: id "a" is used twice' });
});

const BAD = {
  "not an object": [[null, 5, []], /is not an object/],
  "no boxes": [[{ id: "r" }, { id: "r", children: [] }], /has no boxes/],
  "children or edges not a list": [[{ id: "r", children: "x" }, { id: "r", children: null }, two({ edges: "x" })], /is not a list/],
  "an id that is not text": [[{ children: [leaf("a")] }, two({ edges: [{ sources: ["a"], targets: ["b"] }] })], /(an|whose) id (that )?is not text$/],
  "an id used twice": [
    [{ id: "r", children: [leaf("a"), { id: "g", children: [leaf("a")] }] }, two({ edges: [arrow("a", "a", "b")] })],
    /used twice/,
  ],
  "a width or height not a number above 0": [
    [0, "9", Infinity].map((width) => ({ id: "r", children: [leaf("a", { width })] })).concat({ id: "r", children: [leaf("a", { height: 0 })] }),
    /above 0/,
  ],
};
test("an arrow not from one box to one box is refused, naming the end and the id it gives", () => {
  const cases = [
    [{ id: "r", children: [leaf("a"), leaf("b", { ports: [{ id: "p" }] })], edges: [arrow("e", "a", "p")] }, 'target "p" is not a box in the plan'],
    [two({ edges: [arrow("e", "a", "zzz")] }), 'target "zzz" is not a box in the plan'],
    [two({ edges: [arrow("e", "zzz", "b")] }), 'source "zzz" is not a box in the plan'],
    [two({ edges: [{ id: "e", sources: ["a"], targets: ["b", "a"] }] }), "targets must be a list of one box id"],
    [two({ edges: [{ id: "e", sources: "a", targets: ["b"] }] }), "sources must be a list of one box id"],
    [two({ edges: [{ id: "e", sources: ["a"], targets: ["r"] }] }), 'target "r" is not a box in the plan'],
    [two({ edges: [arrow("e", "a", "b"), arrow("f", "e", "b")] }), 'source "e" is not a box in the plan'],
  ];
  for (const [plan, why] of cases) {
    const id = plan.edges.at(-1).id;
    assert.throws(() => prepare(plan), { message: `arrow "${id}": ${why}` }, JSON.stringify(plan));
  }
});

for (const [name, [plans, reason]] of Object.entries(BAD)) {
  test(`refused, with the reason: ${name}`, () => {
    for (const plan of plans) assert.throws(() => prepare(plan), reason, JSON.stringify(plan));
  });
}
