// The page: show each message the server sends, newest only. ELK and rough are globals from index.html's scripts.
// A click on a box opens that box's code beside the chart. The chart stays; the panel is not part of the SVG.
import { boxAt, find, html } from "./code.js";
import { latest } from "./latest.js";
import { keepLastGood, render } from "./render.js";

const [elk, gen, keep] = [new ELK(), rough.generator(), keepLastGood()];
const chart = document.getElementById("chart");
const code = document.getElementById("code");
let plan = null;
let open = null;

function showCode() {
  const body = html(plan && open ? find(plan, open) : null);
  code.hidden = body === "";
  code.innerHTML = body;
}

const show = latest(async (text) => {
  try {
    const state = JSON.parse(text);
    if (state?.ok === true) plan = state.plan;
  } catch {
    // render turns a message it cannot read into the error panel
  }
  return render(text, elk, gen);
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

const events = new EventSource("/events");
events.onmessage = (event) => show(event.data);
events.onerror = () => show(JSON.stringify({ ok: false, error: "Lost the connection to asbuilt. Trying again." }));
