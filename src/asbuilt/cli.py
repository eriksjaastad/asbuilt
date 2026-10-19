import argparse
import contextlib
import sys
import threading
import traceback
from pathlib import Path

from asbuilt import plan
from asbuilt.hub import Hub
from asbuilt.server import Server
from asbuilt.state import build
from asbuilt.watch import snapshot, watch


def main(argv=None):
    """Serve the plan file as a chart on this machine, redrawn when it is saved, until interrupted."""
    parser = argparse.ArgumentParser("asbuilt")
    parser.add_argument(
        "plan",
        nargs="?",
        type=Path,
        default=Path("asbuilt/plan.json"),
        help="default %(default)s",
    )
    parser.add_argument("--port", type=int, default=8765, help="default %(default)s")
    args = parser.parse_args(argv)
    # The page is drawn from the snapshot the watch settled on, never from a reread.
    shown = [snapshot(args.plan)]
    hub = Hub(lambda: build(lambda: plan.parse_snapshot(shown[0], args.plan.name)))
    try:
        server = Server(args.port, hub)
    except (OSError, OverflowError) as exc:  # in use, no permission, out of range
        print(f"asbuilt: cannot start on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
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
    print(f"asbuilt: drawing {args.plan} at http://127.0.0.1:{server.server_port}/")
    with server, contextlib.suppress(KeyboardInterrupt):  # leaving closes the socket
        server.serve_forever()
    stop.set()
    if failed:
        print(f"asbuilt: stopped, the watch of {args.plan} failed:", file=sys.stderr)
        traceback.print_exception(failed[0])
        return 1
    return 0
