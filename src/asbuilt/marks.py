"""The marks a box can prove from its own code. Not from a box it calls.

A name is the module its import binds, here or at the top of the file, by the same
rule as an arrow (_scope). A parameter, a local, or a nested def hides that import.
A kind is one check below. Nested defs count, because they are not their own boxes.
Add a kind by appending a check. A later change in behavior cannot be proven, so it
is not a kind.
"""

_ORDER = ("env", "input", "writes", "calls out", "model")
_MODEL = {("openai",), ("anthropic",)}
_OUT = {("requests",), ("httpx",)}
_RUN = ("run", "Popen", "call", "check_call", "check_output")
_SOCKET = ("socket", "create_connection", "create_server")


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


def _write_mode(text):
    """True when a string literal is a mode that writes, not a file name. The quotes stay."""
    if len(text) < 2 or text[0] not in "\"'" or text[-1] != text[0]:
        return False
    body = text[1:-1]
    if not body or any(c not in "rwaxbt+" for c in body):
        return False
    return any(c in "wax+" for c in body)


def _open_writes(call, first=False):
    """True when an open-like call's mode string writes. A method takes the mode first."""
    args = call.child_by_field_name("arguments")
    if args is None:
        return False
    positional = []
    for child in args.named_children:
        if child.type == "keyword_argument":
            key, val = child.child_by_field_name("name"), child.child_by_field_name("value")
            if key is not None and key.text.decode() == "mode" and val is not None and val.type == "string":
                return _write_mode(val.text.decode())
        else:
            positional.append(child)
    slot = 0 if first else 1
    return len(positional) > slot and positional[slot].type == "string" and _write_mode(positional[slot].text.decode())


def layer(fn, scope):
    """scope plus this function's parameters and the imports and defs in its body."""
    from asbuilt.built import _scope, one_name

    inner, params = dict(scope), set()
    head = fn.child_by_field_name("parameters")
    for child in head.named_children if head is not None else ():
        name = one_name(child)
        if name is not None:
            params.add(name)
            inner[name] = None
    body = fn.child_by_field_name("body")
    if body is not None:
        for name, spec in _scope(list(body.named_children), None, False).items():
            if name not in params:
                inner[name] = spec
    return inner


def _env(path):
    return path[:2] == ("os", "environ") or path[:2] == ("os", "getenv")


def _calls_out(path):
    return (
        path[:1] in _OUT
        or path[:2] in {("urllib", "request"), ("http", "client"), ("os", "system")}
        or path[:1] == ("subprocess",) and len(path) > 1 and path[1] in _RUN
        or path[:1] == ("socket",) and len(path) > 1 and path[1] in _SOCKET
    )


def _imported(node, scope):
    """True when this attribute chain starts at a name the file imported."""
    root, _attrs = _chain(node)
    if root is None or root.type != "identifier":
        return False
    return isinstance(_resolve(root.text.decode(), scope), tuple)


def _on_call(node, scope, add):
    fun = next(iter(node.named_children), None)
    _root, attrs = _chain(fun)
    if attrs and attrs[-1] in ("write_text", "write_bytes"):
        add("writes")
    # Path.open("w") takes the mode first. gzip.open(path, "wt") takes it second.
    if attrs and attrs[-1] == "open" and _open_writes(node, first=not _imported(fun, scope)):
        add("writes")
    path = resolve(fun, scope)
    if path is None:
        return
    if path[0] == "builtin":
        if path[1] == "input":
            add("input")
        elif path[1] == "open" and _open_writes(node):
            add("writes")
        return
    if _env(path):
        add("env")
    if path[:1] in _MODEL:
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
                from asbuilt.built import _scope

                inner.update(_scope(list(body.named_children), None, False))
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
