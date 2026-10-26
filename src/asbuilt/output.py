"""One place for what a command writes and how it fails (for compare and draft)."""

import contextlib
import json
import os
import sys
import tempfile

from asbuilt.plan import MAX_BYTES
from asbuilt.state import PlanError


def plan_bytes(plan, name):
    """`plan` as the bytes of a plan file asbuilt reads back, or a PlanError naming the file; nothing is opened."""
    try:  # each is a ValueError or RecursionError: a lone surrogate, NaN, inf, too many digits, too deep
        raw = (
            json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode()
    except (ValueError, RecursionError):
        why = "text that is not Unicode, NaN, an infinity, a number too long or nesting too deep"
        raise PlanError(f"{name} cannot be written: it holds {why}.") from None
    if len(raw) > MAX_BYTES:  # the reader's own limit, so what is written can be read
        raise PlanError(f"{name} cannot be written: over {MAX_BYTES} bytes.")
    return raw


def write_atomic(path, data):
    """Put `data` at `path` (a link is followed) whole or not at all: an existing file is replaced in one step or left as it was."""
    real = os.path.realpath(path)
    tmp = None
    try:  # in the same folder, so os.replace is one step; mkstemp's 0600 becomes the umask's mode, as open() gives
        fd, tmp = tempfile.mkstemp(".tmp", dir=os.path.dirname(real))
        os.umask(mask := os.umask(0o22))
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), 0o666 & ~mask)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, real)
    except OSError as exc:
        why = exc.strerror or type(exc).__name__
        raise PlanError(f"{path.name} cannot be written: {why}.") from None
    finally:  # also on Ctrl-C; after os.replace there is nothing left to remove
        if tmp is not None:
            try:
                with contextlib.suppress(FileNotFoundError):  # os.replace moved it
                    os.unlink(tmp)
            except OSError as exc:  # left behind: say so, with its name
                why = f"its temporary file {tmp} could not be removed: {exc.strerror}"
                raise PlanError(f"{path.name} cannot be written: {why}.") from None


def fail(prog, exc):
    """Print `prog: <exc>` to stderr as one line (each line break in the whole message becomes a space); return 2."""
    print(f"{prog}: {' '.join(str(exc).splitlines())}", file=sys.stderr)
    return 2
