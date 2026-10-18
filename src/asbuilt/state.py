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
    """The one-line message for the page: the plan, or an error text. Never raises.

    Only a PlanError's own text is shown; any other failure gets one fixed sentence, so no
    code of a failing source runs while describing it.
    """
    try:
        return _message(source)
    except Exception:
        return _error(FALLBACK)
