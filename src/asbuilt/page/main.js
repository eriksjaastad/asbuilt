// The page: show each message the server sends, newest only. ELK and rough are globals from index.html's scripts.
// A click on a box opens that box's code beside the chart. The chart stays; the panel is not part of the SVG.
import { boxAt, find, html } from "./code.js";
import { latest } from "./latest.js";
import { carries, hiddenBoxes, looked, markCounts, SAYS, showing, shown } from "./marks.js";
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

// One reading of the current view: the pipeline when one is on, then the chosen marks.
// The empty line lives here so the chart and the toolbar cannot each decide it.
function picture() {
  const id = focusId();
  const base = id ? pipeline(plan, id) : plan;
  const draw = shown(plan, id, [...chosen]);
  const empty = Boolean(chosen.size && draw && !draw.children?.length);
  const reached = id ? (find(plan, id).labels?.[0]?.text ?? id) : null;
  return { base, draw, empty, line: empty ? carries([...chosen], reached) : "" };
}

function sayHidden() {
  const view = picture();
  const tally = markCounts(view.base);
  for (const button of markButtons) {
    const count = tally[button.dataset.mark] ?? 0;
    button.textContent = `${SAYS[button.dataset.mark]} (${count})`;
    // A chosen mark stays clickable at 0, so it can still be turned off.
    button.disabled = count === 0 && !chosen.has(button.dataset.mark);
    button.title = button.disabled ? `asBuilt found none in this view. ${looked([button.dataset.mark])}` : "";
  }
  // The chart says the empty sentence. The toolbar line is only for a chart that still has boxes.
  if (!chosen.size || !plan || view.empty) {
    marksNote.textContent = "";
    return;
  }
  const [full, kept] = [listed(view.base), listed(view.draw)];
  const hidden = hiddenBoxes(view.base, kept.boxes);
  const arrows = full.arrows.filter((id) => !kept.arrows.includes(id)).length;
  marksNote.textContent = showing([...chosen], hidden, arrows);
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
  let ok = false;
  try {
    const state = JSON.parse(text);
    ok = state?.ok === true;
    if (ok) plan = state.plan;
  } catch {
    // render turns a message it cannot read into the error panel
  }
  if (focus && !find(plan, focus)) focus = null;
  const view = picture();
  // An empty mark is a calm chart only for a plan the server accepted. An error, or a lost
  // connection, has to win, or the empty line hides it and becomes the last good chart.
  if (ok && view.empty) return `<p class="empty">${esc(view.line)}</p>`;
  return render(text, elk, gen, ok ? view.draw : null);
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
