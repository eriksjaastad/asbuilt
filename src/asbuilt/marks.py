"""The marks a box can prove from its own code. Not from a box it calls.

A name is the module its import binds, here or at the top of the file, by the same
rule as an arrow (_scope). A parameter, a local, or a nested def hides that import.
A kind is one check below. Nested defs count, because they are not their own boxes.
Add a kind by appending a check. A later change in behavior cannot be proven, so it
is not a kind.
"""

import ast

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


def _path(node, scope):
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


def _layer(fn, scope):
    """scope plus this function's parameters and the imports and defs in its body."""
    from asbuilt.built import _one_name, _scope

    inner, params = dict(scope), set()
    head = fn.child_by_field_name("parameters")
    for child in head.named_children if head is not None else ():
        name = _one_name(child)
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
    path = _path(fun, scope)
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
    path = _path(node, scope)
    if path is not None and path[0] != "builtin" and _env(path):
        add("env")


def _literal(node):
    """A plain string literal's text, or None. An f-string or a non-string is not a name."""
    if node is None or node.type != "string":
        return None
    try:
        value = ast.literal_eval(node.text.decode())
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) and value else None


def _snippet(node):
    """The call or expression, one line, short enough to repeat inside a sentence."""
    text = " ".join(node.text.decode().split())
    if len(text) <= 100:
        return text
    fun = next(iter(node.named_children), None)
    head = " ".join(fun.text.decode().split()) if fun is not None else ""
    return head if head and head in text else text[:100]


def _args(call):
    args = call.child_by_field_name("arguments")
    positional, named = [], {}
    for child in args.named_children if args is not None else ():
        if child.type == "keyword_argument":
            key, val = child.child_by_field_name("name"), child.child_by_field_name("value")
            if key is not None and val is not None:
                named[key.text.decode()] = val
        else:
            positional.append(child)
    return positional, named


def _resolved(node, constants):
    """A string literal, or one assignment of one. A variable's spelling is not a name."""
    if node is None:
        return None
    if node.type == "string":
        return _literal(node)
    if node.type == "identifier":
        return constants.get(node.text.decode())
    return None


def _is_target(node):
    parent = node.parent
    if parent is None:
        return False
    kids = list(parent.named_children)
    if parent.type == "augmented_assignment":
        return bool(kids) and node == kids[0]
    return parent.type == "assignment" and len(kids) >= 2 and node in kids[:-1]


def _caught(node):
    """True when this call sits in a try body. An except handler is not that."""
    cur = node.parent
    while cur is not None:
        if cur.type == "try_statement":
            body = cur.child_by_field_name("body")
            if body is not None and body.start_byte <= node.start_byte and node.end_byte <= body.end_byte:
                return True
        cur = cur.parent
    return False


def _repeats(node):
    """True when this call sits in a for or while body in this box."""
    cur = node.parent
    while cur is not None:
        if cur.type in ("for_statement", "while_statement"):
            body = cur.child_by_field_name("body")
            if body is not None and body.start_byte <= node.start_byte and node.end_byte <= body.end_byte:
                return True
        cur = cur.parent
    return False


# Calls that really send, connect, or run something. A constructor such as requests.Session()
# or urllib.request.Request() is a calls-out badge and not one of these: it has not sent yet.
_HTTP = ("request", "get", "post", "put", "patch", "delete", "head", "options", "send")
_NAMED_ENV = ("get", "pop", "setdefault", "getenv")


def _call_path(fun, scope):
    """_path, and also obj().attr when obj() itself resolves. build_opener().open is that open."""
    path = _path(fun, scope)
    if path is not None or fun is None or fun.type != "attribute":
        return path
    obj, attr = fun.child_by_field_name("object"), fun.child_by_field_name("attribute")
    if obj is None or obj.type != "call" or attr is None:
        return None
    base = _path(next(iter(obj.named_children), None), scope)
    return None if base is None else base + (attr.text.decode(),)


def _way(path):
    last = path[-1]
    if path[:1] == ("subprocess",) or path[:2] == ("os", "system"):
        return "command"
    if path[:1] == ("socket",):
        return "connect"
    if path[:2] == ("http", "client") and last in ("HTTPConnection", "HTTPSConnection"):
        return "connect"
    if path[:2] == ("http", "client") and last == "request":
        return "send"
    if path[:1] in _OUT and last in _HTTP:
        return "send"
    if path[:2] == ("urllib", "request") and last in ("urlopen", "urlretrieve", "open"):
        return "send"
    return None


def _timeout_applies(path):
    """True when this call's own API takes a timeout keyword. os.system and socket() do not."""
    last = path[-1]
    if path[:1] in _OUT and last in _HTTP:
        return True
    if path[:2] == ("urllib", "request") and last in ("urlopen", "urlretrieve", "open"):
        return True
    if path[:1] == ("subprocess",) and last in _RUN:
        return True
    if last == "create_connection" or last in ("HTTPConnection", "HTTPSConnection"):
        return True
    return False


def _timeout_state(call):
    _positional, named = _args(call)
    node = named.get("timeout")
    if node is None:
        return "absent"
    if node.type == "none":
        return "none"
    return "passed"


def module_constants(nodes):
    """name -> string for one top-level assignment of a string. A second binding drops the name."""
    from asbuilt.built import _COND, _imported, _pattern

    found = {}

    def ban(names):
        for name in names:
            found[name] = None

    def walk(node, conditional):
        kind = node.type
        if kind in ("function_definition", "class_definition"):
            name = node.child_by_field_name("name")
            if name is not None:
                ban([name.text.decode()])
            return
        if kind == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is not None:
                walk(inner, conditional)
            return
        if kind in ("import_statement", "import_from_statement"):
            ban(_imported(node))
            return
        if kind == "assignment":
            names = list(_pattern(next(iter(node.named_children), None)))
            value = node.named_children[-1] if len(node.named_children) > 1 else None
            lit = _literal(value) if not conditional and len(names) == 1 else None
            if lit is None or names[0] in found:
                ban(names)
            else:
                found[names[0]] = lit
            return
        if kind in ("augmented_assignment", "named_expression", "for_statement"):
            ban(_pattern(next(iter(node.named_children), None)))
        nested = conditional or kind in _COND
        for child in node.named_children:
            walk(child, nested)

    for node in nodes:
        walk(node, False)
    return {name: text for name, text in found.items() if isinstance(text, str)}


def _function_constants(fn, outer):
    """outer, minus every name this function binds, plus one direct assignment of a string."""
    from asbuilt.built import _bound, _one_name, _pattern

    bound = _bound(fn)
    consts = {name: text for name, text in outer.items() if name not in bound}
    params, globals_ = set(), set()
    head = fn.child_by_field_name("parameters")
    for child in head.named_children if head is not None else ():
        name = _one_name(child)
        if name:
            params.add(name)
    body = fn.child_by_field_name("body")
    direct, multi = {}, set()
    for stmt in body.named_children if body is not None else ():
        if stmt.type == "global_statement":
            globals_.update(c.text.decode() for c in stmt.named_children if c.type == "identifier")
        if stmt.type == "expression_statement":
            stmt = next(iter(stmt.named_children), stmt)
        if stmt.type != "assignment":
            continue
        names = list(_pattern(next(iter(stmt.named_children), None)))
        value = stmt.named_children[-1] if len(stmt.named_children) > 1 else None
        lit = _literal(value) if len(names) == 1 else None
        name = names[0] if len(names) == 1 else None
        if name is None or lit is None or name in direct or name in multi:
            for item in names:
                multi.add(item)
            if name:
                direct.pop(name, None)
            continue
        direct[name] = lit
    for name, lit in direct.items():
        if name not in params and name not in multi and name not in globals_:
            consts[name] = lit
    for name in globals_:
        consts.pop(name, None)
    return consts


def facts(node, module, constants=None):
    """What this box itself proves about env, input, and calls out. [] when nothing here does.

    A name is a string literal or one assignment of a string. The spelling of a variable is not
    a name. A calls-out badge that only builds a client is not a fact: it has not sent yet.
    """
    outer = dict(constants or {})
    found, claimed = [], []

    def inside(cur):
        return any(start <= cur.start_byte and cur.end_byte <= end for start, end in claimed)

    def add(kind, cur, fields):
        if inside(cur):
            return
        claimed.append((cur.start_byte, cur.end_byte))
        fact = {"kind": kind, "at": _snippet(cur)}
        fact.update(fields)
        found.append(fact)

    def on_call(cur, scope, constants):
        fun = next(iter(cur.named_children), None)
        path = _call_path(fun, scope)
        if path is None:
            return False
        if path[0] == "builtin":
            if path[1] != "input":
                return False
            positional, _named = _args(cur)
            fields = {}
            prompt = _literal(positional[0]) if positional else None
            if prompt:
                fields["prompt"] = prompt
            add("input", cur, fields)
            return True
        if _env(path):
            last = path[-1]
            positional, named = _args(cur)
            how = "write" if last == "update" else "read"
            fields = {"how": how}
            if last in _NAMED_ENV:
                name = _resolved(positional[0] if positional else named.get("key"), constants)
                if name:
                    fields["name"] = name
            add("env", cur, fields)
            return True
        if not _calls_out(path):
            return False
        way = _way(path)
        if way is None:
            return False
        fields = {"way": way, "caught": _caught(cur)}
        if _repeats(cur):
            fields["repeats"] = True
        if _timeout_applies(path):
            fields["timeout"] = _timeout_state(cur)
        add("calls out", cur, fields)
        return True

    def visit(cur, scope, constants):
        if cur.type in ("import_statement", "import_from_statement"):
            return
        if cur.type == "function_definition":
            inner = _layer(cur, scope)
            local = _function_constants(cur, constants)
            body = cur.child_by_field_name("body")
            for child in body.children if body is not None else ():
                visit(child, inner, local)
            return
        if cur.type == "class_definition":
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
                visit(child, scope if method else inner, constants)
            return
        if cur.type == "call":
            on_call(cur, scope, constants)
        elif cur.type == "subscript" and not inside(cur):
            value = cur.child_by_field_name("value")
            path = _path(value, scope)
            if path is not None and _env(path):
                index = cur.child_by_field_name("subscript")
                fields = {"how": "set" if _is_target(cur) else "read"}
                name = _resolved(index, constants)
                if name:
                    fields["name"] = name
                add("env", cur, fields)
        elif cur.type == "comparison_operator" and not inside(cur):
            kids = list(cur.named_children)
            op = next((c.text.decode() for c in cur.children if c.text.decode() in ("in", "not in")), None)
            if op and len(kids) == 2:
                path = _path(kids[1], scope)
                if path is not None and _env(path) and path[-1] == "environ" and len(path) == 2:
                    fields = {"how": "check"}
                    name = _resolved(kids[0], constants)
                    if name:
                        fields["name"] = name
                    add("env", cur, fields)
        elif cur.type in ("identifier", "attribute") and not inside(cur):
            path = _path(cur, scope)
            if path is not None and path[0] != "builtin" and _env(path):
                how = "write" if path[-1] == "update" else "read"
                add("env", cur, {"how": how})
        for child in cur.children:
            visit(child, scope, constants)

    visit(node, dict(module or {}), outer)
    return found


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
            inner = _layer(cur, scope)
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
