// Plans the tests draw: small builders and a generated 49-box plan (7 groups of 6 boxes, 35 arrows inside the
// groups and 6 more from each group to the next).
export const leaf = (id) => ({ id, width: 40, height: 20 });
export const arrow = (id, a, b) => ({ id, sources: [a], targets: [b] });
const range = (n) => Array.from({ length: n }, (_, i) => i);
const group = (g) => ({
  id: `g${g}`,
  children: range(6).map((i) => leaf(`g${g}n${i}`)),
  edges: range(5).map((i) => arrow(`g${g}e${i}`, `g${g}n${i}`, `g${g}n${i + 1}`)),
});
export const big = { id: "r", children: range(7).map(group), edges: range(6).map((g) => arrow(`x${g}`, `g${g}n0`, `g${g + 1}n0`)) };
