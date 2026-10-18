// One message from the server becomes what the page shows: the chart, or an error panel. It never throws, so a
// bad message can never leave the page blank or showing an older chart (D-012). ELK and rough come in as arguments.
import { draw } from "./draw.js";
import { layout } from "./layout.js";
import { esc } from "./shapes.js";

export const panel = (text) => `<pre class="error">${esc(text)}</pre>`;

export async function render(text, elk, gen) {
  try {
    const state = JSON.parse(text);
    if (state?.ok === true) return draw(await layout(state.plan, elk), gen);
    return panel(state?.error ?? "The server sent a message the page cannot read.");
  } catch (error) {
    return panel(error?.message ?? error);
  }
}
