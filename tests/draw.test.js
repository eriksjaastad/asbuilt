import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import rough from "roughjs/bundled/rough.esm.js";
import { draw } from "../src/asbuilt/page/draw.js";
import { MARKS } from "../src/asbuilt/page/shapes.js";
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

test("an unmarked plan draws byte for byte as before marks existed, and so do planned and unknown marks", () => {
  const svg = draw(laid, rough.generator());
  assert.equal(createHash("sha256").update(svg).digest("hex"), "ec9a518dc03484b458c305a4b9b196f2d3e82e1cbd45b7dd260fd5dbd30bd15e");
  for (const mark of ["planned", "bogus", "constructor", "toString", 7, null]) {
    const marked = { ...laid, boxes: laid.boxes.map((b) => ({ ...b, mark })), arrows: laid.arrows.map((a) => ({ ...a, mark })) };
    assert.equal(draw(marked, rough.generator()), svg, String(mark));
  }
});

test("each mark draws its hue, dash, fade and tag on a box, its wash and an arrow; the tag is escaped text", () => {
  for (const [mark, cue] of Object.entries(MARKS)) {
    const box = { ...one("b"), x: 10, y: 30, mark };
    const line = { id: "e", points: [[0, 0], [0, 40], [20, 40]], mark };
    const svg = draw({ width: 60, height: 80, boxes: [box], arrows: [line] }, rough.generator());
    const attrs = (cue.dash ? ` stroke-dasharray="${cue.dash}"` : "") + (cue.fade < 1 ? ` opacity="${cue.fade}"` : "");
    for (const g of ["box", "wash", "arrow"]) assert.ok(svg.includes(`<g data-${g}="${g === "arrow" ? "e" : "b"}"${attrs}>`), `${mark} ${g}`);
    const strokes = (g) => [...svg.match(new RegExp(`<g data-${g}=[^>]*>(.*?)</g>`))[1].matchAll(/stroke="([^"]*)"/g)].map((m) => m[1]);
    for (const g of ["box", "arrow"]) assert.deepEqual([...new Set(strokes(g))], [cue.hue], `${mark} ${g}`);
    const tags = [...svg.matchAll(/<text x="([^"]*)" y="([^"]*)" text-anchor="(\w+)"[^>]*font-size="10" fill="([^"]*)"[^>]*>(.*?)<\/text>/g)].map((m) => m.slice(1));
    assert.deepEqual(tags, [["60", "25", "end", cue.hue, cue.tag], ["16", "44", "start", cue.hue, cue.tag]], mark);
  }
  assert.deepEqual(new Set(Object.values(MARKS).map((c) => c.dash + c.tag)).size, 3); // never colour alone
});
