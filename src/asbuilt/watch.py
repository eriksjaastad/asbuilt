from asbuilt.plan import read_bytes
from asbuilt.state import PlanError


def snapshot(path):
    """What the file holds now: its bytes, or the reason it cannot be read (so a deletion is a change too)."""
    try:
        return read_bytes(path)
    except PlanError as exc:
        return str(exc)


def watch(path, on_change, stop, tick=0.1):
    """Call on_change() once per change of contents, after it holds still for a tick.

    Not reported: the first look, a half-written save, an undone change. An error from on_change ends the watch.
    """
    reported = seen = snapshot(path)
    while not stop.wait(tick):
        now = snapshot(path)
        if now == seen and now != reported:
            reported = now
            on_change()
        seen = now
