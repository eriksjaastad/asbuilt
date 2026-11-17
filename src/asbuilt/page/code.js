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

// A short name for one security fact, from the fields security.py stored with it. Each matches
// what that fact's Plain sentence already says, and claims nothing more.
export function titled(fact) {
  if (fact?.kind === "input") return "Reads what a person types";
  if (fact?.kind === "env") {
    if (fact.how === "check") return "Checks an environment setting";
    if (["remove", "default", "set", "write"].includes(fact.how)) return "Changes an environment setting";
    return "Reads an environment setting";
  }
  if (fact?.kind === "calls out") {
    if (fact.way === "command") return "Runs a program outside this one";
    if (fact.way === "connect") return "Opens a network connection";
    return "Makes a network request";
  }
  return "Something to know";
}

export function html(box) {
  const info = box?.asbuilt;
  if (typeof info?.text !== "string") return "";
  const proven = info.proven ? `<p class="proven">${esc(info.proven)}</p>` : "";
  const plain = (Array.isArray(info.plain) ? info.plain : [])
    .filter((line) => typeof line === "string")
    .map((line) => `<p class="plain">${esc(line)}</p>`)
    .join("");
  const facts = (Array.isArray(info.security) ? info.security : [])
    .filter((note) => note && typeof note.plain === "string" && typeof note.expert === "string");
  const security = facts.length
    ? `<p class="security-head">Security: ${facts.length} ${facts.length === 1 ? "thing" : "things"} to know</p>` +
      facts.map((note, i) => `<p class="fact">${i + 1}. ${esc(titled(note.fact))}</p>` +
        `<p class="security"><b>Plain (everyday words):</b> ${esc(note.plain)}</p>` +
        `<p class="security"><b>Expert (the code):</b> ${esc(note.expert)}</p>`).join("")
    : "";
  const model = (Array.isArray(info.model) ? info.model : [])
    .filter((note) => note && typeof note.text === "string")
    .map((note) => `<p class="model"><b>model</b> ${esc(note.label)}: ${esc(note.text)}</p>`)
    .join("");
  const rest = info.rest ? `<p class="rest">${esc(info.rest)}</p>` : "";
  return `<p class="name">${esc(box.labels?.[0]?.text ?? box.id)}</p>${proven}${plain}${security}${model}<pre>${esc(info.text)}</pre>${rest}`;
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
