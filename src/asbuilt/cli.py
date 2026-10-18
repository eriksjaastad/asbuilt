import argparse
import contextlib
import sys
import threading
from pathlib import Path

from asbuilt import plan
from asbuilt.hub import Hub
from asbuilt.server import Server
from asbuilt.state import build
from asbuilt.watch import watch


class Stop(threading.Event):  # watch() waits right after its first look: redraw then
    def __init__(self, refresh):
        super().__init__()
        self._refresh = refresh

    def wait(self, timeout=None):
        refresh, self._refresh = self._refresh, None
        if refresh:
            refresh()
        return super().wait(timeout)


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
    hub = Hub(lambda: build(lambda: plan.read(args.plan)))
    try:
        server = Server(args.port, hub)
    except (OSError, OverflowError) as exc:  # in use, no permission, out of range
        print(f"asbuilt: cannot start on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    stop = Stop(hub.refresh)
    threading.Thread(
        target=watch, args=(args.plan, hub.refresh, stop), daemon=True
    ).start()
    print(f"asbuilt: drawing {args.plan} at http://127.0.0.1:{server.server_port}/")
    with server, contextlib.suppress(KeyboardInterrupt):  # leaving closes the socket
        server.serve_forever()
    stop.set()
    return 0
