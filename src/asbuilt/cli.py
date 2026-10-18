import argparse
import contextlib
import sys
from pathlib import Path

from asbuilt import plan
from asbuilt.hub import Hub
from asbuilt.server import Server
from asbuilt.state import build


def main(argv=None):
    """Serve the plan file as a chart on this machine until interrupted."""
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
    try:
        server = Server(args.port, Hub(lambda: build(lambda: plan.read(args.plan))))
    except (OSError, OverflowError) as exc:  # in use, no permission, out of range
        print(f"asbuilt: cannot start on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    print(f"asbuilt: drawing {args.plan} at http://127.0.0.1:{server.server_port}/")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0
