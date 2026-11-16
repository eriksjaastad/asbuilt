"""As-built chart: the code, read into the ELK JSON the page draws.

python -m asbuilt.built PATH OUT writes one Python file, or a folder of Python files, as a
plan the page can draw. A file is one box, and each top-level function and class is a box inside
it. A folder holds a box per file and a box per subfolder that has one. A nested file's id is
its path (`sub/util.py:name`). tests, __pycache__, venv, env, node_modules, build, dist,
site-packages, and hidden directories are skipped. OUT is only written. A missing path, or OUT
being the source or ending in .py, prints one line and writes nothing. A folder with nothing
to chart does too.

A file Python accepts, as UTF-8, holds those definitions, the lines they occupy, and an arrow
where one of them calls another top-level name in the file. Otherwise the
file is one box with a note: Python does not accept it, it is not UTF-8, or tree-sitter misreads
code Python accepts. Python's own compile, never run, decides what is valid."""

import argparse
import sys
import tokenize
import warnings
from pathlib import Path

import tree_sitter
import tree_sitter_python

from asbuilt import output
from asbuilt.compare import check
from asbuilt.plan import read_bytes
from asbuilt.state import PlanError

PYTHON = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))
CAP = 40  # lines of a definition kept on the box; the rest stays in the file, named, not summarized
PASSES = "This function calls one other function and returns whatever that returns."


def _note(source):
    """Why Python does not accept these bytes, or "": they are compiled, never run. A warning,
    like a bad escape, is not an error."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            compile(source, "", "exec", dont_inherit=True)
        except SyntaxError as e:  # also IndentationError, bad bytes or coding
            line = f": line {e.lineno}" if e.lineno else ""
            return f"does not parse{line} (Python: {e.msg})"
        except Exception as e:  # anything else compile raises, like too deep
            return f"does not parse (Python: {type(e).__name__}: {e})"
    return ""


def _text(source):
    """The bytes Python accepted, as UTF-8 text for tree-sitter with line endings as Python
    reads them, and "", or None and why not: tokenize.detect_encoding names something other
    than utf-8 or utf-8-sig, or the bytes do not decode under that encoding."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # unicode_escape warns on a bad escape
        try:
            lines = iter(source.splitlines(True)).__next__  # LF, CRLF or CR, as Python
            encoding = tokenize.detect_encoding(lines)[0]
            if encoding not in ("utf-8", "utf-8-sig"):
                return None, "Python accepts it; not UTF-8, so not read"
            text = source.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
            return text.encode(), ""
        except Exception:  # a bad byte, a codec cut short, anything a codec raises
            return None, "Python accepts it; not UTF-8, so not read"


def _line(point):
    """A tree-sitter point as a 1-based line. The end point is the first column not in the node,
    so a node that ends at column 0 belongs to the previous line."""
    row, col = point
    return row if col == 0 else row + 1


def _definitions(root):
    """Top-level functions and classes: (name, class?, start line, end line). A decorator stays
    with the definition it decorates. Anything nested, including a function inside a class or
    under an if, is left inside its parent's lines and is not its own box."""
    found = []
    for node in root.named_children:
        outer = node
        if node.type == "decorated_definition":
            node = node.child_by_field_name("definition")
        if node is None or node.type not in ("function_definition", "class_definition"):
            continue
        name = node.child_by_field_name("name")
        if name is None:
            continue
        start = outer.start_point[0] + 1
        found.append(
            (name.text.decode(), node.type == "class_definition", start, _line(outer.end_point), node)
        )
    return found


def _calls(node):
    """How many calls `node` contains, walked on a stack so a deep expression cannot overflow."""
    count, todo = 0, [node]
    while todo:
        cur = todo.pop()
        count += cur.type == "call"
        todo.extend(cur.children)
    return count


def _one_name(child):
    """A parameter's own name. The default (`fn=load`) and the type are not names it binds."""
    if child.type == "identifier":
        return child.text.decode()
    ident = next((c for c in child.named_children if c.type == "identifier"), None)
    return None if ident is None else ident.text.decode()


def _imported(stmt):
    """Names an import binds. `import pkg as load` and `from pkg import load` bind load, not pkg."""
    items = stmt.named_children[1:] if stmt.type == "import_from_statement" else stmt.named_children
    names = []
    for item in items:
        if item.type == "aliased_import":
            alias = next((c for c in item.named_children if c.type == "identifier"), None)
            if alias is not None:
                names.append(alias.text.decode())
        elif item.type == "dotted_name":
            ident = next((c for c in item.named_children if c.type == "identifier"), None)
            if ident is not None:
                names.append(ident.text.decode())
    return names


def _bound(node):
    """Names this box assigns. A call of one of them is not the top-level function of that name.
    `global load` puts the name back: the call is the top-level one."""
    names, glob = set(), set()
    params = node.child_by_field_name("parameters")
    for child in params.named_children if params is not None else ():
        name = _one_name(child)
        if name is not None:
            names.add(name)
    body = node.child_by_field_name("body")
    todo = list(reversed(body.children)) if body is not None else []
    while todo:
        cur = todo.pop()
        if cur.type in ("function_definition", "class_definition"):
            name = cur.child_by_field_name("name")
            if name is not None:
                names.add(name.text.decode())
            continue
        if cur.type == "decorated_definition":
            inner = cur.child_by_field_name("definition")
            name = None if inner is None else inner.child_by_field_name("name")
            if name is not None:
                names.add(name.text.decode())
            continue
        if cur.type == "global_statement":
            glob.update(c.text.decode() for c in cur.named_children if c.type == "identifier")
            continue
        if cur.type in ("import_statement", "import_from_statement"):
            names.update(_imported(cur))
            continue
        if cur.type == "as_pattern":
            target = next((c for c in cur.named_children if c.type == "as_pattern_target"), None)
            if target is not None:
                names.update(_pattern(next(iter(target.named_children), None)))
        if cur.type in ("assignment", "augmented_assignment", "for_statement", "named_expression"):
            names.update(_pattern(next(iter(cur.named_children), None)))
        todo.extend(reversed(cur.children))
    return names - glob


def _pattern(node):
    """Identifiers bound by an assignment target, not the value or a type."""
    if node is None:
        return ()
    if node.type == "identifier":
        return (node.text.decode(),)
    if node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        return tuple(name for c in node.named_children for name in _pattern(c))
    return ()


def _scoped(node, extra):
    """Names a lambda or a comprehension binds, added to the names already bound outside it."""
    names = set(extra)
    if node.type == "lambda":
        params = next((c for c in node.named_children if c.type == "lambda_parameters"), None)
        for child in params.named_children if params is not None else ():
            name = _one_name(child)
            if name is not None:
                names.add(name)
    else:
        for clause in node.named_children:
            if clause.type == "for_in_clause":
                names.update(_pattern(next(iter(clause.named_children), None)))
    return names


def _direct_calls(node, scope):
    """Bare names called in `node`'s body, ignoring names bound there. A call on an attribute,
    and a call inside a nested function or class, are skipped."""
    body = node.child_by_field_name("body")
    found, todo = [], [(c, scope) for c in reversed(body.children)] if body is not None else []
    nested = ("function_definition", "class_definition", "decorated_definition")
    comps = ("list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression")
    while todo:
        cur, names = todo.pop()
        if cur.type in nested:
            continue
        if cur.type == "lambda" or cur.type in comps:
            todo.extend((c, _scoped(cur, names)) for c in reversed(cur.children))
            continue
        if cur.type == "call":
            fun = next(iter(cur.named_children), None)
            if fun is not None and fun.type == "identifier" and fun.text.decode() not in names:
                found.append(fun.text.decode())
        todo.extend((c, names) for c in reversed(cur.children))
    return found


def _passes(node):
    """True when the body is a single return of one call. Parentheses and await may wrap that call.
    A second call, a second statement, or a return of anything else does not match."""
    if node.type != "function_definition":
        return False
    body = node.child_by_field_name("body")
    stmts = [] if body is None else body.named_children
    if len(stmts) != 1 or stmts[0].type != "return_statement":
        return False
    expr = next(iter(stmts[0].named_children), None)
    while expr is not None and expr.type in ("parenthesized_expression", "await"):
        expr = next(iter(expr.named_children), None)
    return expr is not None and expr.type == "call" and _calls(body) == 1


def _source(text, start, end):
    """The definition's own lines, exactly, and a note when they were cut to CAP. `lines` stays the
    real span either way, so a cut box still says where the rest is."""
    shown = end if end - start + 1 <= CAP else start + CAP - 1
    info = {"lines": [start, end], "text": "\n".join(text.split("\n")[start - 1 : shown])}
    if shown < end:
        info["rest"] = (
            f"the rest is in the file, line {end}."
            if shown + 1 == end
            else f"the rest is in the file, lines {shown + 1}-{end}."
        )
    return info


def python_file(name, source):
    """The box for the Python file called `name` with these bytes, and its note if any: why
    Python does not accept them, else that they are not UTF-8 so not read, else that
    tree-sitter misreads code Python accepts. A file that reads cleanly holds a child box for
    each top-level function and class, and an arrow for a direct call of one of those names.
    A parameter, local, import, with or except name, comprehension variable, or lambda
    parameter of that name is not the call. A default value is not a parameter, and global
    keeps the call. A name defined twice points at the last one, which is the one Python runs."""
    note = _note(source)
    definitions, decoded = [], None
    if not note:
        text, note = _text(source)
        if not note:
            root = PYTHON.parse(text).root_node
            if root.has_error:
                note = "tree-sitter could not read it all, though Python accepts it"
            else:
                decoded, definitions = text.decode(), _definitions(root)
    box = {"id": name, "labels": [{"text": t} for t in (name, note) if t]}
    if definitions:
        children, at = [], {}
        for ident, is_class, start, end, node in definitions:
            info = _source(decoded, start, end)
            if _passes(node):
                info["proven"] = PASSES
            child = f"{name}:{ident}:{start}"
            at[ident] = child  # the last def is the one a call reaches
            children.append(
                {
                    "id": child,
                    "labels": [{"text": ident if is_class else f"{ident}()"}],
                    "asbuilt": info,
                }
            )
        edges, seen = [], set()
        for ident, _, start, _, node in definitions:
            for callee in _direct_calls(node, _bound(node)):
                pair = (f"{ident}:{start}", callee)
                if callee not in at or pair in seen:
                    continue  # a local, not a top-level box, or already drawn
                seen.add(pair)
                edges.append(
                    {
                        "id": f"{name}:{ident}:{start}>{callee}",
                        "sources": [f"{name}:{ident}:{start}"],
                        "targets": [at[callee]],
                    }
                )
        box["children"] = children
        if edges:
            box["edges"] = edges
    return box


_SKIP_DIRS = {"tests", "__pycache__", "venv", "env", "node_modules", "build", "dist", "site-packages"}


def _py_files(folder):
    """(relative path, file) for each Python file under folder. A skipped directory, a hidden
    directory, or a directory symlink is left out. A file whose name contains "test" is kept."""
    found, todo = [], [folder]
    while todo:
        current = todo.pop()
        try:
            entries = sorted(current.iterdir(), reverse=True)
        except OSError as exc:
            why = exc.strerror or type(exc).__name__
            raise PlanError(f"{current} cannot be read: {why}") from None
        for path in entries:
            if path.is_dir():
                if path.name.startswith(".") or path.name in _SKIP_DIRS or path.is_symlink():
                    continue
                todo.append(path)
            elif path.is_file() and path.suffix == ".py":
                found.append((path.relative_to(folder).as_posix(), path))
    found.sort()
    return found


def _place(files):
    """Each directory in a relative path is a box inside its parent, and the file is inside that."""
    root, boxes = {"children": []}, {}
    boxes[""] = root
    for rel, path in files:
        parent, parts = "", rel.split("/")
        for part in parts[:-1]:
            key = f"{parent}/{part}" if parent else part
            if key not in boxes:
                boxes[key] = {"id": key, "labels": [{"text": part}], "children": []}
                boxes[parent]["children"].append(boxes[key])
            parent = key
        box = python_file(rel, read_bytes(path))
        box["labels"][0]["text"] = parts[-1]
        boxes[parent]["children"].append(box)
    return root["children"]


def python_dir(folder):
    """One box for the folder, named with its real name so `.` is not blank. A file in the folder
    is `load.py:load`. A nested file is `sub/util.py:name`, inside a box for each directory."""
    try:
        name = folder.resolve().name
    except OSError as exc:
        why = exc.strerror or type(exc).__name__
        raise PlanError(f"{folder} cannot be read: {why}") from None
    files = _py_files(folder)
    if not files:
        raise PlanError(f"{name} has no Python files to chart.")
    # A trailing slash cannot be a relative file path, so mysite/mysite/ does not reuse "mysite".
    return {"id": f"{name}/", "labels": [{"text": name}], "children": _place(files)}


def _is_same(out, path):
    """True when both exist and are the same file, once OUT is resolved. A symlink counts."""
    try:
        return out.exists() and path.exists() and out.resolve().samefile(path)
    except OSError:
        return False


def _clashes(out, source):
    """The refusal line when OUT would replace a charted file or end in .py, else ""."""
    target = out.resolve()
    label = target.name
    same = _is_same(out, source)
    if not same and source.is_dir() and out.exists():
        same = any(_is_same(out, path) for _, path in _py_files(source))
    if same:
        return f"{label}: OUT is the source; it is only read"
    if target.suffix.lower() == ".py":
        return f"{label}: the chart is JSON, not a Python file"
    return ""


def main(argv=None):
    """Exit 0 with OUT written, or 2 with one line naming the file and why, and nothing written."""
    parser = argparse.ArgumentParser(
        prog="python -m asbuilt.built", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("file", type=Path, help="the Python file or folder to chart")
    parser.add_argument("out", type=Path, help="where the plan is written")
    args = parser.parse_args(argv)
    try:
        try:  # samefile raises when a path cannot be statted
            clash = _clashes(args.out, args.file)
        except OSError as exc:
            why = exc.strerror or type(exc).__name__
            raise PlanError(f"{args.file.name} cannot be read: {why}") from None
        if clash:
            raise PlanError(clash)
        if args.file.is_dir():
            box = python_dir(args.file)
        else:
            box = python_file(args.file.name, read_bytes(args.file))
        plan = {"id": "asbuilt:built", "children": [box]}
        check(plan, args.out.name)
        output.write_atomic(args.out, output.plan_bytes(plan, args.out.name))
    except PlanError as exc:
        return output.fail("asbuilt.built", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
