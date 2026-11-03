"""One plan from two ELK plans: each box and arrow marked "planned" (the same parent, label and
note, or the same ends and label, in both), "changed" (in both, one of those differs), "added" (only built) or "dropped" (only planned),
with an empty "why". Built's keep their place; a dropped box goes under its planned parent, a
dropped arrow on the top box, joining its planned ends.

python -m asbuilt.compare PLANNED BUILT OUT writes that plan to OUT, and `asbuilt OUT` draws each
mark with its tag, dash and hue (MARKS in page/shapes.js)."""

import argparse
import json
import math
import sys
from pathlib import Path

from asbuilt import output
from asbuilt.plan import parse, read_bytes
from asbuilt.state import PlanError


def _q(text):  # as JSON.stringify writes it
    return json.dumps(text, ensure_ascii=False, separators=(",", ":"))


def _above_zero(v):
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and v > 0
    )


def _refuse_unwritable(plan, name):
    todo = [(plan, "plan")]
    while todo:
        item, where = todo.pop()
        if isinstance(item, dict):
            todo += [(v, f"{where}.{k}") for k, v in item.items()]
        elif isinstance(item, list):
            todo += [(v, f"{where}[{i}]") for i, v in enumerate(item)]
        elif isinstance(item, float) and not math.isfinite(item):
            raise PlanError(f"{name}: {where}: is not a finite number")
        elif isinstance(item, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in item):
            raise PlanError(f"{name}: {where}: holds text that is not Unicode")


def check(plan, name):
    """A PlanError "NAME: " plus prepare.js's own message when the page would refuse `plan` for
    what compare reads (boxes, ids, children, edges, labels, arrow ends, sizes), in prepare.js's
    order. Non-finite numbers and lone surrogates are refused too (shared writer cannot write them)."""
    ids, arrows, todo = set(), [], [(plan, "plan")]
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
        for key in ("width", "height"):  # prepare.js: omit or a finite number above 0
            if key in box and not _above_zero(box[key]):
                raise PlanError(f"{name}: {at}: {key} is not a number above 0")
        arrows += box.get("edges", [])
        kids = list(enumerate(box.get("children", [])))
        todo += [(kid, f"{where}.children[{i}]") for i, kid in reversed(kids)]
    if not plan.get("children"):  # prepare.js takes "children": [] as a leaf
        raise PlanError(f"{name}: plan: has no boxes")
    boxes = ids - {plan["id"]} - {a["id"] for a in arrows}  # what an arrow may join
    for a in arrows:
        for key, end in [("sources", "source"), ("targets", "target")]:
            ends, at = a.get(key), f"{name}: arrow {_q(a['id'])}"
            if not isinstance(ends, list) or len(ends) != 1:
                raise PlanError(f"{at}: {key} must be a list of one box id")
            if not isinstance(ends[0], str) or ends[0] not in boxes:
                raise PlanError(f"{at}: {end} {_q(ends[0])} is not a box in the plan")
    _refuse_unwritable(plan, name)


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
        if bid not in new:
            if (
                bid == built["id"]
            ):  # dropping it would leave it unmarked and move its children
                raise ValueError(f'box "{bid}" was planned; built has it as its top')
            if bid in now:  # prepare.js refuses an id used twice
                raise ValueError(f'box "{bid}" was planned; built has an arrow "{bid}"')
            put(bid, parent, {k: v for k, v in box.items() if k != "edges"}, "dropped")
    for aid, (*_, arrow) in was.items():
        if aid not in now:  # its ends are built boxes or dropped ones put back
            if aid in new or aid == built["id"]:
                raise ValueError(f'arrow "{aid}" was planned; built has a box "{aid}"')
            root.setdefault("edges", []).append(_marked(arrow, "dropped"))
    return root


def main(argv=None):
    """Exit 0 with OUT written, or 2 with one line naming the file and why, and nothing written."""
    parser = argparse.ArgumentParser(prog="python -m asbuilt.compare")
    parser.add_argument("planned", type=Path, help="the planned chart's plan file")
    parser.add_argument("built", type=Path, help="the built chart's plan file")
    parser.add_argument("out", type=Path, help="where the marked plan is written")
    args = parser.parse_args(argv)
    try:
        plans = []
        for path, arg in [(args.planned, "PLANNED"), (args.built, "BUILT")]:
            try:  # samefile raises when a path cannot be statted (PermissionError on 3.12)
                same = args.out.exists() and path.exists() and args.out.samefile(path)
            except OSError as exc:
                why = exc.strerror or type(exc).__name__
                raise PlanError(f"{path.name} cannot be read: {why}") from None
            if same:
                raise PlanError(f"{args.out.name}: OUT is {arg}; inputs are only read")
            plans.append(parse(read_bytes(path), path.name))
            check(plans[-1], path.name)
        try:
            out = compare(*plans)
        except ValueError as exc:
            raise PlanError(f"{args.built.name}: {exc}") from None
        output.write_atomic(args.out, output.plan_bytes(out, args.out.name))
    except PlanError as exc:
        return output.fail("asbuilt.compare", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
