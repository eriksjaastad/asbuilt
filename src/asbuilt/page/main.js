// The page: show each message the server sends, newest only. ELK and rough are globals from index.html's scripts.
// A click on a box opens that box's code beside the chart. The chart stays; the panel is not part of the SVG.
import { boxAt, find, html } from "./code.js";
import { latest } from "./latest.js";
import { pipeline } from "./pipeline.js";
import { keepLastGood, render } from "./render.js";

const [elk, gen, keep] = [new ELK(), rough.generator(), keepLastGood()];
const chart = document.getElementById("chart");
const code = document.getElementById("code");
const source = document.getElementById("source");
const pipelineBtn = document.getElementById("pipeline");
const whole = document.getElementById("whole");
let plan = null;
let open = null;
let focus = null;
let last = null;

function showCode() {
  const box = plan && open ? find(plan, open) : null;
  const body = html(box);
  source.innerHTML = body;
  // A file or folder has no lines of its own. The button still shows, above the code.
  // A filter keeps the panel up after the code is closed, so Whole chart stays reachable.
  code.hidden = body === "" && !box && !focus;
  pipelineBtn.hidden = !box;
  whole.hidden = !focus;
}

const show = latest(async (text) => {
  last = text;
  try {
    const state = JSON.parse(text);
    if (state?.ok === true) plan = state.plan;
  } catch {
    // render turns a message it cannot read into the error panel
  }
  if (focus && !find(plan, focus)) focus = null;
  return render(text, elk, gen, focus && plan ? pipeline(plan, focus) : null);
}, (svg) => {
  chart.innerHTML = keep(svg);
  showCode();
});

chart.addEventListener("click", (event) => {
  const id = boxAt(event);
  if (!id) return;
  open = open === id ? null : id;
  showCode();
});

pipelineBtn.addEventListener("click", () => {
  if (!open) return;
  focus = open;
  show(last);
});

whole.addEventListener("click", () => {
  focus = null;
  show(last);
});

const events = new EventSource("/events");
events.onmessage = (event) => show(event.data);
events.onerror = () => show(JSON.stringify({ ok: false, error: "Lost the connection to asbuilt. Trying again." }));
