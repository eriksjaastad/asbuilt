// work(state) is async (layout takes time), so an older state can finish after a newer one has started.
// Run work for every state that arrives, in arrival order, and show only the newest one's result.
// Arrival order, not a number from the server: a restarted server counts from 1 again.
// work should turn its own failures into a result to show: if it throws, the error goes to that call's caller.
export function latest(work, show) {
  let newest = 0;
  return async (state) => {
    const mine = ++newest;
    const result = await work(state);
    if (mine === newest) show(result);
  };
}
