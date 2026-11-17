"""Security sentences for facts the parser proved. Plain and Expert are two of the three
levels (Plain, Working, Expert). Working is not written here.

A sentence may only say what its fact says. The chart's sentences are filled in here, so
building a chart never calls a model. A model may rephrase them later, and only a sentence
that still contains the fact is kept.
"""

import json
import re

from asbuilt.marks import facts
from asbuilt.plan import _object
from asbuilt.state import PlanError

_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")
# A model sentence that says one of these, and the fact does not, is inventing a risk.
_ALARM = ("injection", "malware", "exploit", "hack", "vulnerable", "unsanitized", "password", "leak", "stolen", "attack")


def _plain(fact):
    at, kind = fact["at"], fact["kind"]
    if kind == "input":
        return f"This reads what a person types: {at}."
    if kind == "env":
        how, name = fact.get("how"), fact.get("name")
        if how == "check":
            which = f"whether {name} is set" if name else "the environment"
            return f"This checks {which}, and does not read the value: {at}."
        if how == "set":
            return f"This sets {name} in the environment: {at}." if name else f"This sets an environment value: {at}."
        if how == "write":
            return f"This changes the environment: {at}."
        if name:
            return f"This reads {name} from the environment: {at}."
        return f"This reads the environment: {at}."
    if fact.get("way") == "command":
        return f"This runs a command outside this program: {at}."
    if fact.get("way") == "connect":
        return f"This opens a network connection: {at}."
    return f"This sends your data to an outside service: {at}."


def _timeout(fact):
    state = fact.get("timeout")
    if state == "passed":
        return "A timeout is passed."
    if state == "none":
        return "The timeout is None."
    if state == "absent":
        return "No timeout is passed."
    return ""


def _expert(fact):
    at = fact["at"]
    if fact["kind"] == "input":
        line = f"{at} reads what a person types."
        if fact.get("prompt"):
            line += f" The prompt is {fact['prompt']!r}."
        return line
    if fact["kind"] == "env":
        how, name = fact.get("how"), fact.get("name")
        if how == "check":
            which = f"{name} is set" if name else "a name is set"
            return f"{at} checks whether {which}. It does not read the value."
        if how == "set":
            which = name if name else "a value"
            return f"{at} sets {which} in the environment."
        if how == "write":
            return f"{at} changes the environment."
        if name:
            return f"{at} reads {name} from the environment."
        return f"{at} reads the environment. The name is not a literal in this box."
    parts = [f"{at}."]
    timeout = _timeout(fact)
    if timeout:
        parts.append(timeout)
    parts.append("This call is inside try." if fact.get("caught") else "This call is not inside try.")
    if fact.get("repeats"):
        parts.append("This call is inside a loop in this box.")
    return " ".join(parts)


def for_box(node, scope, constants):
    """The plain and expert sentences for this box, one pair per fact. [] when there is no fact."""
    return [{"fact": fact, "plain": _plain(fact), "expert": _expert(fact)} for fact in facts(node, scope, constants)]


def _quotes(text):
    return [one or two for one, two in _QUOTED.findall(text)]


def _fits(text, fact):
    """True when this one line repeats the fact and does not name a new quoted string or an alarm."""
    if not isinstance(text, str):
        return False
    line = text.strip()
    if not line or "\n" in line or len(line) > 240 or fact["at"] not in line:
        return False
    allowed = fact["at"]
    name = fact.get("name") or ""
    for quoted in _quotes(line):
        if quoted and quoted not in allowed and quoted != name:
            return False
    low = line.casefold()
    return not any(word in low and word not in allowed.casefold() for word in _ALARM)


def reworded(reply, rows):
    """Sentences from a model reply that still point at a fact. A reply that is not the JSON
    asked for is ignored, and so is one sentence that adds a name or a risk. rows are
    {"id", "fact"} for notes already on the plan."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        said = json.loads(text, object_pairs_hook=lambda pairs: _object(pairs, "the reply"))
    except (ValueError, RecursionError, PlanError):
        return []
    notes = said.get("notes") if isinstance(said, dict) and set(said) == {"notes"} else None
    if not isinstance(notes, list):
        return []
    pending = {(row["id"], row["fact"]["at"]): row["fact"] for row in rows}
    kept = []
    for item in notes:
        if not isinstance(item, dict) or set(item) != {"id", "at", "plain", "expert"}:
            continue
        fact = pending.get((item["id"], item["at"]))
        if fact is None or not _fits(item["plain"], fact) or not _fits(item["expert"], fact):
            continue
        kept.append({"id": item["id"], "at": item["at"], "plain": item["plain"].strip(), "expert": item["expert"].strip()})
    return kept
