// Code beside the chart. Letters sit above the box in the SVG, so a click is matched by position,
// and the smallest box under the pointer wins when boxes are nested.
import { esc } from "./shapes.js";

export function find(plan, id) {
  const todo = [plan];
  for (const box of todo) {
    if (box?.id === id) return box;
    todo.push(...(box?.children ?? []));
  }
  return null;
}

export function html(box) {
  const info = box?.asbuilt;
  if (typeof info?.text !== "string") return "";
  const proven = info.proven ? `<p class="proven">${esc(info.proven)}</p>` : "";
  // Plain is the only level written so far. The sentence is the parser's plain line.
  // The expert line stays on the plan and is not shown.
  const security = (Array.isArray(info.security) ? info.security : [])
    .filter((note) => note && typeof note.plain === "string")
    .map((note) => `<p class="security">${esc(note.plain)}</p>`)
    .join("");
  const model = (Array.isArray(info.model) ? info.model : [])
    .filter((note) => note && typeof note.text === "string")
    .map((note) => `<p class="model"><b>model</b> ${esc(note.label)}: ${esc(note.text)}</p>`)
    .join("");
  const rest = info.rest ? `<p class="rest">${esc(info.rest)}</p>` : "";
  return `<p class="name">${esc(box.labels?.[0]?.text ?? box.id)}</p>${proven}${security}${model}<pre>${esc(info.text)}</pre>${rest}`;
}

export function boxAt(event) {
  const svg = event.target?.closest?.("svg");
  if (!svg?.getScreenCTM()) return null;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const { x, y } = point.matrixTransform(svg.getScreenCTM().inverse());
  let [best, area] = [null, Infinity];
  for (const g of svg.querySelectorAll("[data-box]")) {
    const b = g.getBBox();
    const size = b.width * b.height;
    if (x >= b.x && x <= b.x + b.width && y >= b.y && y <= b.y + b.height && size < area) {
      [best, area] = [g.getAttribute("data-box"), size];
    }
  }
  return best;
}
