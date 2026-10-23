import assert from "node:assert/strict";
import test from "node:test";
import ELK from "elkjs/lib/elk.bundled.js";
import rough from "roughjs/bundled/rough.esm.js";
import { keepLastGood, render } from "../src/asbuilt/page/render.js";
import { decode, escaped, evils } from "./helpers.js";
import { big, leaf } from "./plans.js";

const [elk, gen] = [new ELK(), rough.generator()];
const NOTE = "Out of date: this is the last good chart, from before the error above.";
const show = (state) => render(typeof state === "string" ? state : JSON.stringify(state), elk, gen);

test("a good plan becomes the chart", async () => {
  const html = await show({ ok: true, plan: big });
  assert.match(html, /^<svg /);
  assert.equal(html.split("<g data-box=").length, 50);
});

test("an error from the server becomes a panel holding its text, escaped, whatever it holds", async () => {
  for (const error of evils) {
    const html = await show({ ok: false, error });
    const [, inside] = html.match(/^<pre class="error">(.*)<\/pre>$/s);
    assert.match(inside, escaped, error);
    assert.equal(decode(inside), error);
  }
});

const WRONG = {
  "not JSON": ["{nope", /JSON/],
  "JSON that is not a message": ["null", /cannot read/],
  "a verdict that is not true or false": [{ ok: "yes", plan: big }, /cannot read/],
  "ok with no plan": [{ ok: true }, /plan: is not an object/],
  "a plan the page refuses": [{ ok: true, plan: { id: "r", children: [leaf("a"), leaf("a")] } }, /used twice/],
};
for (const [name, [state, reason]] of Object.entries(WRONG)) {
  test(`it shows a panel, not a throw or a blank: ${name}`, async () => {
    const html = await show(state);
    assert.match(html, /^<pre class="error">/);
    assert.match(html, reason);
  });
}

test("something thrown that is not an Error still shows its text", async () => {
  const elkThatRefuses = { layout: () => Promise.reject("plain text") };
  const html = await render(JSON.stringify({ ok: true, plan: big }), elkThatRefuses, gen);
  assert.equal(html, '<pre class="error">plain text</pre>');
});

test("an error keeps the last good chart under its panel, dimmed and marked; a good plan clears both", async () => {
  const keep = keepLastGood();
  const [chart, bad, worse] = [await show({ ok: true, plan: big }), await show("{nope"), await show({ ok: false, error: "x" })];
  const marked = (error, good) => `${error}<div class="stale"><p>${NOTE}</p>${good}</div>`;
  assert.equal(keep(bad), bad, "with no good chart yet, only the panel");
  assert.equal(keep(chart), chart);
  assert.equal(keep(bad), marked(bad, chart));
  assert.equal(keep(worse), marked(worse, chart), "a second error still keeps the good chart, not the first panel");
  const next = await show({ ok: true, plan: { id: "r", children: [leaf("a")] } });
  assert.equal(keep(next), next);
  assert.equal(keep(bad), marked(bad, next), "the newest good chart is the one kept");
});
