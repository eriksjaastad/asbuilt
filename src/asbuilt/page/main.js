// The page: show each message the server sends, newest only. ELK and rough are globals from index.html's scripts.
import { latest } from "./latest.js";
import { render } from "./render.js";

const [elk, gen] = [new ELK(), rough.generator()];
const show = latest((text) => render(text, elk, gen), (html) => {
  document.getElementById("chart").innerHTML = html;
});
const events = new EventSource("/events");
events.onmessage = (event) => show(event.data);
events.onerror = () => show(JSON.stringify({ ok: false, error: "Lost the connection to asbuilt. Trying again." }));
