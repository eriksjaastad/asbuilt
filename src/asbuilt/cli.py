import argparse
import contextlib
import json
import sys

from asbuilt.hub import Hub
from asbuilt.server import Server

# until a plan source is connected the page shows this error panel, never a blank chart
NO_PLAN = json.dumps({"ok": False, "error": "No plan is connected yet."})


def main(argv=None, build=lambda: NO_PLAN):
    """Serve the chart page until interrupted; `build` returns the one-line message it shows."""
    parser = argparse.ArgumentParser("asbuilt")
    parser.add_argument("--port", type=int, default=8765, help="default %(default)s")
    args = parser.parse_args(argv)
    try:
        server = Server(args.port, Hub(build))
    except (OSError, OverflowError) as exc:  # in use, no permission, out of range
        print(f"asbuilt: cannot start on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    print(f"asbuilt: serving at http://127.0.0.1:{server.server_port}/")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0
