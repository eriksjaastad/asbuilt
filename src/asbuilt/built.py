"""As-built chart: the code, read into the ELK JSON the page draws.

python -m asbuilt.built FILE OUT writes one Python file as a plan the page can draw. The file is
one box, and each top-level function and class is a box inside it. OUT is only written. A missing
file, or OUT being the source file, prints one line and writes nothing.

A file Python accepts, as UTF-8, holds those definitions and the lines they occupy. Otherwise the
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


def _direct_calls(node):
    """Bare names called in `node`'s body. A call on an attribute, and a call inside a nested
    function or class, are skipped: they are not this box calling another top-level name."""
    body = node.child_by_field_name("body")
    found, todo = [], list(reversed(body.children)) if body is not None else []
    while todo:
        cur = todo.pop()
        if cur.type in ("function_definition", "class_definition", "decorated_definition"):
            continue
        if cur.type == "call":
            fun = next(iter(cur.named_children), None)
            if fun is not None and fun.type == "identifier":
                found.append(fun.text.decode())
        todo.extend(reversed(cur.children))
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
    each top-level function and class."""
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
            at.setdefault(ident, child)
            children.append(
                {
                    "id": child,
                    "labels": [{"text": ident if is_class else f"{ident}()"}],
                    "asbuilt": info,
                }
            )
        edges, seen = [], set()
        for ident, _, start, _, node in definitions:
            for callee in _direct_calls(node):
                pair = (f"{ident}:{start}", callee)
                if callee not in at or pair in seen:
                    continue  # not a top-level box in this file, or already drawn
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


def main(argv=None):
    """Exit 0 with OUT written, or 2 with one line naming the file and why, and nothing written."""
    parser = argparse.ArgumentParser(
        prog="python -m asbuilt.built", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("file", type=Path, help="the Python file to chart")
    parser.add_argument("out", type=Path, help="where the plan is written")
    args = parser.parse_args(argv)
    try:
        try:  # samefile raises when a path cannot be statted
            same = args.out.exists() and args.file.exists() and args.out.samefile(args.file)
        except OSError as exc:
            why = exc.strerror or type(exc).__name__
            raise PlanError(f"{args.file.name} cannot be read: {why}") from None
        if same:
            raise PlanError(f"{args.out.name}: OUT is the source file; it is only read")
        box = python_file(args.file.name, read_bytes(args.file))
        plan = {"id": "asbuilt:built", "children": [box]}
        check(plan, args.out.name)
        output.write_atomic(args.out, output.plan_bytes(plan, args.out.name))
    except PlanError as exc:
        return output.fail("asbuilt.built", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
