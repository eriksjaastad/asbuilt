"""One plan from two ELK plans: each box and arrow marked "planned" (the page draws it the same
in both), "changed" (in both, drawn differently), "added" (only built) or "dropped" (only planned),
with an empty "why". Built's keep their place; a dropped box goes under its planned parent, a
dropped arrow on the top box, joining its planned ends."""

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


def _drawn(plan):
    """{id: (source, target, label, arrow)} for each arrow, as prepare.js draws it wherever it
    sits: from its one source to its one target, with its first label's text or none."""
    found, todo = {}, [plan]
    for box in todo:
        todo += box.get("children", [])
        for a in box.get("edges", []):
            label = ([x["text"] for x in a.get("labels", [])] + [""])[0]
            found[a["id"]] = (a["sources"][0], a["targets"][0], label, a)
    return found


def _mark(key, old, new):
    """For a key of `new`: "added", or by what the page draws (all but the last item of each
    value) "planned" or "changed"."""
    if key not in old:
        return "added"
    return "planned" if old[key][:-1] == new[key][:-1] else "changed"


def _marked(item, mark):
    return {**item, "asbuilt": {"mark": mark, "why": ""}}


def compare(planned, built):
    """Built with every box and arrow marked, plus the planned ones it lacks. Inputs are not
    changed. Both must be plans the page accepts (prepare.js), so ids are unique text in each."""
    old, new, was, now = _shown(planned), _shown(built), _drawn(planned), _drawn(built)
    root = {**built, "children": []}
    placed = {None: root}  # a loop, not recursion: parents come first in _shown

    def put(bid, parent, box, mark):
        placed[bid] = _marked({k: v for k, v in box.items() if k != "children"}, mark)
        placed.get(parent, root).setdefault("children", []).append(placed[bid])

    for bid, (parent, *_, box) in new.items():
        put(bid, parent, box, _mark(bid, old, new))
    for box in [root, *map(placed.get, new)]:  # copies: built's arrows stay as they are
        if "edges" in box:
            box["edges"] = [_marked(a, _mark(a["id"], was, now)) for a in box["edges"]]
    for bid, (parent, *_, box) in old.items():
        if bid not in new and bid != built["id"]:  # the root is in built, unmarked
            if bid in now:  # prepare.js refuses an id used twice
                raise ValueError(f'box "{bid}" was planned; built has an arrow "{bid}"')
            put(bid, parent, {k: v for k, v in box.items() if k != "edges"}, "dropped")
    for aid, (source, target, _, arrow) in was.items():
        if aid not in now:  # its ends are built boxes or dropped ones put back
            if aid in new or aid == built["id"]:
                raise ValueError(f'arrow "{aid}" was planned; built has a box "{aid}"')
            if built["id"] in (source, target):  # prepare.js refuses the top as an end
                raise ValueError(f'arrow "{aid}" was planned to end on built\'s top')
            root.setdefault("edges", []).append(_marked(arrow, "dropped"))
    return root
