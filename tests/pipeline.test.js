import assert from "node:assert/strict";
import test from "node:test";
import { pipeline, resolveFocus } from "../src/asbuilt/page/pipeline.js";

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

test("pipeline guards against malformed plans without throwing", () => {
  for (const bad of [null, undefined, 42, "nope", []]) {
    assert.equal(pipeline(bad, "a"), bad);
  }
  const badChildren = { id: "r", children: "not-a-list", edges: 123 };
  assert.equal(pipeline(badChildren, "missing"), badChildren);
  const mixed = {
    id: "r",
    children: [null, 5, { id: "a" }, { id: "b" }],
    edges: [null, 7, { id: "e", sources: ["a"], targets: ["b"] }],
  };
  assert.deepEqual(ids(pipeline(mixed, "a")).sort(), ["a", "b", "r"]);
});

test("pipeline handles empty-string ids as start and as parent", () => {
  const withEmptyStart = {
    id: "r",
    children: [{ id: "" }, { id: "b" }, { id: "c" }],
    edges: [{ id: "e", sources: [""], targets: ["b"] }],
  };
  assert.deepEqual(ids(pipeline(withEmptyStart, "")).sort(), ["", "b", "r"]);

  const withEmptyParent = {
    id: "",
    children: [{ id: "a" }, { id: "b" }],
    edges: [{ id: "e", sources: ["a"], targets: ["b"] }],
  };
  assert.deepEqual(ids(pipeline(withEmptyParent, "a")).sort(), ["", "a", "b"]);
});

test("an arrow into a group or file keeps its contents and what they reach", () => {
  const grouped = {
    id: "app",
    children: [
      { id: "entry" },
      {
        id: "service",
        children: [
          { id: "inner-a" },
          { id: "inner-b" },
          { id: "nested", children: [{ id: "deep" }] },
        ],
        edges: [{ id: "inside", sources: ["inner-a"], targets: ["inner-b"] }],
      },
      { id: "downstream" },
      { id: "aside" },
    ],
    edges: [
      { id: "into-group", sources: ["entry"], targets: ["service"] },
      { id: "out-of-group", sources: ["inner-a"], targets: ["downstream"] },
    ],
  };
  const out = pipeline(grouped, "entry");
  assert.deepEqual(
    ids(out).sort(),
    ["app", "deep", "downstream", "entry", "inner-a", "inner-b", "nested", "service"],
  );
  assert.deepEqual(arrows(out).sort(), ["inside", "into-group", "out-of-group"]);
});

test("compare charts ignore dropped plan arrows when computing reachability", () => {
  const compared = {
    id: "app",
    children: [
      { id: "a" },
      { id: "b" },
      { id: "c" },
      { id: "d" },
      { id: "e" },
      { id: "f" },
    ],
    edges: [
      { id: "kept-planned", sources: ["a"], targets: ["b"], asbuilt: { mark: "planned" } },
      { id: "gone", sources: ["a"], targets: ["c"], asbuilt: { mark: "dropped" } },
      { id: "kept-added", sources: ["a"], targets: ["d"], asbuilt: { mark: "added" } },
      { id: "kept-changed", sources: ["d"], targets: ["e"], asbuilt: { mark: "changed" } },
      { id: "after-dropped", sources: ["c"], targets: ["f"] },
    ],
  };
  const out = pipeline(compared, "a");
  assert.deepEqual(ids(out).sort(), ["a", "app", "b", "d", "e"]);
  assert.deepEqual(arrows(out).sort(), ["kept-added", "kept-changed", "kept-planned"]);
  assert.equal(pipeline(compared, null), compared, "unfiltered compare plan stays unchanged");
});

test("resolveFocus keeps exact match, follows a single unambiguous line move, and clears when ambiguous or gone", () => {
  const moved = {
    id: "app",
    children: [
      { id: "shop.py", children: [{ id: "shop.py:load:24" }, { id: "shop.py:save:40" }] },
      { id: "sub", children: [{ id: "sub/a.py:run:5" }] },
    ],
  };
  assert.equal(resolveFocus(moved, "shop.py:load:24"), "shop.py:load:24");
  assert.equal(resolveFocus(moved, "shop.py:load:12"), "shop.py:load:24");
  assert.equal(resolveFocus(moved, "shop.py:load"), "shop.py:load:24");
  assert.equal(resolveFocus(moved, "sub/"), "sub");
  assert.equal(resolveFocus(moved, "shop.py:missing:12"), null);

  const ambiguous = {
    id: "app",
    children: [
      { id: "shop.py", children: [{ id: "shop.py:load:24" }, { id: "shop.py:load:50" }] },
    ],
  };
  assert.equal(resolveFocus(ambiguous, "shop.py:load:12"), null);
  assert.equal(resolveFocus(ambiguous, "shop.py:load:24"), "shop.py:load:24", "exact match wins even when another shares the stable key");

  const singleColon = {
    id: "app",
    children: [{ id: "a:20" }],
  };
  assert.equal(resolveFocus(singleColon, "a:10"), null, "single-colon ids are not stripped like file:fn:line");
  assert.equal(resolveFocus(null, "shop.py:load:12"), null);
  assert.equal(resolveFocus(moved, null), null);
});
