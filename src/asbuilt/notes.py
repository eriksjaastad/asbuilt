"""Model notes for boxes that already have source. python -m asbuilt.notes PLAN OUT

The model only comments: it cannot add or remove a box. A reply that is not the JSON
asked for is refused, the command exits 2, and nothing is written. A run that returns no
notes, including the mock used unless ASBUILT_MODEL=1, leaves model notes already there.
A run that returns notes replaces them.

Security sentences are already on the plan. The same opt-in can rephrase them. A sentence
that does not repeat its fact is left as it was. The mock does not call out.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from asbuilt import output
from asbuilt.compare import check
from asbuilt.connections import ConnectionFailed, ask
from asbuilt.plan import _object, parse, read_bytes
from asbuilt.security import reworded
from asbuilt.state import PlanError

LABELS = ("tricky part", "common pattern")
NOTE_MAX = 200
WORD_PROMPT = """Write a plain sentence and an expert sentence for each fact. Use only the fact.
The plain sentence is for a beginner. The expert sentence may mention only a field that is present:
the call, the environment name, a timeout, try, or a loop. Add no other risk.
Each sentence must contain the fact's "at" text exactly and be one line.
Reply with JSON only: {{"notes": [{{"id": "<id>", "at": "<at>", "plain": "<sentence>", "expert": "<sentence>"}}]}}
FACTS: {facts}
"""
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


def _rows(plan):
    rows, todo = [], [plan]
    while todo:
        box = todo.pop()
        todo.extend(box.get("children") or [])
        for note in (box.get("asbuilt") or {}).get("security") or []:
            fact = note.get("fact") if isinstance(note, dict) else None
            if isinstance(fact, dict) and isinstance(fact.get("at"), str):
                rows.append({"id": box["id"], "fact": fact})
    return rows


def _reword(plan, connection):
    """Replace a security sentence only when the model repeats that fact. Otherwise leave it.
    The mock is not asked: a sentence is already filled in from the fact."""
    rows = _rows(plan)
    if not rows:
        return plan
    if connection is None and os.environ.get("ASBUILT_MODEL") not in ("claude", "1"):
        return plan
    payload = [{"id": row["id"], "fact": row["fact"]} for row in rows]
    chosen = reworded(ask(WORD_PROMPT.format(facts=json.dumps(payload, ensure_ascii=False)), connection), rows)
    if not chosen:
        return plan
    plan = json.loads(json.dumps(plan))
    by = {(item["id"], item["at"]): item for item in chosen}
    todo = [plan]
    while todo:
        box = todo.pop()
        todo.extend(box.get("children") or [])
        for note in (box.get("asbuilt") or {}).get("security") or []:
            hit = by.get((box.get("id"), (note.get("fact") or {}).get("at")))
            if hit:
                note["plain"], note["expert"] = hit["plain"], hit["expert"]
    return plan


def annotate(plan, name, connection=None):
    """`plan` plus model notes. No notes back leaves model notes already there; notes replace them."""
    boxes = _coded(plan)
    if not boxes:
        return _reword(plan, connection)
    said = [
        {"id": b["id"], "name": (b.get("labels") or [{"text": b["id"]}])[0].get("text", b["id"]), "code": b["asbuilt"]["text"]}
        for b in boxes
    ]
    try:
        notes = read_reply(ask(PROMPT.format(max=NOTE_MAX, boxes=json.dumps(said, ensure_ascii=False)), connection), [b["id"] for b in boxes])
    except PlanError as exc:
        raise PlanError(f"{name}: the model's reply was not used: {exc}") from None
    return _reword(plan if not notes else applied(plan, notes), connection)


def main(argv=None):
    """Exit 0 with OUT written, or 2 with one line and nothing written."""
    parser = argparse.ArgumentParser(prog="python -m asbuilt.notes", description=__doc__.split("\n\n")[0])
    parser.add_argument("plan", type=Path, help="the plan to annotate")
    parser.add_argument("out", type=Path, help="where the annotated plan is written")
    args = parser.parse_args(argv)
    try:
        plan = parse(read_bytes(args.plan), args.plan.name)
        check(plan, args.plan.name)
        out = annotate(plan, args.plan.name)
        output.write_atomic(args.out, output.plan_bytes(out, args.out.name))
    except (PlanError, ConnectionFailed) as exc:
        return output.fail("asbuilt.notes", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
