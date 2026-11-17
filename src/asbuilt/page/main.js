// The page: show each message the server sends, newest only. ELK and rough are globals from index.html's scripts.
// A click on a box opens that box's code beside the chart. The chart stays; the panel is not part of the SVG.
import { boxAt, find, html } from "./code.js";
import { latest } from "./latest.js";
import { carries, markCounts, showing, shown } from "./marks.js";
import { pipeline } from "./pipeline.js";
import { keepLastGood, render } from "./render.js";
import { esc } from "./shapes.js";

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

const focusId = () => (focus && plan && find(plan, focus) ? focus : null);

const basePlan = () => (focusId() ? pipeline(plan, focusId()) : plan);

const picked = () => shown(plan, focusId(), [...chosen]);

function sayHidden() {
  const tally = markCounts(plan ? basePlan() : null);
  for (const button of markButtons) button.textContent = `${button.dataset.mark} ${tally[button.dataset.mark] ?? 0}`;
  if (!chosen.size || !plan) {
    marksNote.textContent = "";
    return;
  }
  const drawn = picked();
  const reached = focusId() ? (find(plan, focusId()).labels?.[0]?.text ?? focusId()) : null;
  if (drawn && !drawn.children?.length) {
    marksNote.textContent = carries([...chosen], reached);
    return;
  }
  const [full, kept] = [listed(basePlan()), listed(drawn ?? basePlan())];
  const boxes = full.boxes.filter((id) => !kept.boxes.includes(id)).length;
  const arrows = full.arrows.filter((id) => !kept.arrows.includes(id)).length;
  marksNote.textContent = showing([...chosen], boxes, arrows);
}

function showCode() {
  const box = plan && open ? find(plan, open) : null;
  const body = html(box);
  source.innerHTML = body;
  // Whole chart sits in the toolbar, so the panel closes when it has no code to show.
  code.hidden = body === "";
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
  const draw = picked();
  if (chosen.size && draw && !draw.children?.length) {
    const reached = focusId() ? (find(plan, focusId()).labels?.[0]?.text ?? focusId()) : null;
    return `<p class="empty">${esc(carries([...chosen], reached))}</p>`;
  }
  return render(text, elk, gen, draw);
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
