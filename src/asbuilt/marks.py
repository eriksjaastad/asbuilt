"""The marks a box can prove from its own code. Not from a box it calls.

A name is the module its import binds, here or at the top of the file, by the same
rule as an arrow (_scope). A parameter, a local, or a nested def hides that import.
A kind is one check below. Nested defs count, because they are not their own boxes.
Add a kind by appending a check. A later change in behavior cannot be proven, so it
is not a kind.
"""

_ORDER = ("env", "input", "writes", "calls out", "model")
# Each module path below marks a call or name at it or under it. These tuples are the checks,
# and LOOKS_FOR is read from them, so the page never lists a call that is not checked.
_ENV = (("os", "environ"), ("os", "getenv"))
_MODEL = (("openai",), ("anthropic",))
_RUN = ("run", "Popen", "call", "check_call", "check_output", "getoutput")
_SOCKET = ("socket", "create_connection", "create_server")
_OUT = (
    ("requests",),
    ("httpx",),
    ("urllib", "request"),
    ("http", "client", "HTTPConnection"),
    ("http", "client", "HTTPSConnection"),
    ("os", "system"),
    ("os", "popen"),
    *(("subprocess", name) for name in _RUN),
    *(("socket", name) for name in _SOCKET),
)
_WRITE_METHODS = ("write_text", "write_bytes")
_INPUT = "input"


def _dotted(paths):
    return [".".join(path) for path in paths]


# What each mark looks for, in the words the page shows. Nothing else is looked for, so a box
# without a mark proves only that none of these is in its own code.
LOOKS_FOR = {
    "env": _dotted(_ENV),
    "input": [f"{_INPUT}()"],
    "writes": [
        "open() with a write mode",
        "os.fdopen() with a write mode",
        "a .open() method with a write mode",
        *(f".{name}()" for name in _WRITE_METHODS),
    ],
    "calls out": _dotted(_OUT),
    "model": _dotted(_MODEL),
}


def _under(path, prefixes):
    return any(path[: len(prefix)] == prefix for prefix in prefixes)


def _chain(node):
    """The node under a chain of attributes, and those attribute names, outer last."""
    attrs = []
    while node is not None and node.type == "attribute":
        attr = node.child_by_field_name("attribute")
        if attr is None:
            return None, ()
        attrs.append(attr.text.decode())
        node = node.child_by_field_name("object")
    attrs.reverse()
    return node, tuple(attrs)


def _resolve(name, scope):
    """The module path an imported name is, 'shadow' when this box rebinds it, or None."""
    spec = scope.get(name, False)
    if spec is False:
        return None
    if spec is None or spec[0] not in ("mod", "from"):
        return "shadow"
    return spec[1] if spec[0] == "mod" else spec[1] + (spec[2],)


def resolve(node, scope):
    """The module path of a name or attribute chain, or ('builtin', name) for open and input."""
    if node is None:
        return None
    root, attrs = _chain(node)
    if root is None or root.type != "identifier":
        return None
    found = _resolve(root.text.decode(), scope)
    if found == "shadow":
        return None
    if found is None:
        return ("builtin", root.text.decode()) if not attrs and root.text.decode() in ("input", "open") else None
    return found + attrs


def client_module(path):
    """The module a marked constructor stands for: OpenAI(), Anthropic(), requests.Session()."""
    if path in {("openai", "OpenAI"), ("anthropic", "Anthropic"), ("requests", "Session")}:
        return (path[0],)
    return None


def _mode(text):
    """The mode body when a string literal is a file mode, else ''."""
    if len(text) < 2 or text[0] not in "\"'" or text[-1] != text[0]:
        return ""
    body = text[1:-1]
    return body if body and all(c in "rwaxbt+" for c in body) else ""


def _write_mode(text):
    """True when a string literal is a mode that writes, not a file name. The quotes stay."""
    return any(c in "wax+" for c in _mode(text))


def call_arguments(call):
    """Split a call's arguments into (positional, named, has_splat)."""
    args = call.child_by_field_name("arguments")
    positional, named, splat = [], {}, False
    for child in args.named_children if args is not None else ():
        if child.type == "keyword_argument":
            key, val = child.child_by_field_name("name"), child.child_by_field_name("value")
            if key is not None and val is not None:
                named[key.text.decode()] = val
        elif child.type in ("dictionary_splat", "list_splat"):
            splat = True
        else:
            positional.append(child)
    return positional, named, splat


def _open_writes(call, first=False):
    """True when an open-like call's mode string writes. A method may take the mode first
    (Path.open('w')) or second (ZipFile.open(name, 'w'))."""
    positional, named, _splat = call_arguments(call)
    mode = named.get("mode")
    if mode is not None and mode.type == "string":
        return _write_mode(mode.text.decode())
    if len(positional) > 1 and positional[1].type == "string" and _mode(positional[1].text.decode()):
        return _write_mode(positional[1].text.decode())
    return first and len(positional) > 0 and positional[0].type == "string" and _write_mode(positional[0].text.decode())


_COND = {
    "if_statement", "elif_clause", "else_clause", "try_statement", "except_clause",
    "finally_clause", "while_statement", "with_statement", "match_statement", "case_clause",
}


def _one_name(child):
    """A parameter's own name. The default and type annotation are not names it binds."""
    if child.type == "identifier":
        return child.text.decode()
    ident = next((c for c in child.named_children if c.type == "identifier"), None)
    return None if ident is None else ident.text.decode()


def _pattern(node):
    """Identifiers bound by an assignment target, not the value or a type."""
    if node is None:
        return ()
    if node.type == "identifier":
        return (node.text.decode(),)
    if node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        return tuple(name for c in node.named_children for name in _pattern(c))
    return ()


def _dots(node):
    return tuple(c.text.decode() for c in node.named_children if c.type == "identifier")


def _imports(stmt):
    """Names bound by one import statement: (name, spec) pairs. Relative imports bind None."""
    found = []
    if stmt.type == "import_from_statement":
        first = next(iter(stmt.named_children), None)
        mod = _dots(first) if first is not None and first.type == "dotted_name" else None
        for item in stmt.named_children[1:]:
            if item.type == "dotted_name":
                name = _dots(item)[0]
                found.append((name, ("from", mod, name) if mod else None))
            elif item.type == "aliased_import":
                dotted = next(c for c in item.named_children if c.type == "dotted_name")
                alias = next(c for c in item.named_children if c.type == "identifier")
                found.append((alias.text.decode(), ("from", mod, _dots(dotted)[0]) if mod else None))
    elif stmt.type == "import_statement":
        for item in stmt.named_children:
            if item.type == "dotted_name":
                parts = _dots(item)
                found.append((parts[0], ("mod", (parts[0],))))
            elif item.type == "aliased_import":
                dotted = next(c for c in item.named_children if c.type == "dotted_name")
                alias = next(c for c in item.named_children if c.type == "identifier")
                found.append((alias.text.decode(), ("mod", _dots(dotted))))
    return found


def _client_spec(node, scope):
    """("mod", module) when this assignment is one call of a marked constructor, else None."""
    kids = list(node.named_children)
    if len(kids) < 2 or kids[-1].type != "call":
        return None
    fun = next(iter(kids[-1].named_children), None)
    path = resolve(fun, scope)
    module = None if path is None else client_module(path)
    return None if module is None else ("mod", module)


def scope_of(nodes, outer=None):
    """Mark scope for a sequence of statements: name -> ('from', mod, orig), ('mod', mod), or None.

    Imports under try/if count when the name is only bound to that one import (including the
    `try: import x / except ...: x = None` fallback idiom). One unconditional assignment of a
    marked client constructor (OpenAI(), Anthropic(), requests.Session()) is that module; a
    conditional or repeated binding is None.
    """
    decided = {}

    def bind(name, spec):
        prev = decided.get(name, False)
        if prev is False:
            decided[name] = spec
        elif spec is None or prev is None or prev != spec:
            decided[name] = None

    def walk(node, conditional, in_except):
        kind = node.type
        if kind in ("function_definition", "class_definition"):
            name = node.child_by_field_name("name")
            if name is not None:
                bind(name.text.decode(), None)
            return
        if kind == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                walk(inner, conditional, in_except)
            return
        if kind in ("import_from_statement", "import_statement"):
            for name, spec in _imports(node):
                bind(name, spec)
            return
        if kind == "assignment":
            kids = list(node.named_children)
            names = list(_pattern(kids[0] if kids else None))
            rhs = kids[-1] if len(kids) >= 2 else None
            if (
                in_except
                and len(names) == 1
                and rhs is not None
                and rhs.type == "none"
                and isinstance(decided.get(names[0]), tuple)
            ):
                return
            lookup = {**outer, **decided} if outer else decided
            spec = _client_spec(node, lookup) if not conditional and len(names) == 1 else None
            for name in names:
                bind(name, spec)
            return
        if kind in ("augmented_assignment", "named_expression"):
            for name in _pattern(next(iter(node.named_children), None)):
                bind(name, None)
            return
        if kind == "for_statement":
            for name in _pattern(next(iter(node.named_children), None)):
                bind(name, None)
            body = node.child_by_field_name("body")
            for child in body.named_children if body is not None else ():
                walk(child, True, in_except)
            return
        if kind == "delete_statement":
            for child in node.named_children:
                for name in _pattern(child):
                    bind(name, None)
            return
        if kind == "as_pattern":
            target = next((c for c in node.named_children if c.type == "as_pattern_target"), None)
            if target is not None:
                for name in _pattern(next(iter(target.named_children), None)):
                    bind(name, None)
            return
        nested = conditional or kind in _COND
        nested_except = in_except or kind == "except_clause"
        for child in node.named_children:
            walk(child, nested, nested_except)

    for node in nodes:
        walk(node, False, False)
    return decided


def layer(fn, scope):
    """scope plus this function's parameters and the imports and defs in its body."""
    inner, params = dict(scope), set()
    head = fn.child_by_field_name("parameters")
    for child in head.named_children if head is not None else ():
        name = _one_name(child)
        if name is not None:
            params.add(name)
            inner[name] = None
    body = fn.child_by_field_name("body")
    if body is not None:
        for name, spec in scope_of(list(body.named_children), outer=inner).items():
            if name not in params:
                inner[name] = spec
    return inner


def _env(path):
    return _under(path, _ENV)


def _calls_out(path):
    # Building an exception (`raise http.client.LineTooLong(...)`, `requests.exceptions.HTTPError`)
    # contacts nothing, so a name that reads as an exception class is never a call out.
    last = path[-1] if path else ""
    if last.endswith(("Error", "Exception", "Warning")) or "exceptions" in path:
        return False
    return _under(path, _OUT)


def _imported(node, scope):
    """True when this attribute chain starts at a name the file imported."""
    root, _attrs = _chain(node)
    if root is None or root.type != "identifier":
        return False
    return isinstance(_resolve(root.text.decode(), scope), tuple)


def _on_call(node, scope, add):
    fun = next(iter(node.named_children), None)
    _root, attrs = _chain(fun)
    if attrs and attrs[-1] in _WRITE_METHODS:
        add("writes")
    # Path.open("w") takes the mode first. gzip.open(path, "wt") takes it second.
    if attrs and attrs[-1] == "open" and _open_writes(node, first=not _imported(fun, scope)):
        add("writes")
    path = resolve(fun, scope)
    if path is None:
        return
    if path[0] == "builtin":
        if path[1] == _INPUT:
            add("input")
        elif path[1] == "open" and _open_writes(node):
            add("writes")
        return
    if _env(path):
        add("env")
    if _under(path, _MODEL):
        add("model")
    elif _calls_out(path):
        add("calls out")
    if path[:2] == ("os", "fdopen") and _open_writes(node):
        add("writes")


def _on_name(node, scope, add):
    path = resolve(node, scope)
    if path is not None and path[0] != "builtin" and _env(path):
        add("env")


def reads_env(path):
    """True when this resolved path is os.environ or os.getenv."""
    return bool(path) and path[0] != "builtin" and _env(path)


def reaches_out(path):
    """True when this resolved path is one of the calls-out marks."""
    return bool(path) and path[0] != "builtin" and _calls_out(path)


def of(node, module):
    """The mark names this box's own tree proves, in display order. [] when it proves none."""
    found = []

    def add(kind):
        if kind not in found:
            found.append(kind)

    def visit(cur, scope):
        if cur.type in ("import_statement", "import_from_statement"):
            return
        if cur.type == "function_definition":
            inner = layer(cur, scope)
            body = cur.child_by_field_name("body")
            for child in body.children if body is not None else ():
                visit(child, inner)
            return
        if cur.type == "class_definition":
            # A class body binds names for the class, not for the methods. Python does not
            # see `def open` or `def run` inside a sibling method.
            body = cur.child_by_field_name("body")
            inner = dict(scope)
            if body is not None:
                inner.update(scope_of(list(body.named_children), outer=scope))
            for child in body.children if body is not None else ():
                method = child.type == "function_definition" or (
                    child.type == "decorated_definition"
                    and (child.child_by_field_name("definition") or child).type == "function_definition"
                )
                visit(child, scope if method else inner)
            return
        if cur.type == "call":
            _on_call(cur, scope, add)
        elif cur.type in ("identifier", "attribute"):
            _on_name(cur, scope, add)
        for child in cur.children:
            visit(child, scope)

    visit(node, dict(module or {}))
    return [kind for kind in _ORDER if kind in found]
