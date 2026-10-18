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
        raise PlanError(f"{name} was not found.") from None
    except OSError as exc:
        raise PlanError(
            f"{name} cannot be read: {exc.strerror or type(exc).__name__}."
        ) from None
    if len(raw) > MAX_BYTES:
        raise PlanError(f"{name} is over {MAX_BYTES} bytes.")
    return raw
