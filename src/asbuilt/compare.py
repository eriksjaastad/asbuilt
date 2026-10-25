"""One plan from two ELK plans: each box marked "planned" (the page draws it the same in both),
"changed" (in both, drawn differently), "added" (only built) or "dropped" (only planned), with an
empty "why". Built's boxes keep their place; dropped ones go under their planned parent."""

import json

from asbuilt.state import PlanError


LIKE = '[{"text": "..."}]'


def _readable(labels):
    return isinstance(labels, list) and all(
        isinstance(one, dict) and isinstance(one.get("text"), str) for one in labels
    )


def check(plan, name):
    """A PlanError naming the file and the box unless the page (prepare.js) would take `plan` as
    far as compare reads it: objects, children and edges lists, text ids used once across boxes
    and arrows, labels like [{"text": ...}], boxes under the root. Arrow ends: not read here."""
    ids, todo = set(), [(plan, "plan")]
    for box, at in todo:  # grows as it goes, so deep plans need no recursion
        if not isinstance(box, dict):
            raise PlanError(f"{name}: {at} is not an object.")
        if isinstance(box.get("id"), str):
            at = f"box {json.dumps(box['id'])}"
        if not all(isinstance(box.get(key, []), list) for key in ("children", "edges")):
            raise PlanError(f"{name}: {at} has children or edges that are not a list.")
        for item in [box, *box.get("edges", [])]:
            bid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(bid, str):
                raise PlanError(f"{name}: {at} or one of its arrows has a non-text id.")
            if bid in ids:
                raise PlanError(f"{name}: {at}: id {json.dumps(bid)} is used twice.")
            if not _readable(item.get("labels", [])):
                raise PlanError(
                    f"{name}: {at}: {json.dumps(bid)} has labels unlike {LIKE}."
                )
            ids.add(bid)
        todo += [
            (kid, f"{at}.children[{i}]")
            for i, kid in enumerate(box.get("children", []))
        ]
    if "children" not in plan:
        raise PlanError(f"{name}: plan has no boxes.")


def _shown(plan):
    """{id: (parent id or None, label, note, box)} for each box under the root, parents first;
    label and note as layout.js draws them: first label's text or the id, second's or none."""
    found, todo = {}, [(plan, None)]
    for box, at in todo:  # grows as it goes: each box's children are looked at once
        for kid in box.get("children", []):
            texts = [label["text"] for label in kid.get("labels", [])]
            label, note = (texts + [kid["id"], ""])[0], (texts[1:] + [""])[0]
            found[kid["id"]] = (at, label, note, kid)
            todo.append((kid, kid["id"]))
    return found


def compare(planned, built):
    """Built with every box marked, plus the planned boxes it lacks. Inputs are not changed.
    Both must pass check(), so ids are unique text in each."""
    old, new = _shown(planned), _shown(built)
    root = {**built, "children": []}
    placed = {None: root}  # a loop, not recursion: parents come first in _shown
    boxes = [built] + [seen[-1] for seen in new.values()]
    arrows = {e["id"] for b in boxes for e in b.get("edges", [])}

    def put(bid, parent, box, mark):
        placed[bid] = {k: v for k, v in box.items() if k != "children"}
        placed[bid]["asbuilt"] = {"mark": mark, "why": ""}
        placed.get(parent, root).setdefault("children", []).append(placed[bid])

    for bid, (parent, label, note, box) in new.items():
        same = bid in old and old[bid][:3] == (parent, label, note)
        mark = "planned" if same else "changed" if bid in old else "added"
        put(bid, parent, box, mark)
    for bid, (parent, *_, box) in old.items():
        if bid not in new and bid != built["id"]:  # the root is in built, unmarked
            if bid in arrows:  # prepare.js refuses an id used twice
                raise ValueError(f'box "{bid}" was planned; built has an arrow "{bid}"')
            put(bid, parent, {k: v for k, v in box.items() if k != "edges"}, "dropped")
    return root
