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

test("every label sits on a paper halo drawn under its letters, so an arrow under it cannot hide it", () => {
  const svg = draw(laid, rough.generator());
  const labels = svg.match(/<text [^>]*>/g);
  assert.equal(labels.length, 49);
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
