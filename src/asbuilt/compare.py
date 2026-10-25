"""One plan from two ELK plans: each box marked "planned" (the page draws it the same in both),
"changed" (in both, drawn differently), "added" (only built) or "dropped" (only planned), with an
empty "why". Built's boxes keep their place; dropped ones go under their planned parent."""


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
