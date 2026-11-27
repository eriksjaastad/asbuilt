import argparse
import contextlib
import errno
import sys
import threading
import traceback
from pathlib import Path

from asbuilt import overview, plan
from asbuilt.hub import Hub
from asbuilt.server import OverviewServer, Server
from asbuilt.state import PlanError, build
from asbuilt.watch import snapshot, watch

USAGE = """\
Open the address it prints. Saving the file redraws the page. If the file is
missing or cannot be drawn, the page says why, and the chart comes back when
the file is fixed. Stop with Ctrl-C.

The plan is ELK JSON (eclipse.org/elk, "JSON format"): boxes inside boxes, and
arrows between them. A box shows its first label; a second label, if any, is
drawn under it in smaller type, for a short note on what the box does. Point
each arrow the way the work goes, from the step that runs first to the one
after it: the chart reads top to bottom along the arrows, and it is never wider
than a laptop screen (1440 px): a plan too wide for that grows downward. An
arrow's label is drawn beside it, for what starts that step (a schedule, a click on a link).
For example:

  {"id": "root",
   "children": [
     {"id": "read_plan", "labels": [{"text": "read_plan()"},
                                    {"text": "reads the plan file"}]},
     {"id": "draw_chart", "labels": [{"text": "draw_chart()"}]}],
   "edges": [{"id": "e1", "sources": ["read_plan"], "targets": ["draw_chart"],
              "labels": [{"text": "on each save"}]}]}
"""


def _report_bind_error(port, exc):
    why = exc
    # Taken is usually the default port, which whoever started asbuilt never chose: say what to do.
    if getattr(exc, "errno", None) == errno.EADDRINUSE:
        why = "it is in use, perhaps by asbuilt already running. "
        why += "Stop that one, or choose another port with --port N."
    print(f"asbuilt: cannot start on 127.0.0.1:{port}: {why}", file=sys.stderr)
    return 2


def _serve_overview(project_dir, port):
    """Serve the folder's overview at the printed address, as a plan is served, until interrupted."""
    try:
        payload = overview.build(project_dir)
    except (PlanError, OSError) as exc:
        print(f"asbuilt: {exc}", file=sys.stderr)
        return 2
    for line in overview.problems(payload):
        print(f"asbuilt: {line}", file=sys.stderr)
    try:
        server = OverviewServer(port, payload)
    except (OSError, OverflowError) as exc:
        return _report_bind_error(port, exc)
    print(
        f"asbuilt: showing {project_dir} at http://127.0.0.1:{server.server_port}/",
        flush=True,
    )
    with server, contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


def main(argv=None):
    """Serve the plan file as a chart on this machine, redrawn when it is saved, or a folder's overview, until interrupted."""
    parser = argparse.ArgumentParser(
        "asbuilt",
        description="Draw a plan file as a chart in the browser, or open the component overview of a project folder, on this machine only.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "plan",
        nargs="?",
        type=Path,
        default=Path("asbuilt/plan.json"),
        help="a plan file, or a project folder for its component overview (default %(default)s)",
    )
    parser.add_argument("--port", type=int, default=8765, help="default %(default)s")
    args = parser.parse_args(argv)
    if args.plan.is_dir():
        return _serve_overview(args.plan, args.port)
    # The page is drawn from the snapshot the watch settled on, never from a reread.
    shown = [snapshot(args.plan)]
    hub = Hub(lambda: build(lambda: plan.parse_snapshot(shown[0], args.plan.name)))
    try:
        server = Server(args.port, hub)
    except (OSError, OverflowError) as exc:  # in use, no permission, out of range
        return _report_bind_error(args.port, exc)
    stop, failed = threading.Event(), []

    def changed(now):
        shown[0] = now
        hub.refresh()

    def watching():
        try:
            watch(args.plan, changed, stop, shown[0])
        except Exception as exc:  # a dead watch ends the command, not a stale page
            failed.append(exc)
            server.shutdown()

    threading.Thread(target=watching, daemon=True).start()
    # flush: piped or captured output is block-buffered, and the address must show before serving starts.
    print(
        f"asbuilt: drawing {args.plan} at http://127.0.0.1:{server.server_port}/",
        flush=True,
    )
    with server, contextlib.suppress(KeyboardInterrupt):  # leaving closes the socket
        server.serve_forever()
    stop.set()
    if failed:
        print(f"asbuilt: stopped, the watch of {args.plan} failed:", file=sys.stderr)
        traceback.print_exception(failed[0])
        return 1
    return 0
