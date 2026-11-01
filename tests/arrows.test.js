import assert from "node:assert/strict";
import test from "node:test";
import rough from "roughjs/bundled/rough.esm.js";
import { arrows } from "../src/asbuilt/page/arrows.js";
import { draw } from "../src/asbuilt/page/draw.js";
import { decode, escaped, evils, laid, pieces } from "./helpers.js";

const coords = (svg) => [...svg.matchAll(/ d="([^"]*)"/g)].map((m) => (m[1].match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g) ?? []).map(Number));
const pairs = (nums) => nums.flatMap((n, i) => (i % 2 ? [] : [[n, nums[i + 1]]]));
const run = (id, points) => coords(arrows([{ id, points }], rough.generator()));

test("41 arrows are drawn, each a line and a head, the same on a redraw and wherever they sit in the list", () => {
  const first = pieces(draw(laid, rough.generator()), "arrow");
  assert.equal(Object.keys(first).length, 41);
  const svg = draw(laid, rough.generator());
  const inked = svg.slice(svg.indexOf('<g filter="url(#ink)">'), svg.indexOf("<text")); // the layer that wobbles
  assert.equal(Object.keys(pieces(inked, "arrow")).length, 41);
  assert.ok(Object.values(first).every((a) => a.split("<path").length === 3));
  const again = draw({ ...laid, arrows: [...laid.arrows].reverse() }, rough.generator());
  assert.deepEqual(pieces(again, "arrow"), first);
});

test("an arrow id is escaped, whatever it holds, and decodes back to exactly what was given", () => {
  for (const evil of evils) {
    const [id] = Object.keys(pieces(arrows([{ id: evil, points: [[0, 0], [9, 9]] }], rough.generator()), "arrow"));
    assert.match(id, escaped, evil);
    assert.equal(decode(id), evil);
  }
});

test("the line goes through its bend points and the head sits behind the tip, whichever way it points", () => {
  const [line] = run("e", [[0, 10], [100, 60], [200, 10]]);
  assert.ok(pairs(line).some(([x, y]) => Math.abs(x - 100) < 15 && Math.abs(y - 60) < 15), "passes the bend");
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const tip = [100 + 100 * dx, 100 + 100 * dy];
    const [, head] = run("e", [[100, 100], tip]);
    const back = pairs(head).map(([x, y]) => ((x - tip[0]) * dx + (y - tip[1]) * dy));
    assert.ok(back.every((b) => b < 3 && b > -16), `behind the tip, dir ${dx},${dy}: ${back}`);
    assert.ok(back.reduce((a, b) => a + b) / back.length < -3, "mean behind the tip");
    const side = pairs(head).map(([x, y]) => (x - tip[0]) * -dy + (y - tip[1]) * dx);
    assert.ok(Math.max(...side) > 2 && Math.min(...side) < -2, `a barb on each side: ${side}`);
  }
});
