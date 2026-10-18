import ELK from "elkjs/lib/elk.bundled.js";
import { layout } from "../src/asbuilt/page/layout.js";
import { big } from "./plans.js";

export const laid = await layout(big, new ELK());
export const pieces = (svg, attr) =>
  Object.fromEntries([...svg.matchAll(new RegExp(`<g data-${attr}="([^"]*)">(.*?)</g>`, "g"))].map((m) => [m[1], m[2]]));
// Text that would break out of an attribute or a text node if it were not escaped.
export const evils = ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "a&b'c", 'x" onmouseover="alert(1)'];
export const decode = (t) => t.replace(/&#(\d+);/g, (_, n) => String.fromCharCode(n));
export const escaped = /^(?:[^<>"'&]|&#\d+;)*$/;
