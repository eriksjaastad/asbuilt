"""One plan from two ELK plans: each box marked "planned" (the page draws it the same in both),
"changed" (in both, drawn differently), "added" (only built) or "dropped" (only planned), with an
empty "why". Built's boxes keep their place; dropped ones go under their planned parent."""

import json

from asbuilt.state import PlanError


def _q(text):  # as JSON.stringify writes it
    return json.dumps(text, ensure_ascii=False)


def check(plan, name):
    """A PlanError "NAME: " plus prepare.js's own message when the page would refuse `plan` for
    what compare reads (boxes, ids, children, edges, labels), in prepare.js's order. Width, height
    and arrow ends are not read here; the page refuses bad ones itself."""
    ids, todo = set(), [(plan, "plan")]
    while todo:  # a stack, not recursion: kids go on reversed so the first is first
        box, where = todo.pop()
        if not isinstance(box, dict):
            raise PlanError(f"{name}: {where}: is not an object")
        at = f"box {_q(i)} at {where}" if isinstance(i := box.get("id"), str) else where
        for key in ("children", "edges"):
            if not isinstance(box.get(key, []), list):
                raise PlanError(f"{name}: {at}: {key} is not a list")
        for item in [box, *box.get("edges", [])]:
            bid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(bid, str):
                kind = "has an id that" if item is box else "has an arrow whose id"
                raise PlanError(f"{name}: {at}: {kind} is not text")
            if bid in ids:
                raise PlanError(f"{name}: {at}: id {_q(bid)} is used twice")
            labels = item.get("labels", [])
            if not isinstance(labels, list) or not all(
                isinstance(x, dict) and isinstance(x.get("text"), str) for x in labels
            ):
                who = "has" if item is box else f"has an arrow {_q(bid)} with"
                bad = 'labels that are not a list like [{"text": "..."}]'
                raise PlanError(f"{name}: {at}: {who} {bad}")
            ids.add(bid)
        kids = list(enumerate(box.get("children", [])))
        todo += [(kid, f"{where}.children[{i}]") for i, kid in reversed(kids)]
    if not plan.get("children"):  # prepare.js takes "children": [] as a leaf
        raise PlanError(f"{name}: plan: has no boxes")


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
    Both must be plans the page accepts (prepare.js), so ids are unique text in each."""
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
