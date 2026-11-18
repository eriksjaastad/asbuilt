"""A program's components, found by parsing (D-026): each one thing the program does.

python -m asbuilt.components PATH OUT writes the component list of a Python folder (or file) as
JSON to OUT and prints it as text.

Entry points: a [project.scripts] line in the nearest pyproject.toml, an
`if __name__ == "__main__":` block or a `__main__.py`, a click command or group, and a route or
hook on a FastAPI or Flask app or router made in that file. One program is one component: a
click group with every command and group under it, a web app with every router it includes at
module level, and entry points that share a function, or whose code calls a click group's own
function, are one component listing each way in.

A component holds what its entry functions reach along the arrows asbuilt.built proves, plus
what a file's module-level code calls once a component reaches anything in that file, since
loading the file runs that code. A function or class reached from exactly one component is in
that component. One reached from two or more is in a shared box, one per file, named by the file;
each member lists the components that reach it. One no component reaches is listed as having no path
from an entry point that asBuilt found, which does not mean it is never run: a call through a
variable, an argument or `or` is not followed. Each is in exactly one box. An arrow between
boxes lists the calls, each with the annotations the called function declares, if any; they
are its declared signature, not proof of what is passed. Argparse
subcommands, scheduled jobs and routers added in a function are not looked for; a decorator
that looks like a command or route but is not proven is listed as not proven.

A folder a file puts on sys.path, with insert or append at a path the parser proves, is an
import root for that file and every file its imports reach (asbuilt.built.links). A module name
two files could then be is not followed. Any other sys.path change, or a folder known only when
the program runs, is listed as not proven."""

import argparse
import json
import sys
import tomllib
from pathlib import Path

from asbuilt import output
from asbuilt.built import _clashes, _definitions, _direct_calls, _live, links
from asbuilt.built import local_names, python_dir_links, python_file, targets, tree
from asbuilt.marks import resolve
from asbuilt.plan import read_bytes
from asbuilt.state import PlanError

CLICK = {("click", "command"), ("click", "group")}
APPS = {("fastapi", "FastAPI"), ("fastapi", "APIRouter"), ("flask", "Flask"), ("flask", "Blueprint")}
VERBS = {"get", "post", "put", "patch", "delete", "route", "api_route", "websocket"}
HOOKS = {"exception_handler", "middleware", "on_event", "errorhandler", "before_request", "after_request"}
INCLUDE = {"include_router", "register_blueprint"}
NO_PATH = "no path from an entry point found by asBuilt"
# A component is named after its way in that comes first here: a console script, a run file, a
# top-level click group, a web app, then a click subgroup.
SCRIPT, RUN, CLICK_TOP, WEB_APP, CLICK_SUB = range(5)


def _scripts(folder):
    """(name, module, function) for each [project.scripts] line of the nearest pyproject.toml."""
    for parent in [folder, *folder.parents]:
        file = parent / "pyproject.toml"
        if file.is_file():
            try:
                with file.open("rb") as fh:
                    scripts = tomllib.load(fh).get("project", {}).get("scripts", {})
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise PlanError(f"{file} cannot be read: {exc}") from None
            return [(name, *spec.partition(":")[::2]) for name, spec in scripts.items()]
    return []


def _is_main(node):
    """True for `if __name__ == "__main__":`, either way round."""
    cond = node.child_by_field_name("condition")
    if cond is None or cond.type != "comparison_operator" or len(cond.named_children) != 2:
        return False
    sides = sorted(c.text.decode().replace("'", '"') for c in cond.named_children)
    ops = [c.type for c in cond.children if not c.is_named]
    return sides == ['"__main__"', "__name__"] and ops == ["=="]


def _declared(node):
    """A function's annotations as written ("plan: Path -> dict"), or None when it has none:
    its declared signature, not proof of what a caller passes."""
    params = node.child_by_field_name("parameters")
    typed = [c.text.decode() for c in params.named_children if c.type.startswith("typed")]
    ret = node.child_by_field_name("return_type")
    text = ", ".join(typed) + (f" -> {ret.text.decode()}" if ret is not None else "")
    return text.strip() or None


def _entries(path):
    """The charted boxes, arrows and nodes; each entry point as (key, way in, roots, name hint);
    the click groups (child -> parent), the routers an app includes (router -> app), the
    decorators that look like entry points but are not proven, {file: boxes its module-level
    code calls}, and each file not read with why."""
    if path.is_dir():
        _, _, files, (found, index, modules, scopes, canon, paths) = python_dir_links(path)
    else:
        box, files = python_file(path.name, read_bytes(path)), [(path.name, path)]
        found, index, modules, scopes, canon, paths = links(box, files, box["id"], path.parent.resolve())
    by_module = {mod: rel for rel, mod in modules.items() if mod}
    names, edges, nodes, entries, apps, looks, included, adds = {}, [], {}, [], set(), [], {}, []
    loads, unread, moved = {}, [], []
    for rel, file in files:
        root = tree(read_bytes(file))
        fbox = found[rel]
        names.update((c["id"], c["labels"][0]["text"]) for c in fbox.get("children", []))
        edges += [(e["sources"][0], e["targets"][0]) for e in fbox.get("edges", [])]
        if root is None:
            unread.append(f"{rel}: {fbox['labels'][-1]['text']}")
            continue
        scope, mod, init = scopes[rel], modules.get(rel), rel.endswith("__init__.py")
        defs = {f"{rel}:{d[0]}:{d[2]}": d[4] for d in _definitions(root)}
        nodes.update(defs)
        local = local_names(mod, init, canon.get(rel))
        moved += [f"{rel}:{line}: {stmt}: {why}" for line, stmt, _, why in paths[rel] if why]

        def box_of(name, rel=rel, scope=scope, defs=defs):
            """The box a top-level name is: this file's one def of it, or a def an import proves."""
            spec = scope.get(name)
            if spec == ("def",):
                return next((c for c in defs if c.rpartition(":")[0] == f"{rel}:{name}"), None)
            return _live(index, scopes, spec[1], spec[2]) if spec and spec[0] == "from" else None

        def reach(block, box_of=box_of, scope=scope, local=local):
            """Boxes that code outside any function calls, by built's own call rules."""
            attrs = []
            calls = _direct_calls(block, lambda s: {} if s is block else local(s), attrs, block)
            own = [box_of(n) for n, how in calls if how is None and scope.get(n) == ("def",)]
            return [r for r in own if r] + targets(calls, attrs, scope, index, scopes, modules)

        def app_of(arg, scope=scope):
            """(file, name) of the router an include names, when an import proves it."""
            spec = scope.get(arg.text.decode()) if arg.type == "identifier" else None
            if spec and spec[0] == "from":
                return by_module.get(spec[1]), spec[2]
            obj, attr = arg.child_by_field_name("object"), arg.child_by_field_name("attribute")
            spec = scope.get(obj.text.decode()) if arg.type == "attribute" and obj.type == "identifier" else None
            return (by_module.get(spec[1]), attr.text.decode()) if spec and spec[0] == "mod" else None

        run = f"python -m {'.'.join(mod)}" if mod else f"python {rel}"
        hint = mod[-2] if mod and len(mod) > 1 and rel.endswith("__main__.py") else mod[-1] if mod else Path(rel).stem
        top = ("function_definition", "class_definition", "decorated_definition")
        code = [n for n in root.named_children if n.type not in top]  # defs are boxes of their own
        loaded = [n for n in code if not (n.type == "if_statement" and _is_main(n))]
        loads[rel] = [r for n in loaded for r in reach(n)]  # what loading the file calls
        if rel.endswith("__main__.py"):
            entries.append((("main", rel), run.removesuffix(".__main__"), [r for n in code for r in reach(n)], (RUN, hint)))
        for node in root.named_children:
            if node.type == "if_statement" and _is_main(node) and not rel.endswith("__main__.py"):
                entries.append((("main", rel), run, reach(node.child_by_field_name("consequence")), (RUN, hint)))
            stmt = node.named_children[0] if node.type == "expression_statement" else None
            if stmt is not None and stmt.type == "assignment":
                right = stmt.child_by_field_name("right")
                if right is not None and right.type == "call":
                    if resolve(right.child_by_field_name("function"), scope) in APPS:
                        apps.add((rel, stmt.child_by_field_name("left").text.decode()))
            if stmt is not None and stmt.type == "call":
                fun, args = stmt.child_by_field_name("function"), stmt.child_by_field_name("arguments").named_children
                if fun.type == "attribute" and fun.child_by_field_name("attribute").text.decode() in INCLUDE and args:
                    router = app_of(args[0])
                    if router:
                        included[router] = (rel, fun.child_by_field_name("object").text.decode())
                if fun.type == "attribute" and fun.child_by_field_name("attribute").text.decode() == "add_command":
                    obj = fun.child_by_field_name("object")
                    if obj.type == "identifier" and args and args[0].type == "identifier":
                        adds.append((box_of(obj.text.decode()), box_of(args[0].text.decode())))
        for cid, node in defs.items():
            outer = node.parent
            for dec in outer.named_children if outer.type == "decorated_definition" else ():
                call = dec.named_children[0]  # `@x.get("/a")`, or bare `@click.command`, with no arguments
                bare = call.type in ("attribute", "identifier")
                fun = call if bare else call.child_by_field_name("function") if call.type == "call" else None
                if fun is not None and resolve(fun, scope) in CLICK:
                    looks.append((None, cid, fun.text.decode().split(".")[-1], rel))
                elif fun is not None and fun.type == "attribute" and fun.child_by_field_name("object").type == "identifier":
                    obj = fun.child_by_field_name("object").text.decode()
                    verb = fun.child_by_field_name("attribute").text.decode()
                    args = [] if bare else call.child_by_field_name("arguments").named_children
                    at = f" {args[0].text.decode()}" if args and args[0].type == "string" else ""
                    if verb in VERBS | HOOKS and (rel, obj) in apps:
                        way = f"{verb.upper() if verb in VERBS else verb}{at} on {obj} in {rel}: {names[cid]}"
                        entries.append((("routes", rel, obj), way, [cid], (WEB_APP, f"web app {obj} in {rel}")))
                    elif verb in VERBS or verb in ("command", "group"):
                        looks.append((box_of(obj) or obj, cid, verb, rel))
    # A decorator on a group is a click one only once that group is proven; groups nest.
    parent, groups, more = {}, set(), True
    while more:
        more = False
        for group, cid, kind, _ in looks:
            if kind in ("command", "group") and cid not in parent and (group is None or group in groups):
                parent[cid], more = group, True
                groups |= {cid} if kind == "group" else set()
    proven = {m for m in looks if m[1] in parent and parent[m[1]] == m[0] and m[2] in ("command", "group")}
    for group, cid, kind, rel in sorted(proven, key=lambda m: m[1]):
        under = "" if group is None else f" under {names[group]}"
        hint = (CLICK_TOP if group is None else CLICK_SUB, names[cid][:-2])  # named by its outer group
        entries.append((("click", cid), f"click {kind} {names[cid]}{under} in {rel}", [cid], hint))
    for group, cid in adds:  # `cli.add_command(sub)` puts a proven command under a proven group
        if group in groups and cid != group and cid in parent and parent[cid] is None:
            parent[cid] = group
    if path.is_dir():
        for script, module, fn in _scripts(path):
            cid = _live(index, scopes, tuple(module.split(".")), fn)
            if cid:
                entries.append((("script", cid), f"console script {script} = {module}:{fn}", [cid], (SCRIPT, script)))
            else:
                looks.append((f"{module}:{fn}", script, "script", "pyproject.toml"))

    def why_not(group, cid, kind, rel):
        if kind == "script":
            return f"console script {cid} = {group}: not a function charted here"
        what = "a click group" if kind in ("command", "group") else "an app or router made in this file"
        name = names.get(group, group)
        return f"@{name}.{kind} on {names[cid]} in {rel}: {name} is not proven to be {what}"

    unproven = [why_not(*m) for m in looks if m not in proven] + moved
    included = {k: v for k, v in included.items() if k in apps}
    return names, edges, nodes, entries, parent, included, unproven, loads, unread


def find(path):
    """The component list of a folder or file, as a dict ready to write as JSON."""
    names, edges, nodes, entries, parent, included, unproven, loads, unread = _entries(path)
    file_of = lambda box: box.rsplit(":", 2)[0]  # noqa: E731
    after = {}
    for a, b in edges:
        after.setdefault(a, []).append(b)
    for box in names:  # reaching a box loads its file, which runs the file's module-level code
        if loads.get(file_of(box)):
            after.setdefault(box, []).append(f"loading {file_of(box)}")
    for rel, called in loads.items():
        after[f"loading {rel}"] = called

    def walk(roots):
        """{box: path from a root} for every box the roots reach along the arrows."""
        paths, todo = {r: [r] for r in roots}, list(roots)
        for cur in todo:
            for nxt in after.get(cur, []):
                if nxt not in paths:
                    paths[nxt] = [*paths[cur], nxt]
                    todo.append(nxt)
        return paths

    def top(key):
        """The entry a click group or an included router belongs to: its outermost group or app."""
        for _ in range(len(parent) + len(included)):  # bounded, so a loop of includes ends
            if key[0] == "click" and parent.get(key[1]) is not None:
                key = ("click", parent[key[1]])
            elif key[0] == "routes" and key[1:] in included:
                key = ("routes", *included[key[1:]])
        return key

    union = {}

    def find_set(key):
        while union.get(key, key) != key:
            key = union[key]
        return key

    keyed, nothing = {}, []
    for key, way, roots, hint in entries:
        if not roots:
            nothing.append(way)
            continue
        slot = keyed.setdefault(top(key), {"ways": [], "roots": [], "hints": []})
        slot["ways"].append(way)
        slot["roots"] += [r for r in roots if r not in slot["roots"]]
        slot["hints"].append(hint)
    owner = {}  # A root shared by two entries joins them, and so does code calling a click group.
    for key, slot in keyed.items():
        for r in slot["roots"]:
            union[find_set(key)] = find_set(owner.setdefault(r, key))
    groups = {key[1]: key for key in keyed if key[0] == "click"}
    for key, slot in keyed.items():
        for box in walk(slot["roots"]):
            if box in groups:
                union[find_set(groups[box])] = find_set(key)
    merged = {}
    for key, slot in keyed.items():
        into = merged.setdefault(find_set(key), {"ways": [], "roots": [], "hints": []})
        for field in ("ways", "roots", "hints"):
            into[field] += [x for x in slot[field] if x not in into[field]]
    comps, why = [], {}  # why: box -> {component name: path from a root}
    for slot in sorted(merged.values(), key=lambda s: min(s["hints"])):
        name = min(slot["hints"])[1]
        while name in [c["name"] for c in comps]:
            name += "'"
        comps.append({"name": name, "ways_in": slot["ways"], "roots": slot["roots"]})
        for box, chain in walk(slot["roots"]).items():
            why.setdefault(box, {})[name] = chain

    def because(chain):
        via = f" via {', '.join(names.get(p, p) for p in chain[1:-1])}" if len(chain) > 2 else ""
        return "entry point" if len(chain) == 1 else f"reached from {names[chain[0]]}{via}"

    users = {}  # file -> the components reaching code of it that two or more reach
    for box, held in why.items():
        if box in names and len(held) > 1:
            users.setdefault(file_of(box), set()).update(held)
    short = {f: f.rsplit("/", 1)[-1] for f in users}  # the file's name, or its path when two share it
    short = {f: n if list(short.values()).count(n) == 1 else f for f, n in short.items()}

    def place(box):
        held = why.get(box, {})
        if len(held) < 2:
            return next(iter(held), NO_PATH)
        return short[file_of(box)]  # by file alone: each member lists the components reaching it

    group = {box: place(box) for box in names}
    for comp in comps:
        comp["members"] = [
            {"id": b, "name": names[b], "why": because(why[b][comp["name"]])} for b in names if group[b] == comp["name"]
        ]
    shared = {}
    for b in names:
        if group[b] not in [c["name"] for c in comps] and group[b] != NO_PATH:
            box = shared.setdefault(group[b], {"name": group[b], "file": file_of(b), "used_by": sorted(users[file_of(b)]), "members": []})
            box["members"].append({"id": b, "name": names[b], "used_by": {c: because(p) for c, p in why[b].items()}})
    arrows = {}
    for a, b in edges:
        if group[a] != group[b]:
            call = {"from": names[a], "to": names[b]}
            declared = _declared(nodes[b]) if nodes[b].type == "function_definition" else None
            if declared:
                call["callee_declares"] = declared
            arrows.setdefault((group[a], group[b]), []).append(call)
    return {
        "components": comps,
        "shared": list(shared.values()),
        "no_path_found": [{"id": b, "name": names[b]} for b in names if group[b] == NO_PATH],
        "arrows": [{"from": a, "to": b, "calls": calls} for (a, b), calls in arrows.items()],
        "entries_reaching_nothing": nothing,
        "not_proven": unproven,
        "files_not_read": unread,
    }


def summary(found):
    """The component list as short text, for a person to judge the boxes."""
    file = lambda m: m["id"].rsplit(":", 2)[0]  # noqa: E731
    comps, shared, none = found["components"], found["shared"], found["no_path_found"]
    lines = [f"{len(comps)} components, {len(shared)} shared files, {NO_PATH} {len(none)}", ""]
    lines += [f"  {c['name']}: {len(c['members'])}" for c in comps]
    lines += [f"  {s['name']}: {len(s['members'])}" for s in shared]
    lines += [f"  {NO_PATH}: {len(none)}", "", f"arrows between boxes: {len(found['arrows'])}"]
    lines += [f"  {a['from']} -> {a['to']}: {len(a['calls'])} calls" for a in found["arrows"]]
    for comp in comps:
        lines += ["", f"{comp['name']}: {len(comp['members'])} members", "  ways in:"]
        lines += [f"    {w}" for w in comp["ways_in"]]
        lines += [f"  {m['name']} ({file(m)}): {m['why']}" for m in comp["members"]]
    for box in shared:
        lines += ["", f"{box['name']}: {len(box['members'])} members"]
        for m in box["members"]:
            lines.append(f"  {m['name']}: " + "; ".join(f"{c} {w}" for c, w in m["used_by"].items()))
    by_file = {}
    for m in none:
        by_file.setdefault(file(m), []).append(m["name"])
    lines += ["", f"{NO_PATH}: {len(none)}"] + [f"  {f}: {', '.join(n)}" for f, n in by_file.items()]
    lines += ["", "calls between boxes:"]
    for arrow in found["arrows"]:
        calls = ", ".join(f"{c['from']} -> {c['to']}" + (f" [callee declares {c['callee_declares']}]" if "callee_declares" in c else "") for c in arrow["calls"])
        lines.append(f"  {arrow['from']} -> {arrow['to']}: {calls}")
    for title, items in (
        ("entry points reaching no charted function", found["entries_reaching_nothing"]),
        ("not proven", found["not_proven"]),
        ("files not read", found["files_not_read"]),
    ):
        if items:
            lines += ["", f"{title}: {len(items)}"] + [f"  {i}" for i in items]
    return "\n".join(lines)


def main(argv=None):
    """Exit 0 with OUT written and the summary printed, or 2 with one line and nothing written."""
    parser = argparse.ArgumentParser(prog="python -m asbuilt.components", description=__doc__.split("\n\n")[0])
    parser.add_argument("path", type=Path, help="the Python folder or file")
    parser.add_argument("out", type=Path, help="where the component list is written, as JSON")
    args = parser.parse_args(argv)
    try:
        if not args.path.exists():
            raise PlanError(f"{args.path.name}: no such file or folder")
        try:  # samefile raises when a path cannot be statted
            clash = _clashes(args.out, args.path)
        except OSError as exc:
            raise PlanError(f"{args.path.name} cannot be read: {exc.strerror or exc}") from None
        if clash:
            raise PlanError(clash)
        found = find(args.path)
        data = (json.dumps(found, indent=2, ensure_ascii=False) + "\n").encode()
        output.write_atomic(args.out, data)
    except PlanError as exc:
        return output.fail("asbuilt.components", exc)
    print(summary(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
