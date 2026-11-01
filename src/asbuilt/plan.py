import json
import os
import stat

from asbuilt.state import PlanError

MAX_BYTES = 5_000_000


def read_bytes(path):
    """The plan file's bytes, or a PlanError naming the file and why not. O_NONBLOCK: a pipe must not hold us."""
    name = path.name
    try:
        with open(
            path, "rb", opener=lambda p, flags: os.open(p, flags | os.O_NONBLOCK)
        ) as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise PlanError(f"{name} is not a regular file.")
            raw = file.read(MAX_BYTES + 1)
    except FileNotFoundError:
        # Where it was looked for, in full: the page cannot see the folder the command was started in.
        raise PlanError(
            f"{name} was not found: {path.absolute()}. Save a plan there, or start asbuilt with its path."
        ) from None
    except OSError as exc:
        raise PlanError(
            f"{name} cannot be read: {exc.strerror or type(exc).__name__}."
        ) from None
    if len(raw) > MAX_BYTES:
        raise PlanError(f"{name} is over {MAX_BYTES} bytes.")
    return raw


def _object(pairs, name):
    seen = set()
    for key, _ in pairs:
        if key in seen:  # json would keep the last one and draw a quietly wrong chart
            raise PlanError(f"{name} repeats the key {key[:40]!r} in one object.")
        seen.add(key)
    return dict(pairs)


def parse(raw, name):
    """The JSON in `raw` (plain parsing; numbers are checked when the message is made), or a PlanError."""
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=lambda pairs: _object(pairs, name)
        )
    except json.JSONDecodeError as exc:
        raise PlanError(f"{name} is not valid JSON: {exc}.") from None
    except UnicodeDecodeError:
        raise PlanError(f"{name} is not UTF-8 text.") from None
    except RecursionError:
        raise PlanError(f"{name} is nested too deeply.") from None
    except ValueError:  # the one other: more digits than Python converts
        raise PlanError(f"{name} has a whole number with too many digits.") from None


def parse_snapshot(snap, name):
    """The plan in a watch snapshot (the file's bytes, or the reason it could not be read), or a PlanError."""
    if isinstance(snap, str):
        raise PlanError(snap)
    return parse(snap, name)
