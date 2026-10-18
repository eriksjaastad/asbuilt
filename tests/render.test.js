import assert from "node:assert/strict";
import test from "node:test";
import ELK from "elkjs/lib/elk.bundled.js";
import rough from "roughjs/bundled/rough.esm.js";
import { render } from "../src/asbuilt/page/render.js";
import { decode, escaped, evils } from "./helpers.js";
import { arrow, big, leaf } from "./plans.js";

const [elk, gen] = [new ELK(), rough.generator()];
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
  "a message with no verdict": [{ plan: big }, /cannot read/],
  "a verdict that is not true or false": [{ ok: "yes", plan: big }, /cannot read/],
  "ok with no plan": [{ ok: true }, /plan: is not an object/],
  "a plan the page refuses": [{ ok: true, plan: { id: "r", children: [leaf("a"), leaf("a")] } }, /used twice/],
  "an arrow ELK refuses": [{ ok: true, plan: { id: "r", children: [leaf("a")], edges: [arrow("e", "a", "zzz")] } }, /zzz/],
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
