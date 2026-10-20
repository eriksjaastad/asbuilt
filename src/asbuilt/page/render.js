// One message from the server becomes what the page shows: the chart, or an error panel. It never throws, so a
// bad message can never leave the page blank (D-012). ELK and rough come in as arguments.
import { draw } from "./draw.js";
import { layout } from "./layout.js";
import { esc } from "./shapes.js";

const ERROR = '<pre class="error">';
export const panel = (text) => `${ERROR}${esc(text)}</pre>`;
const NOTE = "Out of date: this is the last good chart, from before the error above.";

// What render made becomes what the page shows. An error keeps the last good chart under its panel, dimmed and
// marked out of date, so a typo mid-edit does not blank the chart (D-016). With no good chart yet, only the panel.
export function keepLastGood() {
  let good = "";
  return (html) => {
    if (!html.startsWith(ERROR)) return (good = html);
    return good ? `${html}<div class="stale"><p>${NOTE}</p>${good}</div>` : html;
  };
}

export async function render(text, elk, gen) {
  try {
    const state = JSON.parse(text);
    if (state?.ok === true) return draw(await layout(state.plan, elk), gen);
    return panel(state?.error ?? "The server sent a message the page cannot read.");
  } catch (error) {
    return panel(error?.message ?? error);
  }
}
