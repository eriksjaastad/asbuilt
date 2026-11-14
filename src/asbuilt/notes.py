"""Model notes for boxes that already have source.

The model only comments: it cannot add or remove a box. A reply that is not the JSON
asked for raises PlanError and the plan is left unchanged. Unless ASBUILT_MODEL=1,
ask() is the mock and no notes are added.
"""

import json

from asbuilt.connections import ask
from asbuilt.plan import _object
from asbuilt.state import PlanError

LABELS = ("tricky part", "common pattern")
NOTE_MAX = 200
PROMPT = """Comment on these functions for someone learning the code. Add nothing.
Reply with JSON only: {{"notes": [{{"id": "<id from the list>", "label": "tricky part" or "common pattern", "text": "<one line, at most {max} characters>"}}]}}
Skip a function that is neither. Use each id at most once.
FUNCTIONS: {boxes}
"""


def _coded(plan):
    found, todo = [], [plan]
    while todo:
        box = todo.pop()
        todo.extend(box.get("children") or [])
        info = box.get("asbuilt")
        if isinstance(info, dict) and isinstance(info.get("text"), str):
            found.append(box)
    return found


def read_reply(reply, ids):
    """Notes from `reply`, or a PlanError. An id outside `ids` is refused."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        said = json.loads(text, object_pairs_hook=lambda pairs: _object(pairs, "the reply"))
    except (ValueError, RecursionError, PlanError):
        raise PlanError("it is not the JSON asked for") from None
    notes = said.get("notes") if isinstance(said, dict) and set(said) == {"notes"} else None
    if not isinstance(notes, list):
        raise PlanError('it is not {"notes": [...]}')
    seen, out, allowed = set(), [], set(ids)
    for n, item in enumerate(notes, 1):
        if not isinstance(item, dict) or set(item) != {"id", "label", "text"}:
            raise PlanError(f"note {n} is not an id, a label, and text")
        if item["id"] not in allowed or item["id"] in seen:
            raise PlanError(f"note {n} is not about one listed function")
        if item["label"] not in LABELS:
            raise PlanError(f"note {n} is not a tricky part or a common pattern")
        raw = item["text"]
        if not isinstance(raw, str) or not raw.strip() or not raw.isprintable() or len(raw.strip()) > NOTE_MAX:
            raise PlanError(f"note {n} is not one short line")
        seen.add(item["id"])
        out.append({"id": item["id"], "label": item["label"], "text": raw.strip()})
    return out


def applied(plan, notes):
    """A copy of `plan` with `notes` on the named boxes. No box is added, removed, or renamed."""
    plan = json.loads(json.dumps(plan))
    by_id = {note["id"]: {"label": note["label"], "text": note["text"]} for note in notes}
    todo = [plan]
    while todo:
        box = todo.pop()
        todo.extend(box.get("children") or [])
        info = box.get("asbuilt")
        if isinstance(info, dict) and "text" in info:
            if box["id"] in by_id:
                info["model"] = [by_id[box["id"]]]
            else:
                info.pop("model", None)
    return plan


def annotate(plan, name, connection=None):
    """`plan` plus model notes. `connection` defaults to the mock, which adds none."""
    boxes = _coded(plan)
    if not boxes:
        return plan
    said = [
        {"id": b["id"], "name": (b.get("labels") or [{"text": b["id"]}])[0].get("text", b["id"]), "code": b["asbuilt"]["text"]}
        for b in boxes
    ]
    try:
        notes = read_reply(ask(PROMPT.format(max=NOTE_MAX, boxes=json.dumps(said, ensure_ascii=False)), connection), [b["id"] for b in boxes])
    except PlanError as exc:
        raise PlanError(f"{name}: the model's reply was not used: {exc}") from None
    return applied(plan, notes)
