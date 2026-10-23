import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import rough from "roughjs/bundled/rough.esm.js";
import { draw } from "../src/asbuilt/page/draw.js";
import { decode, escaped, evils, laid, pieces } from "./helpers.js";

const one = (id) => ({ id, x: 0, y: 0, w: 50, h: 20, level: 1, label: id });
const small = (id) => ({ width: 60, height: 30, boxes: [one(id)], arrows: [] });

test("49 boxes are drawn, each with a wash, a label and a margin, and nothing is NaN", () => {
  const svg = draw(laid, rough.generator());
  assert.equal(Object.keys(pieces(svg, "box")).length, 49);
  assert.equal(Object.keys(pieces(svg, "wash")).length, 49);
  assert.equal(svg.split("<text").length, 50);
  const [w, h] = [laid.width + 20, laid.height + 20];
  assert.ok(svg.includes(`viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"`));
  assert.ok(laid.boxes.every((b) => svg.includes(`<text x="${b.x + b.w / 2}" y="${b.y + 22}"`)));
  assert.equal(svg.split('fill="rgba(170,158,135,0.20)"').length, 50);
  assert.doesNotMatch(svg, /NaN|undefined|Infinity|fill="black"/);
});

test("every label, note and arrow label sits on a paper halo drawn under its letters, so an arrow under it cannot hide it", () => {
  const arrow = { id: "l", points: [[0, 0], [0, 30]], label: { x: 4, y: 6, w: 40, text: "l" } };
  const svg = draw({ ...laid, boxes: [...laid.boxes, { ...one("n"), note: "n" }], arrows: [...laid.arrows, arrow] }, rough.generator());
  const labels = svg.match(/<text [^>]*>/g);
  assert.equal(labels.length, 52);
  for (const t of labels) assert.match(t, / fill="#2F2E2C" stroke="#F9F5EB" stroke-width="4" stroke-linejoin="round" paint-order="stroke">$/);
  assert.ok(svg.lastIndexOf("data-arrow") < svg.indexOf("<text"));
  assert.match(readFileSync(new URL("../src/asbuilt/page/index.html", import.meta.url), "utf8"), /background: #F9F5EB;/);
});

test("a redraw is byte-identical, and a box looks the same wherever it sits in the list", () => {
  const first = draw(laid, rough.generator());
  assert.equal(draw(laid, rough.generator()), first);
  const again = draw({ ...laid, boxes: [...laid.boxes].reverse() }, rough.generator());
  for (const attr of ["box", "wash"]) assert.deepEqual(pieces(again, attr), pieces(first, attr));
});

test("a note is drawn under its box's label in smaller type, escaped; a box without one gets one line", () => {
  const note = '<b>"n"</b>';
  const svg = draw({ width: 60, height: 30, boxes: [{ ...one("a"), note }, one("c")], arrows: [] }, rough.generator());
  const texts = [...svg.matchAll(/<text x="([^"]*)" y="([^"]*)"[^>]*font-size="(\d+)"[^>]*>(.*?)<\/text>/g)].map((m) => m.slice(1));
  assert.deepEqual(texts.map(([x, y, size, t]) => [x, y, size, decode(t)]), [["25", "22", "13", "a"], ["25", "40", "11", note], ["25", "22", "13", "c"]]);
  assert.match(texts[1][3], escaped);
});

test("ids and labels are escaped, whatever they hold, and decode back to exactly what was given", () => {
  for (const evil of evils) {
    const svg = draw(small(evil), rough.generator());
    const found = [...Object.keys(pieces(svg, "box")), ...Object.keys(pieces(svg, "wash")), svg.match(/<text[^>]*>(.*?)<\/text>/)[1]];
    assert.equal(found.length, 3);
    for (const f of found) {
      assert.match(f, escaped, evil);
      assert.equal(decode(f), evil);
    }
  }
});

test("an arrow's label is drawn where layout put it in the note's type, escaped; no label, no text", () => {
  const evil = '<b>"n"</b>';
  const line = (id, label) => ({ id, points: [[0, 0], [0, 30]], label });
  const svg = draw({ width: 60, height: 30, boxes: [], arrows: [line("e", { x: 4, y: 6, w: 40, text: evil }), line("f")] }, rough.generator());
  const texts = [...svg.matchAll(/<text x="([^"]*)" y="([^"]*)"[^>]*font-size="(\d+)"[^>]*>(.*?)<\/text>/g)].map((m) => m.slice(1));
  assert.deepEqual(texts.map(([x, y, size, t]) => [x, y, size, decode(t)]), [["24", "18", "11", evil]]);
  assert.match(texts[0][3], escaped);
});
