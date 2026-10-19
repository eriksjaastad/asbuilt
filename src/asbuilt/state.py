import json

FALLBACK = "The plan could not be read."
NOT_SENDABLE = "The plan could not be sent to the page. Look for NaN, Infinity or a number too large to hold."


class PlanError(Exception):
    """A problem with the plan that its reader describes in words meant for the person."""


def _error(text):
    return json.dumps({"ok": False, "error": text})


def _message(source):
    try:
        plan = source()
    except PlanError as exc:
        return _error(str(exc))
    try:
        return json.dumps({"ok": True, "plan": plan}, allow_nan=False)
    except Exception:  # NaN, Infinity or an overflowed number (the one place they are refused), or not JSON at all
        return _error(NOT_SENDABLE)


def build(source):
    """The one-line message for the page. Never raises; only a PlanError's text is shown, anything else gets FALLBACK."""
    try:
        return _message(source)
    except Exception:
        return _error(FALLBACK)
