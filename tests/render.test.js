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

test("main validates state.plan before committing plan/looks or returning empty-mark view, preserving last-good state on invalid plans", async () => {
  const makeEl = (id, mark) => {
    const attrs = new Map();
    const listeners = new Map();
    const el = {
      id,
      hidden: false,
      textContent: "",
      innerHTML: "",
      title: "",
      dataset: mark ? { mark } : {},
      setAttribute: (k, v) => attrs.set(k, v),
      getAttribute: (k) => attrs.get(k) ?? null,
      addEventListener: (type, fn) => listeners.set(type, fn),
      closest: (sel) => (sel.includes(id) || (mark && sel.includes("[data-mark]")) ? el : null),
      trigger: (type, event = { target: el }) => listeners.get(type)?.(event),
    };
    return el;
  };

  const els = new Map(["chart", "code", "source", "pipeline", "whole", "mark-all", "marks-note", "toolbar"].map((id) => [id, makeEl(id)]));
  const markBtns = ["env", "input", "writes", "calls out", "model"].map((m) => makeEl(`mark-${m}`, m));
  let eventSource = null;

  const saved = { ELK: globalThis.ELK, rough: globalThis.rough, document: globalThis.document, EventSource: globalThis.EventSource };
  globalThis.ELK = ELK;
  globalThis.rough = rough;
  globalThis.document = {
    getElementById: (id) => els.get(id),
    querySelectorAll: () => markBtns,
  };
  globalThis.EventSource = class {
    constructor() { eventSource = this; }
  };

  try {
    await import("../src/asbuilt/page/main.js");
    const flush = async () => {
      for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 15));
    };

    const goodPlan = {
      id: "r",
      children: [
        { ...leaf("a"), asbuilt: { text: "def a(): pass", badges: ["env"] } },
        { ...leaf(""), asbuilt: { text: "def empty(): pass" } },
      ],
    };
    eventSource.onmessage({ data: JSON.stringify({ ok: true, plan: goodPlan, looks: { env: ["os.getenv"] } }) });
    await flush();
    const chart = els.get("chart");
    assert.match(chart.innerHTML, /^<svg /);
    assert.match(markBtns[0].textContent, /\(1\)$/);

    // Select a mark ("writes") with 0 matches: valid plan shows calm empty view.
    markBtns[2].setAttribute("aria-disabled", "false");
    els.get("toolbar").trigger("click", { target: markBtns[2] });
    await flush();
    assert.match(chart.innerHTML, /^<p class="empty">/);

    // Now send a structurally invalid plan (valid JSON, ok: true, but children is not a list):
    // must NOT show <p class="empty">, must show error above Out of date last-good view, and preserve goodPlan.
    eventSource.onmessage({ data: JSON.stringify({ ok: true, plan: { id: "r", children: "bad" } }) });
    await flush();
    assert.match(chart.innerHTML, /^<pre class="error">.*children is not a list.*<\/pre><div class="stale"><p>Out of date:/s);
    assert.match(markBtns[0].textContent, /\(1\)$/, "previous valid plan is preserved for toolbar counts");
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete globalThis[k];
      else globalThis[k] = v;
    }
  }
});

test("Whole chart button hidden state tracks pipeline focus, survives a line move, and clears on ambiguity or click", async () => {
  const makeEl = (id, mark) => {
    const attrs = new Map();
    const listeners = new Map();
    const el = {
      id,
      hidden: id === "whole" || id === "pipeline" || id === "code",
      textContent: "",
      innerHTML: "",
      title: "",
      dataset: mark ? { mark } : {},
      setAttribute: (k, v) => attrs.set(k, v),
      getAttribute: (k) => attrs.get(k) ?? null,
      addEventListener: (type, fn) => listeners.set(type, fn),
      closest: (sel) => (sel.includes(id) || (mark && sel.includes("[data-mark]")) ? el : null),
      trigger: (type, event = { target: el }) => listeners.get(type)?.(event),
    };
    return el;
  };

  const els = new Map(["chart", "code", "source", "pipeline", "whole", "mark-all", "marks-note", "toolbar"].map((id) => [id, makeEl(id)]));
  const markBtns = ["env", "input", "writes", "calls out", "model"].map((m) => makeEl(`mark-${m}`, m));
  let eventSource = null;

  const saved = { ELK: globalThis.ELK, rough: globalThis.rough, document: globalThis.document, EventSource: globalThis.EventSource };
  globalThis.ELK = ELK;
  globalThis.rough = rough;
  globalThis.document = {
    getElementById: (id) => els.get(id),
    querySelectorAll: () => markBtns,
  };
  globalThis.EventSource = class {
    constructor() { eventSource = this; }
  };

  const clickBox = (boxId) => {
    const g = {
      getAttribute: (attr) => (attr === "data-box" ? boxId : null),
      getBBox: () => ({ x: 0, y: 0, width: 100, height: 40 }),
    };
    const svg = {
      getScreenCTM: () => ({ inverse: () => ({}) }),
      createSVGPoint: () => ({ x: 0, y: 0, matrixTransform: () => ({ x: 10, y: 10 }) }),
      querySelectorAll: () => [g],
    };
    els.get("chart").trigger("click", {
      clientX: 10,
      clientY: 10,
      target: { closest: (sel) => (sel === "svg" ? svg : null) },
    });
  };

  try {
    await import("../src/asbuilt/page/main.js?case=whole-and-line-move");
    const flush = async () => {
      for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 15));
    };

    const makePlan = (runId, stepId, asideId, extra = []) => ({
      id: "r",
      children: [
        { ...leaf(runId), asbuilt: { text: "def run(): pass" } },
        leaf(stepId),
        leaf(asideId),
        ...extra,
      ],
      edges: [{ id: "e1", sources: [runId], targets: [stepId] }],
    });

    const chart = els.get("chart");
    const whole = els.get("whole");
    const pipelineBtn = els.get("pipeline");

    // 1. Initial load: Whole chart button is hidden.
    eventSource.onmessage({ data: JSON.stringify({ ok: true, plan: makePlan("app.py:run:10", "app.py:step:20", "app.py:aside:30") }) });
    await flush();
    assert.equal(whole.hidden, true, "Whole chart button starts hidden when no filter is active");
    assert.match(chart.innerHTML, /data-box="app\.py:aside:30"/);

    // 2. Selecting a box opens code panel, shows pipeline button, keeps Whole chart hidden.
    clickBox("app.py:run:10");
    assert.equal(pipelineBtn.hidden, false);
    assert.equal(whole.hidden, true, "Whole chart button stays hidden until pipeline filter is activated");

    // 3. Activating pipeline filter shows Whole chart button and filters out aside.
    pipelineBtn.trigger("click");
    await flush();
    assert.equal(whole.hidden, false, "Whole chart button is visible while pipeline filter is active");
    assert.doesNotMatch(chart.innerHTML, /data-box="app\.py:aside:30"/);
    assert.match(chart.innerHTML, /data-box="app\.py:step:20"/);

    // 4. Reload where focused box moved lines (10 -> 18): focus survives and Whole chart stays visible.
    eventSource.onmessage({ data: JSON.stringify({ ok: true, plan: makePlan("app.py:run:18", "app.py:step:28", "app.py:aside:38") }) });
    await flush();
    assert.equal(whole.hidden, false, "Whole chart button stays visible when focused box moves to a new line");
    assert.doesNotMatch(chart.innerHTML, /data-box="app\.py:aside:38"/);
    assert.match(chart.innerHTML, /data-box="app\.py:run:18"/);
    assert.match(chart.innerHTML, /data-box="app\.py:step:28"/);

    // 5. Reload where two boxes match app.py:run (ambiguous): focus clears and Whole chart hides.
    eventSource.onmessage({
      data: JSON.stringify({
        ok: true,
        plan: makePlan("app.py:run:22", "app.py:step:32", "app.py:aside:42", [leaf("app.py:run:55")]),
      }),
    });
    await flush();
    assert.equal(whole.hidden, true, "Whole chart button hides when reload match is ambiguous");
    assert.match(chart.innerHTML, /data-box="app\.py:aside:42"/);

    // 6. Re-focus a box, then click Whole chart: hides Whole chart and restores full chart.
    clickBox("app.py:run:22");
    pipelineBtn.trigger("click");
    await flush();
    assert.equal(whole.hidden, false);
    assert.doesNotMatch(chart.innerHTML, /data-box="app\.py:aside:42"/);

    whole.trigger("click");
    await flush();
    assert.equal(whole.hidden, true, "Whole chart button hides after clicking Whole chart");
    assert.match(chart.innerHTML, /data-box="app\.py:aside:42"/);
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete globalThis[k];
      else globalThis[k] = v;
    }
  }
});
