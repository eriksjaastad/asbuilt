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

test("a refusal names where the problem is", () => {
  const plan = { id: "r", children: [leaf("a"), { id: "g", children: [leaf("b", { width: 0 })] }] };
  assert.throws(() => prepare(plan), { message: "plan.children[1].children[0]: width is not a number above 0" });
});

const BAD = {
  "not an object": [[null, 5, "x", [], { id: "r", children: [5] }, { id: "r", children: [leaf("a"), null] }], /is not an object/],
  "no boxes": [[{ id: "r" }, { id: "r", children: [] }, { id: "r", edges: [] }], /has no boxes/],
  "children or edges not a list": [
    [{ id: "r", children: "x" }, { id: "r", children: null }, two({ edges: "x" }), two({ edges: { a: 1 } })],
    /is not a list/,
  ],
  "an id that is not text": [
    [{ children: [leaf("a")] }, { id: "r", children: [{ ...leaf("a"), id: 5 }] }, two({ edges: [{ sources: ["a"], targets: ["b"] }] }), two({ edges: [7] })],
    /id that is not text/,
  ],
  "an id used twice": [
    [
      { id: "r", children: [leaf("a"), leaf("a")] },
      { id: "r", children: [leaf("a"), { id: "g", children: [leaf("a")] }] },
      { id: "r", children: [leaf("r")] },
      two({ edges: [arrow("a", "a", "b")] }),
      two({ edges: [arrow("e", "a", "b"), arrow("e", "b", "a")] }),
    ],
    /used twice/,
  ],
};
for (const key of ["width", "height"]) {
  const sizes = [0, -1, "9", NaN, Infinity, true, null, [1]];
  BAD[`${key} not a number above 0`] = [sizes.map((v) => ({ id: "r", children: [leaf("a", { [key]: v })] })), /above 0/];
}
for (const [name, [plans, reason]] of Object.entries(BAD)) {
  test(`refused, with the reason: ${name}`, () => {
    for (const plan of plans) assert.throws(() => prepare(plan), reason, JSON.stringify(plan));
  });
}
