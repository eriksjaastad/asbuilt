// The page: show each message the server sends, newest only. ELK and rough are globals from index.html's scripts.
// A click on a box opens that box's code beside the chart. The chart stays; the panel is not part of the SVG.
import { boxAt, find, html } from "./code.js";
import { latest } from "./latest.js";
import { byMarks } from "./marks.js";
import { pipeline } from "./pipeline.js";
import { keepLastGood, render } from "./render.js";

const [elk, gen, keep] = [new ELK(), rough.generator(), keepLastGood()];
const chart = document.getElementById("chart");
const code = document.getElementById("code");
const source = document.getElementById("source");
const pipelineBtn = document.getElementById("pipeline");
const whole = document.getElementById("whole");
const markAll = document.getElementById("mark-all");
const markButtons = [...document.querySelectorAll("#toolbar [data-mark]")];
const marksNote = document.getElementById("marks-note");
const chosen = new Set();
let plan = null;
let open = null;
let focus = null;
let last = null;

const listed = (box) => {
  const [boxes, arrows] = [[], []];
  const walk = (node, top) => {
    if (!top && node.id) boxes.push(node.id);
    for (const edge of node.edges ?? []) if (edge.id) arrows.push(edge.id);
    for (const child of node.children ?? []) walk(child, false);
  };
  walk(box, true);
  return { boxes, arrows };
};

const basePlan = () => (focus && plan && find(plan, focus) ? pipeline(plan, focus) : plan);

const view = () => {
  if (!plan) return null;
  const base = basePlan();
  const filtered = chosen.size ? byMarks(base, [...chosen]) : base;
  return filtered === plan ? null : filtered;
};

const hiddenLine = (one, many, count) => (count ? `${count} ${count === 1 ? one : many}` : "");

function sayHidden() {
  if (!chosen.size || !plan) {
    marksNote.textContent = "";
    return;
  }
  const [full, shown] = [listed(basePlan()), listed(byMarks(basePlan(), [...chosen]))];
  const boxes = full.boxes.filter((id) => !shown.boxes.includes(id)).length;
  const arrows = full.arrows.filter((id) => !shown.arrows.includes(id)).length;
  const parts = [hiddenLine("box", "boxes", boxes), hiddenLine("arrow", "arrows", arrows)].filter(Boolean);
  marksNote.textContent = parts.length ? `Hiding ${parts.join(" and ")}.` : "Hiding nothing.";
}

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
  return render(text, elk, gen, view());
}, (svg) => {
  chart.innerHTML = keep(svg);
  showCode();
  sayHidden();
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

document.getElementById("toolbar").addEventListener("click", (event) => {
  const button = event.target.closest("#mark-all, [data-mark]");
  if (!button || !last) return;
  if (button === markAll) chosen.clear();
  else if (chosen.has(button.dataset.mark)) chosen.delete(button.dataset.mark);
  else chosen.add(button.dataset.mark);
  markAll.setAttribute("aria-pressed", String(chosen.size === 0));
  for (const mark of markButtons) mark.setAttribute("aria-pressed", String(chosen.has(mark.dataset.mark)));
  show(last);
});

const events = new EventSource("/events");
events.onmessage = (event) => show(event.data);
events.onerror = () => show(JSON.stringify({ ok: false, error: "Lost the connection to asbuilt. Trying again." }));
