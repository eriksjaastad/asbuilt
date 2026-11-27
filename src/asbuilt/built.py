"""As-built chart: the code, read into the ELK JSON the page draws.

python -m asbuilt.built PATH OUT writes one Python file, or a folder of Python files, as a
plan the page can draw. A file is one box, and each top-level function and class is a box inside
it. A folder holds a box per file and a box per subfolder that has one. A nested file's id is
its path (`sub/util.py:name`). An arrow crosses files only when an import proves the call. A
folder a file puts on sys.path (insert or append), at a path the parser proves from __file__
or an absolute string, is one more import root for that file and the files its imports reach
(see links); a module two files could be is not followed.
tests, __pycache__, venv, env, node_modules, build, dist, site-packages, and hidden
directories are skipped. OUT is only written. A missing path, or OUT
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
from asbuilt.marks import of as _marks
from asbuilt.marks import resolve
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


def one_name(child):
    """A parameter's own name. The default (`fn=load`) and the type are not names it binds."""
    if child.type == "identifier":
        return child.text.decode()
    ident = next((c for c in child.named_children if c.type == "identifier"), None)
    return None if ident is None else ident.text.decode()


def imported(stmt):
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


def bound(node, imports=True):
    """Names this box assigns. A call of one of them is not the top-level function of that name.
    `global load` puts the name back: the call is the top-level one."""
    names, glob = set(), set()
    params = node.child_by_field_name("parameters")
    for child in params.named_children if params is not None else ():
        name = one_name(child)
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
            if imports:
                names.update(imported(cur))
            continue
        if cur.type == "as_pattern":
            target = next((c for c in cur.named_children if c.type == "as_pattern_target"), None)
            if target is not None:
                names.update(pattern(next(iter(target.named_children), None)))
        if cur.type in ("assignment", "augmented_assignment", "for_statement", "named_expression"):
            names.update(pattern(next(iter(cur.named_children), None)))
        todo.extend(reversed(cur.children))
    return names - glob


def pattern(node):
    """Identifiers bound by an assignment target, not the value or a type."""
    if node is None:
        return ()
    if node.type == "identifier":
        return (node.text.decode(),)
    if node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        return tuple(name for c in node.named_children for name in pattern(c))
    return ()


def _scoped(node, extra):
    """Names a lambda or a comprehension binds, added to the names already bound outside it."""
    names = dict(extra)
    if node.type == "lambda":
        params = next((c for c in node.named_children if c.type == "lambda_parameters"), None)
        for child in params.named_children if params is not None else ():
            name = one_name(child)
            if name is not None:
                names[name] = None
    else:
        for clause in node.named_children:
            if clause.type == "for_in_clause":
                names.update(dict.fromkeys(pattern(next(iter(clause.named_children), None))))
    return names


def _direct_calls(node, local, attrs=None, body=None):
    """(name, spec) for each bare name called in `node`'s body, in the functions and classes
    nested there too, so a call in a method or an inner function belongs to this box.
    `local(scope)` maps the names a function or class binds to how: one import's spec, or None
    for any other binding, which is then not the call. spec is the innermost import that binds
    the name, or None when no enclosing scope binds it. A method does not see its class body's
    names, as in Python. A call on an attribute is recorded in `attrs` as (object, name, spec)
    when the object is a bare name; a chain is not. `body` given walks that block as
    `node`'s body, for code outside any function, such as an `if __name__` block."""
    nested = ("function_definition", "class_definition")
    comps = ("list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression")
    found, todo = [], []

    def enter(scope, names, inherit):
        # The head (defaults, bases) runs where the def is; the body runs in its own scope.
        block = body if scope is node and body is not None else scope.child_by_field_name("body")
        if scope is not node:
            todo.extend((c, names, inherit) for c in reversed(scope.children) if c != block)
        if block is not None:
            inner = {**inherit, **local(scope)}
            keep = inner if scope.type == "function_definition" else inherit
            todo.extend((c, inner, keep) for c in reversed(block.children))

    enter(node, {}, {})
    while todo:
        cur, names, inherit = todo.pop()
        if cur.type in nested:
            enter(cur, names, inherit)
            continue
        if cur.type == "lambda" or cur.type in comps:
            todo.extend((c, _scoped(cur, names), inherit) for c in reversed(cur.children))
            continue
        if cur.type == "call":
            # A name no enclosing scope binds is the module's (spec None). One bound by a single
            # import there is that import. One bound any other way is not the call.
            fun = next(iter(cur.named_children), None)
            if fun is not None and fun.type == "identifier":
                how = names.get(fun.text.decode(), False)
                if how is not None:
                    found.append((fun.text.decode(), how or None))
            elif attrs is not None and fun is not None and fun.type == "attribute":
                obj, attr = fun.child_by_field_name("object"), fun.child_by_field_name("attribute")
                if obj is not None and obj.type == "identifier" and attr is not None:
                    how = names.get(obj.text.decode(), False)
                    if how is not None:
                        attrs.append((obj.text.decode(), attr.text.decode(), how or None))
        todo.extend((c, names, inherit) for c in reversed(cur.children))
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
    each top-level function and class, and an arrow for a direct call of one of those names,
    or `Cls.attr()` of a class among them, made in its body, a method, or an inner function.
    A parameter, local, import, with or except name, comprehension variable, or lambda
    parameter of that name is not the call. A default value is not a parameter, and global
    keeps the call. A name defined twice points at the last one, which is the one Python runs."""
    note = _note(source)
    definitions, decoded, scope = [], None, {}
    if not note:
        text, note = _text(source)
        if not note:
            root = PYTHON.parse(text).root_node
            if root.has_error:
                note = "tree-sitter could not read it all, though Python accepts it"
            else:
                decoded, definitions = text.decode(), _definitions(root)
                scope = _scope(list(root.named_children), None, False)
                from asbuilt.plain import for_box as plain_for_box
                from asbuilt.security import constants as security_constants
                from asbuilt.security import for_box

                constants = security_constants(list(root.named_children))
    box = {"id": name, "labels": [{"text": t} for t in (name, note) if t]}
    if definitions:
        children, at = [], {}
        for ident, is_class, start, end, node in definitions:
            info = _source(decoded, start, end)
            if _passes(node):
                info["proven"] = PASSES
            badges = _marks(node, scope)
            if badges:
                info["badges"] = badges
            secured = for_box(node, scope, constants)
            if secured:
                info["security"] = secured
            lines = plain_for_box(node, scope, constants)
            if lines:
                info["plain"] = lines
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
        classes = {ident for ident, is_class, *_ in definitions if is_class} - {
            ident for ident, is_class, *_ in definitions if not is_class
        }
        for ident, _, start, _, node in definitions:
            attrs = []
            called = [c for c, how in _direct_calls(node, lambda d: dict.fromkeys(bound(d)), attrs) if how is None]
            # `Cls.make()` uses the class box when Cls is only ever a class in this file.
            called += [obj for obj, _, how in attrs if how is None and obj in classes]
            for callee in called:
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


_NAMED_DIRS = {"tests", "venv", "env", "node_modules", "build", "dist", "site-packages"}
_SKIP_DIRS = _NAMED_DIRS | {"__pycache__"}


def _py_files(folder):
    """(relative path, file) for each Python file under folder, the named skips, and __pycache__.
    A hidden directory or a directory symlink is left out too, and is not named. A file whose
    name contains "test" is kept."""
    found, named, quiet, todo = [], [], [], [folder]
    while todo:
        current = todo.pop()
        try:
            entries = sorted(current.iterdir(), reverse=True)
        except OSError as exc:
            why = exc.strerror or type(exc).__name__
            raise PlanError(f"{current} cannot be read: {why}") from None
        for path in entries:
            if path.is_dir():
                rel = path.relative_to(folder).as_posix()
                if path.name in _NAMED_DIRS:
                    named.append(rel)
                elif path.name == "__pycache__":
                    quiet.append(rel)
                if path.name.startswith(".") or path.name in _SKIP_DIRS or path.is_symlink():
                    continue
                todo.append(path)
            elif path.is_file() and path.suffix == ".py":
                found.append((path.relative_to(folder).as_posix(), path))
    found.sort()
    return found, named, quiet


def _place(files):
    """Each directory in a relative path is a box inside its parent, and the file is inside that."""
    root, boxes = {"children": []}, {}
    boxes[""] = root
    for rel, path in files:
        parent, parts = "", rel.split("/")
        for part in parts[:-1]:
            key = f"{parent}/{part}" if parent else part
            if key not in boxes:
                # A trailing slash is not a file path, so the folder mysite and the subfolder
                # mysite/ are different boxes. The top folder keeps its plain name.
                boxes[key] = {"id": f"{key}/", "labels": [{"text": part}], "children": []}
                boxes[parent]["children"].append(boxes[key])
            parent = key
        box = python_file(rel, read_bytes(path))
        box["labels"][0]["text"] = parts[-1]
        boxes[parent]["children"].append(box)
    return root["children"]


def _dots(node):
    return tuple(c.text.decode() for c in node.named_children if c.type == "identifier")


def _from_mod(stmt, own, init=False):
    """The module tuple a from-import names, or None. In `__init__.py`, `own` is the package."""
    first = next(iter(stmt.named_children), None)
    if first is None:
        return None
    if first.type == "dotted_name":
        return _dots(first)
    if first.type != "relative_import" or not own:
        return None
    prefix = next((c for c in first.named_children if c.type == "import_prefix"), None)
    level = 0 if prefix is None else prefix.text.decode().count(".")
    dotted = next((c for c in first.named_children if c.type == "dotted_name"), None)
    rest = () if dotted is None else _dots(dotted)
    base = own if init else own[:-1]
    # `..` that climbs past the top package is not a module we can see.
    if level > len(base):
        return None
    return base[: len(base) - (level - 1)] + rest


def _imports(stmts, own, init=False):
    """Names bound by these statements: from-imports as (module, original), import as the module."""
    calls, mods = {}, {}
    for stmt in stmts:
        if stmt.type == "import_from_statement":
            mod = _from_mod(stmt, own, init)
            for item in stmt.named_children[1:]:
                if item.type == "dotted_name" and mod:
                    name = _dots(item)[0]
                    calls[name] = (mod, name)
                elif item.type == "aliased_import" and mod:
                    dotted = next(c for c in item.named_children if c.type == "dotted_name")
                    alias = next(c for c in item.named_children if c.type == "identifier")
                    calls[alias.text.decode()] = (mod, _dots(dotted)[0])
        elif stmt.type == "import_statement":
            for item in stmt.named_children:
                if item.type == "dotted_name":
                    parts = _dots(item)
                    mods[parts[0]] = (parts[0],)
                elif item.type == "aliased_import":
                    dotted = next(c for c in item.named_children if c.type == "dotted_name")
                    alias = next(c for c in item.named_children if c.type == "identifier")
                    mods[alias.text.decode()] = _dots(dotted)
    return calls, mods


def _typing(node):
    """True when the test is the name TYPE_CHECKING, so the import is the one the file means."""
    cond = next(iter(node.named_children), None)
    return cond is not None and cond.type == "identifier" and cond.text.decode() == "TYPE_CHECKING"


COND = {
    "if_statement", "elif_clause", "else_clause", "try_statement", "except_clause",
    "finally_clause", "while_statement", "with_statement", "match_statement", "case_clause",
}


def _client_spec(node, decided):
    """("mod", module) when this assignment is one call of a marked constructor, else None."""
    from asbuilt.marks import client_module, resolve

    kids = list(node.named_children)
    if len(kids) < 2 or kids[-1].type != "call":
        return None
    fun = next(iter(kids[-1].named_children), None)
    path = resolve(fun, decided)
    module = None if path is None else client_module(path)
    return None if module is None else ("mod", module)


def _scope(nodes, own, init, local=False):
    """name -> ("from", module, original) or ("mod", module), or None when it is not one import.

    None means the name is bound some other way too, or only inside if/try, so it gets no arrow.
    `local` is a function body: a name bound there only by one import, even inside if/try, can
    only be called once that import ran, so the import is what it calls.
    One assignment of a marked constructor is that module; a second binding is not.
    Defs and classes count as bindings and are not entered."""
    decided = {}

    def bind(name, spec):
        # The same module imported again (`import pkg` and `import pkg.sub`) is still that import.
        # A second def is a different binding, so the name is no longer that one function.
        prev = decided.get(name, False)
        if prev is False:
            decided[name] = spec
        elif spec is None or prev is None or prev != spec or prev[0] == "def":
            decided[name] = None

    def walk(node, conditional):
        kind = node.type
        if kind in ("function_definition", "class_definition"):
            name = node.child_by_field_name("name")
            if name is not None:
                bind(name.text.decode(), ("def",))
            return
        if kind == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                walk(inner, conditional)
            return
        if kind in ("import_from_statement", "import_statement"):
            calls, mods = _imports([node], own, init)
            for name, pair in calls.items():
                bind(name, None if conditional and not local else ("from", pair[0], pair[1]))
            for name, mod in mods.items():
                bind(name, None if conditional and not local else ("mod", mod))
            return
        if kind == "assignment":
            # One assignment of OpenAI(), Anthropic(), or requests.Session() is that module.
            # A second binding, of any kind, is not. The same rule as an import.
            names = list(pattern(next(iter(node.named_children), None)))
            spec = _client_spec(node, decided) if not conditional and len(names) == 1 else None
            for name in names:
                bind(name, spec)
            return
        if kind in ("augmented_assignment", "named_expression"):
            for name in pattern(next(iter(node.named_children), None)):
                bind(name, None)
            return
        if kind == "for_statement":
            for name in pattern(next(iter(node.named_children), None)):
                bind(name, None)
            body = node.child_by_field_name("body")
            for child in body.named_children if body is not None else ():
                walk(child, conditional)
            return
        if kind == "delete_statement":
            for child in node.named_children:
                for name in pattern(child):
                    bind(name, None)
            return
        if kind == "as_pattern":
            target = next((c for c in node.named_children if c.type == "as_pattern_target"), None)
            if target is not None:
                for name in pattern(next(iter(target.named_children), None)):
                    bind(name, None)
            return
        if kind == "if_statement" and not conditional and _typing(node):
            for child in node.named_children:
                walk(child, child.type in ("elif_clause", "else_clause"))
            return
        nested = conditional or kind in COND
        for child in node.named_children:
            walk(child, nested)

    for node in nodes:
        walk(node, False)
    return decided


def _module(rel, pkgs, folder_name):
    """The public module of this file, or None when it is not an importable module."""
    init = rel.endswith("__init__.py")
    if init:
        directory = rel[: -len("__init__.py")].rstrip("/")
        parts = tuple(p for p in directory.split("/") if p)
    else:
        directory = rel.rsplit("/", 1)[0] if "/" in rel else ""
        parts = tuple(rel[:-3].split("/"))
    if "" in pkgs and (directory in pkgs or directory == ""):
        return (folder_name, *parts)
    if directory in pkgs:
        dirs = directory.split("/")
        start = next(
            i
            for i in range(len(dirs))
            if "/".join(dirs[: i + 1]) in pkgs and (i == 0 or "/".join(dirs[:i]) not in pkgs)
        )
        return parts[start:]
    if directory == "" and not init:
        return parts
    return None


def _under(rel, root, pkgs):
    """The module this file is with folder `root` on sys.path, or None: each folder between
    them must be a package."""
    if root:
        if not rel.startswith(root + "/"):
            return None
        rel = rel[len(root) + 1 :]
    dirs, parts = rel.split("/")[:-1], rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    inside = (f"{root}/{'/'.join(dirs[: i + 1])}".lstrip("/") for i in range(len(dirs)))
    return tuple(parts) if parts and all(d in pkgs for d in inside) else None


def _def_index(boxes, folder_name, roots=()):
    """{(module, name): box id}, {file: module}, {file: the module its boxes are indexed under},
    and {None, or a folder on sys.path (`roots`): {module: the files it names}}. Two files for
    one module and name make it None, so it is not used. A file only a sys.path folder names is
    indexed under ("sys.path", folder, *module), which no import writes, so only a file that
    has that folder on sys.path reaches it."""
    pkgs = {rel[: -len("__init__.py")].rstrip("/") for rel in boxes if rel.endswith("__init__.py")}
    index, own, key, tables = {}, {}, {}, {r: {} for r in (None, *roots)}
    for rel, box in boxes.items():
        own[rel] = mod = _module(rel, pkgs, folder_name)
        if mod:
            tables[None].setdefault(mod, set()).add(rel)
        for root in roots:
            under = _under(rel, root, pkgs)
            if under:
                tables[root].setdefault(under, set()).add(rel)
                mod = mod or ("sys.path", root, *under)
        key[rel] = mod
        if not mod:
            continue
        prefix = rel + ":"
        slot = index.setdefault(mod, {})
        for child in box.get("children", []):
            cid = child["id"]
            if not cid.startswith(prefix):
                continue
            ident = cid[len(prefix) :].rpartition(":")[0]
            slot[ident] = None if ident in slot and slot[ident] != cid else cid
    return index, own, key, tables


def _imported(root, own, init):
    """(line, runs, modules, from-names) for each import statement in this file: the modules it
    names, a parent package included; whether it runs, which `if TYPE_CHECKING:` does not; and
    (module, name) for each name a from-import takes, as the name may be a submodule."""
    found, todo = [], [(root, True)]
    while todo:
        cur, runs = todo.pop()
        if cur.type == "if_statement" and _typing(cur):
            runs = False
        if cur.type not in ("import_statement", "import_from_statement"):
            todo.extend((c, runs) for c in cur.named_children)
            continue
        subs = []
        if cur.type == "import_statement":
            items = [c if c.type == "dotted_name" else c.child_by_field_name("name") for c in cur.named_children]
            mods = [_dots(c) for c in items if c is not None]
        else:
            mod = _from_mod(cur, own, init)
            mods = [mod] if mod else []
            for item in cur.named_children[1:] if mod else ():
                dotted = item if item.type == "dotted_name" else item.child_by_field_name("name")
                if dotted is not None and dotted.type == "dotted_name":
                    subs.append((mod, _dots(dotted)[0]))
        named = {mod[:i] for mod in mods for i in range(1, len(mod) + 1)}
        found.append((cur.start_point[0] + 1, runs, named, subs))
    return found


def tree(source):
    """The tree-sitter root of bytes Python accepts as UTF-8 and tree-sitter reads, else None."""
    if _note(source):
        return None
    text, note = _text(source)
    if note:
        return None
    root = PYTHON.parse(text).root_node
    return None if root.has_error else root


def _live(index, scopes, mod, name):
    """The box for `name` in `mod`, only when that file binds the name once, as this def."""
    cid = None if mod is None else index.get(mod, {}).get(name)
    if not cid:
        return None
    rel = cid.rsplit(":", 2)[0]
    if scopes.get(rel, {}).get(name) != ("def",):
        return None
    return cid


def _attr_mod(spec, scopes, modules):
    """The module `obj.attr` calls on. A name bound in the package's __init__ is that object,
    not the submodule, unless the binding is the submodule itself."""
    if not spec:
        return None
    if spec[0] == "mod":
        return spec[1]
    if spec[0] != "from":
        return None
    sub = spec[1] + (spec[2],)
    init = next((r for r, m in modules.items() if m == spec[1] and r.endswith("__init__.py")), None)
    bound = None if init is None else scopes.get(init, {}).get(spec[2], False)
    if bound is False:
        return sub
    same = bound and (bound[0] == "mod" and bound[1] == sub or bound[0] == "from" and bound[1] == spec[1] and bound[2] == spec[2])
    return sub if same else None


def _renamed(scope, canon):
    """`scope` with each module a folder on sys.path names given as its file's module, or None
    when two files could be that module."""
    if not canon:
        return scope
    out = {}
    for name, spec in scope.items():
        if spec and spec[0] in ("from", "mod") and spec[1] in canon:
            spec = None if canon[spec[1]] is None else (spec[0], canon[spec[1]], *spec[2:])
        out[name] = spec
    return out


def local_names(own, init, canon=None):
    """`local` for _direct_calls in a file of module `own`: imports in a function or class body
    resolve; any other binding hides the name."""

    def local(scope):
        body = scope.child_by_field_name("body")
        stmts = list(body.named_children) if body is not None else []
        inner = _renamed(_scope(stmts, own, init, scope.type == "function_definition"), canon)
        return {**inner, **dict.fromkeys(bound(scope, imports=False))}

    return local


READS = {"index", "count", "copy", "__contains__", "__getitem__", "__iter__", "__len__"}  # do not change sys.path
SAME = {("pathlib", "Path"), ("os", "fspath"), ("os", "path", "abspath"), ("os", "path", "realpath"), ("os", "path", "normpath")}


def _step(parts, segment):
    """The path `parts` (relative to the charted folder, ".." above it) after one more segment."""
    if segment in ("", "."):
        return parts
    if segment == "..":
        return parts[:-1] if parts and parts[-1] != ".." else (*parts, "..")
    return (*parts, segment)


def _string(node):
    """The text of a plain string literal, or None: an f-string, bytes, an escape, anything else."""
    kids = node.children if node.type == "string" else []
    if not kids or any(c in kids[0].text.lower() for c in b"fb") or b"\\" in node.text:
        return None
    if any(k.type not in ("string_start", "string_content", "string_end") for k in kids):
        return None
    return "".join(k.text.decode() for k in kids if k.type == "string_content")


def _literal(node, top):
    """The path a plain string names, or None: a relative one is from wherever the program is
    started, which the code does not say."""
    text = _string(node)
    if text is None or not text.startswith("/"):
        return None
    try:
        return Path(text).relative_to(top).parts
    except ValueError:
        return ("..",)


def _path_of(node, rel, top, scope):
    """The path `node` names, as parts relative to the charted folder `top`, or None when only
    running it would tell. Proven: __file__, a string starting with /, Path(), str(), .parent,
    .parents[n], / or os.path.join with a string, os.path.dirname, and abspath, realpath,
    .resolve() or .absolute(), which are taken to leave the path where it is."""
    if node.type == "identifier":
        return tuple(rel.split("/")) if node.text == b"__file__" and "__file__" not in scope else None
    if node.type == "string":
        return _literal(node, top)
    if node.type == "attribute" and node.child_by_field_name("attribute").text == b"parent":
        base = _path_of(node.child_by_field_name("object"), rel, top, scope)
        return None if base is None else _step(base, "..")
    if node.type == "subscript":  # Path(__file__).parents[1]
        value, index = node.child_by_field_name("value"), node.child_by_field_name("subscript")
        if value.type == "attribute" and value.child_by_field_name("attribute").text == b"parents" and index is not None and index.type == "integer":
            idx = None
            try:
                idx = int(index.text, 0)
            except ValueError:  # governance: allow-silent SF002: an index that is not an integer literal is not provable, so no path
                idx = None
            if idx is None or idx < 0:
                return None
            base = _path_of(value.child_by_field_name("object"), rel, top, scope)
            if base is None:
                return None
            if idx >= len(base):
                return ("..",)
            for _ in range(idx + 1):
                base = _step(base, "..")
            return base
        return None
    if node.type == "binary_operator" and node.child_by_field_name("operator").text == b"/":
        base = _path_of(node.child_by_field_name("left"), rel, top, scope)
        return _joined(base, [node.child_by_field_name("right")], top)
    if node.type != "call":
        return None
    fun, args = node.child_by_field_name("function"), node.child_by_field_name("arguments").named_children
    if any(a.type in ("keyword_argument", "list_splat", "dictionary_splat") for a in args):
        return None
    if fun.type == "attribute" and fun.child_by_field_name("attribute").text in (b"resolve", b"absolute") and not args:
        return _path_of(fun.child_by_field_name("object"), rel, top, scope)
    what = ("str",) if fun.type == "identifier" and fun.text == b"str" and "str" not in scope else resolve(fun, scope)
    base = _path_of(args[0], rel, top, scope) if args else None
    if what in SAME | {("str",)} and len(args) == 1:
        return base
    if what == ("os", "path", "dirname") and len(args) == 1:
        return None if base is None else _step(base, "..")
    return _joined(base, args[1:], top) if what == ("os", "path", "join") else None


def _joined(base, nodes, top):
    """`base` joined with strings, as / and os.path.join do: one starting with / starts over."""
    for node in nodes:
        text = _string(node)
        if base is None or text is None:
            return None
        if text.startswith("/"):
            base = _literal(node, top)
            continue
        for segment in text.split("/"):
            base = _step(base, segment)
    return base


def sys_paths(rel, root, top):
    """(line, statement, folder, why) for each change this file makes to sys.path. A
    sys.path.insert or append that is a statement of its own at module level, outside any if,
    try, loop, with or `if __name__` block, at a folder the parser proves inside the charted
    folder `top`, has that folder relative to it ("" for it) and why None. Any other change, by
    a method, an assignment, += or del, or sys.path given another name, has folder None and why
    saying what is not proven. Reading sys.path, or naming one of its methods without calling
    it, is not a change. Imports inside the functions around it count."""
    file_scope, local, found, todo = _scope(list(root.named_children), None, False), local_names(None, False), [], [root]
    while todo:
        cur = todo.pop()
        todo.extend(reversed(cur.children))
        if cur.type != "attribute" or cur.child_by_field_name("attribute").text != b"path":
            continue
        scope, up, parent = dict(file_scope), [], cur.parent
        while parent is not None:
            up += [parent] if parent.type == "function_definition" else []
            parent = parent.parent
        for fn in reversed(up):  # the innermost function's names win
            scope.update(local(fn))
        if resolve(cur, scope) != ("sys", "path"):
            continue
        outer = cur.parent
        method = outer.child_by_field_name("attribute").text.decode() if outer.type == "attribute" else None
        call = outer.parent if method and outer.parent.type == "call" else None
        call = call if call is not None and call.child_by_field_name("function") == outer else None
        if call is not None and method in ("insert", "append"):
            args = call.child_by_field_name("arguments").named_children
            parts = _path_of(args[-1], rel, top, scope) if len(args) == (2 if method == "insert" else 1) else None
            top_level = call.parent.type == "expression_statement" and call.parent.parent.type == "module"
            why = (
                "it runs only when its function is called" if up
                else "it runs only under an if, try, loop or with" if not top_level
                else "the folder is known only when it runs" if parts is None
                else "the folder is outside the charted folder" if parts[:1] == ("..",)
                else None
            )
            found.append((call.start_point[0] + 1, call.text.decode(), None if why else "/".join(parts), why))
            continue
        if method and call is None:
            continue  # sys.path.append named, not called
        target = outer if outer.type == "subscript" and outer.child_by_field_name("value") == cur else cur
        stmt = call if call is not None and method not in READS else target.parent
        side = stmt.type in ("assignment", "augmented_assignment") and stmt.child_by_field_name("left") == target
        if stmt.type == "assignment" and stmt.child_by_field_name("right") == cur:
            found.append((stmt.start_point[0] + 1, stmt.text.decode(), None, "asBuilt does not follow sys.path under another name"))
        elif stmt is call or side or stmt.type == "delete_statement":
            found.append((stmt.start_point[0] + 1, stmt.text.decode(), None, "asBuilt follows only sys.path.insert and append"))
    return found


def targets(calls, attrs, file_scope, index, scopes, modules):
    """Boxes in other files that these _direct_calls results reach, proved by an import."""
    found = []
    for callee, how in calls:
        spec = how or file_scope.get(callee)
        found.append(None if not spec or spec[0] != "from" else _live(index, scopes, spec[1], spec[2]))
    for obj, attr, how in attrs:
        found.append(_live(index, scopes, _attr_mod(how or file_scope.get(obj), scopes, modules), attr))
    return [t for t in found if t]


def _cross(rel, source, index, own, scopes, modules, canon):
    """Arrows this file's top-level boxes draw to other files, proved by an import."""
    root = tree(source)
    if root is None:
        return []
    file_scope = scopes[rel]
    local = local_names(own, rel.endswith("__init__.py"), canon)
    edges, seen = [], set()
    for ident, _, start, _, node in _definitions(root):
        src = f"{rel}:{ident}:{start}"
        attrs = []
        calls = _direct_calls(node, local, attrs)
        for target in targets(calls, attrs, file_scope, index, scopes, modules):
            if (src, target) not in seen:
                seen.add((src, target))
                edges.append({"id": f"{src}>{target}", "sources": [src], "targets": [target]})
    return edges


def python_dir(folder):
    """One box for the folder, named with its real name so `.` is not blank. A file in the folder
    is `load.py:load`. A nested file is `sub/util.py:name`, inside a box for each directory."""
    box, named, _, _ = python_dir_links(folder)
    return box, named


def python_dir_links(folder):
    """python_dir's box and skipped names, plus the files and links() it was built from, so a
    caller that needs them does not walk and parse the folder again."""
    try:
        name = folder.resolve().name
    except OSError as exc:
        why = exc.strerror or type(exc).__name__
        raise PlanError(f"{folder} cannot be read: {why}") from None
    files, named, quiet = _py_files(folder)
    if not files:
        skipped = ", ".join(sorted(named + quiet))
        extra = f" Skipped {skipped}." if skipped else ""
        raise PlanError(f"{name} has no Python files to chart.{extra}")
    box = {"id": name, "labels": [{"text": name}], "children": _place(files)}
    found, index, modules, scopes, canon, paths = links(box, files, name, folder.resolve())
    for rel, path in files:
        extra = _cross(rel, read_bytes(path), index, modules.get(rel), scopes, modules, canon.get(rel))
        if extra:
            found[rel].setdefault("edges", []).extend(extra)
    return box, named, files, (found, index, modules, scopes, canon, paths)


def links(box, files, name, top):
    """What an arrow across files is proved from: each file's box by path, the index of
    definitions, each file's module, how each file binds its top-level names, {file: {module
    named by a folder on sys.path: the module it is, or None}}, and {file: its sys_paths()
    against the charted folder `top`}.

    A proven sys.path folder is an import root for the imports below the insert in the file
    that adds it, and for every import in each file those load, transitively, as those load
    after it in the same process; no other file or import uses it. Which file a module name
    means is decided by every root in effect there; a name two files could be is not followed,
    as which one Python loads depends on the order of sys.path, and neither is a module one
    file imports both with and without a root."""
    found, todo = {}, [box]
    for cur in todo:
        todo += cur.get("children", [])
        if cur["id"].endswith(".py"):
            found[cur["id"]] = cur
    trees = {rel: tree(read_bytes(path)) for rel, path in files}
    paths = {rel: [] if root is None else sys_paths(rel, root, top) for rel, root in trees.items()}
    below = {rel: {} for rel in trees}  # {file: {root: the line of its first insert}}
    for rel, changes in paths.items():
        for line, _, folder, why in changes:
            if why is None:
                below[rel][folder] = min(line, below[rel].get(folder, line))
    index, modules, key, tables = _def_index(found, name, sorted({r for at in below.values() for r in at}))
    init = {rel: rel.endswith("__init__.py") for rel in trees}
    raw = {rel: {} if root is None else _scope(list(root.named_children), modules.get(rel), init[rel]) for rel, root in trees.items()}
    imports = {rel: [] if root is None else _imported(root, modules.get(rel), init[rel]) for rel, root in trees.items()}
    on = {rel: set() for rel in trees}  # roots in effect in the whole file

    def at(rel, line):
        return on[rel] | {r for r, after in below[rel].items() if line > after}

    def files_of(mod, here):
        return set().union(*(tables[r].get(mod, set()) for r in (None, *sorted(here))))

    todo = [rel for rel in trees if below[rel]]
    while todo:  # ends: each step adds a root to a file, and there are finitely many
        rel = todo.pop()
        for line, runs, mods, subs in imports[rel]:
            here = at(rel, line)
            if not runs or not here:
                continue
            loads = [files_of(m, here) for m in mods]
            for mod, sub in subs:  # `from pkg import sub` loads pkg/sub.py, unless pkg binds sub itself
                pkg = files_of(mod, here)
                if len(pkg) == 1 and raw[next(iter(pkg))].get(sub, False) is False:
                    loads.append(files_of((*mod, sub), here))
            for other in [next(iter(f)) for f in loads if len(f) == 1]:
                if not here <= on[other]:
                    on[other] |= here
                    todo.append(other)
    canon = {}
    for rel in trees:
        means = {}
        for line, _, mods, _ in imports[rel]:
            here = at(rel, line)
            for mod in mods:
                rels = files_of(mod, here) if any(mod in tables[r] for r in here) else None
                one = mod if rels is None else key[next(iter(rels))] if len(rels) == 1 else None
                means.setdefault(mod, set()).add(one)
        renamed = {mod: next(iter(m)) if len(m) == 1 else None for mod, m in means.items()}
        renamed = {mod: m for mod, m in renamed.items() if m != mod}
        if renamed:
            canon[rel] = renamed
    scopes = {rel: _renamed(raw[rel], canon.get(rel)) for rel in trees}
    return found, index, modules, scopes, canon, paths


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
        same = any(_is_same(out, path) for _, path in _py_files(source)[0])
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
        skipped = []
        if args.file.is_dir():
            box, skipped = python_dir(args.file)
        else:
            box = python_file(args.file.name, read_bytes(args.file))
        plan = {"id": "asbuilt:built", "children": [box]}
        check(plan, args.out.name)
        output.write_atomic(args.out, output.plan_bytes(plan, args.out.name))
        if skipped:
            print(f"asbuilt.built: skipped {', '.join(sorted(skipped))}", file=sys.stderr)
    except PlanError as exc:
        return output.fail("asbuilt.built", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
