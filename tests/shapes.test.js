import assert from "node:assert/strict";
import test from "node:test";
import rough from "roughjs/bundled/rough.esm.js";
import { drawn, esc, seedOf } from "../src/asbuilt/page/shapes.js";
import { decode, escaped, evils } from "./helpers.js";

test("esc leaves nothing that can break out of text or an attribute, and decodes to the original", () => {
  for (const s of [...evils, "plain", "", "😀"]) {
    assert.match(esc(s), escaped, s);
    assert.equal(decode(esc(s)), s);
  }
});

// An id whose FNV hash is 0: its last character equals the hash of the rest, which cancels it.
const fnv = (s) => [...s].reduce((h, c) => Math.imul(h ^ c.codePointAt(0), 16777619), 2166136261);
const zeroHashId = () => {
  for (let i = 0; ; i++) {
    const last = fnv(`id${i}`) >>> 0;
    if (last > 0 && last < 0xd800) return `id${i}${String.fromCharCode(last)}`;
  }
};
test("a seed is the same for the same id, differs between ids, and is never 0", () => {
  const ids = Array.from({ length: 500 }, (_, i) => `box${i}`);
  assert.equal(new Set(ids.map(seedOf)).size, 500);
  assert.equal(fnv(zeroHashId()), 0);
  assert.equal(seedOf("a"), seedOf("a"));
  for (const id of [...ids, "", zeroHashId()]) assert.ok(Number.isInteger(seedOf(id)) && seedOf(id) > 0, id);
});

test("a drawable becomes paths, one per part (fill, stroke), each with its colours, the same every time for one seed", () => {
  const gen = rough.generator();
  const solid = drawn(gen, gen.rectangle(0, 0, 9, 9, { seed: 3, fill: "red", fillStyle: "solid", stroke: "none" }));
  const line = drawn(gen, gen.rectangle(0, 0, 9, 9, { seed: 3, stroke: "blue", strokeWidth: 2 }));
  assert.match(solid, /^<path d="M[^"]+" fill="red" stroke="none" stroke-width="0"\/>$/);
  const both = drawn(gen, gen.rectangle(0, 0, 9, 9, { seed: 3, fill: "red", fillStyle: "solid", stroke: "blue" }));
  assert.equal(both.split("<path").length, 3);
  assert.match(line, /fill="none" stroke="blue" stroke-width="2"/);
  assert.equal(line, drawn(gen, gen.rectangle(0, 0, 9, 9, { seed: 3, stroke: "blue", strokeWidth: 2 })));
});
