"""Plain and Expert sentences for facts the parser proved on a box. Working, the middle
level, is not written here. The sentences are these ones: a model does not rephrase them.

A timeout is mentioned only when this call shows one, or when its absence is proved.
A network call is a request. It sends data only when a body, files, or data argument is there,
and it is outside only when a literal host is public. Argument values other than an
environment name, a prompt, or a URL host are not shown.
"""

import ast
import ipaddress
from urllib.parse import urlparse

from asbuilt.built import COND, imported, one_name, pattern
from asbuilt.marks import call_arguments, layer, reaches_out, reads_env, resolve

_HTTP = ("request", "get", "post", "put", "patch", "delete", "head", "options", "send")
_ENV_KEY_OP = ("get", "pop", "setdefault", "getenv")
_ENV_OP = (*_ENV_KEY_OP, "update", "items", "values", "keys", "copy", "environ")
_RUN_TIMEOUT = ("run", "call", "check_call", "check_output")  # Popen has no timeout argument
_DATA = ("post", "put", "patch")  # the second positional argument is the body


def string_literal(node):
    """The string this node writes, or None when it is not one plain string."""
    if node is None or node.type != "string":
        return None
    value = None
    try:
        value = ast.literal_eval(node.text.decode())
    except (SyntaxError, ValueError):
        value = None
    return value if isinstance(value, str) and value else None


def _assigned_value(node):
    """One string, one int, or a tuple of strings. None for anything mixed or repeated."""
    lit = string_literal(node)
    if lit:
        return lit
    if node is not None and node.type == "integer":
        num = None
        try:
            num = int(node.text.decode(), 0)
        except ValueError:  # governance: allow-silent SF002: text that is not an int literal is not a proven value
            num = None
        return num
    if node is None or node.type not in ("tuple", "list"):
        return None
    texts = []
    for child in node.named_children:
        text = string_literal(child)
        if not text:
            return None
        texts.append(text)
    return tuple(texts) if texts else None


def _bindings(nodes, read_value, initial=()):
    """Map every name bound in `nodes` to its single unconditional value, or None when banned."""
    found = {name: None for name in initial}

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
        if kind == "global_statement":
            ban(c.text.decode() for c in node.named_children if c.type == "identifier")
            return
        if kind in ("import_statement", "import_from_statement"):
            ban(imported(node))
            return
        if kind == "assignment":
            names = pattern(next(iter(node.named_children), None))
            value = node.named_children[-1] if len(node.named_children) > 1 else None
            lit = read_value(value) if not conditional and len(names) == 1 else None
            if lit is None or names[0] in found:
                ban(names)
            else:
                found[names[0]] = lit
            return
        if kind == "for_statement":
            ban(pattern(next(iter(node.named_children), None)))
            for child in node.named_children:
                walk(child, True)
            return
        if kind in ("augmented_assignment", "named_expression"):
            ban(pattern(next(iter(node.named_children), None)))
        nested = conditional or kind in COND
        for child in node.named_children:
            walk(child, nested)

    for node in nodes:
        walk(node, False)
    return found


def assigned(nodes):
    """name -> one top-level string, int, or tuple of strings. A second binding drops it."""
    return {name: value for name, value in _bindings(nodes, _assigned_value).items() if value is not None}


def constants(nodes):
    """name -> string for one top-level assignment of a string. A second binding drops it."""
    return {name: value for name, value in assigned(nodes).items() if isinstance(value, str)}


def _fn_constants(fn, outer):
    """outer, minus every name this function binds, plus one direct assignment of a string."""
    head = fn.child_by_field_name("parameters")
    params = [name for child in (head.named_children if head is not None else ()) if (name := one_name(child))]
    body = fn.child_by_field_name("body")
    local = _bindings(body.named_children if body is not None else (), string_literal, initial=params)
    kept = {name: text for name, text in outer.items() if name not in local}
    kept.update({name: text for name, text in local.items() if text is not None})
    return kept


def _extended(fun, scope):
    """resolve, and also obj().attr when obj() itself resolves. build_opener().open is that open."""
    path = resolve(fun, scope)
    if path is not None or fun is None or fun.type != "attribute":
        return path
    obj, attr = fun.child_by_field_name("object"), fun.child_by_field_name("attribute")
    if obj is None or obj.type != "call" or attr is None:
        return None
    base = _extended(next(iter(obj.named_children), None), scope)
    return None if base is None else base + (attr.text.decode(),)


def _chain(call, scope):
    """This call, then the call it is a method of. os.getenv("X").strip() yields strip, then getenv."""
    current = call
    while current is not None:
        fun = next(iter(current.named_children), None)
        yield current, fun, _extended(fun, scope)
        if fun is None or fun.type != "attribute":
            return
        obj = fun.child_by_field_name("object")
        current = obj if obj is not None and obj.type == "call" else None


def _label(fun):
    """The callee's names, without its argument values. urllib.request.urlopen, not the URL."""
    if fun is None:
        return ""
    if fun.type == "identifier":
        return fun.text.decode()
    if fun.type == "attribute":
        obj, attr = fun.child_by_field_name("object"), fun.child_by_field_name("attribute")
        attr_name = attr.text.decode() if attr is not None else ""
        if obj is not None and obj.type == "call":
            return f"{_label(next(iter(obj.named_children), None))}.{attr_name}"
        if obj is not None and obj.type in ("attribute", "identifier"):
            return f"{_label(obj)}.{attr_name}"
    return ""


def _shown(fun, bits):
    name = _label(fun) or "call"
    return f"{name}({', '.join(bits)})" if bits else f"{name}(...)"


def _bound_name(node, local):
    """A string literal, or one assignment of one. None when the node is not a name."""
    if node is None:
        return None
    if node.type == "string":
        return string_literal(node)
    return local.get(one_name(node) or "")


def _name_bit(node, found):
    """An environment name or the identifier that holds it. Not a later argument's value."""
    if node is None:
        return None
    if node.type == "string":
        return repr(found) if found else None
    if node.type == "identifier":
        return node.text.decode()
    return None


def _url_text(node, local):
    """A URL string, and whether the call writes it in place. A constant name is not in place."""
    if node is None:
        return None, False
    if node.type == "string":
        return string_literal(node), True
    if node.type == "identifier":
        value = local.get(node.text.decode())
        return (value, False) if isinstance(value, str) else (None, False)
    return None, False


def host_name(text):
    """The host of an http(s) URL string, port removed. None when it is not one."""
    if not isinstance(text, str) or not text:
        return None
    host = None
    try:
        parsed = urlparse(text)
        if parsed.scheme in ("http", "https"):
            host = parsed.hostname
    except ValueError:  # governance: allow-silent SF002: a malformed URL has no host to name, so no fact
        host = None
    return host or None


def local_host(host):
    """True for localhost, a loopback address, 0.0.0.0, or a private address."""
    if not host:
        return False
    if host.lower().rstrip(".") == "localhost":
        return True
    addr = None
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is None:
        return False
    return addr.is_loopback or addr.is_unspecified or addr.is_private


def _module_call(fun, scope):
    """True when the callee is the module or a function imported from it, not a method on a client."""
    if fun is None:
        return False
    if fun.type == "identifier":
        return True
    obj = fun
    while obj is not None and obj.type == "attribute":
        obj = obj.child_by_field_name("object")
    if obj is None or obj.type != "identifier":
        return False
    spec = scope.get(obj.text.decode(), False)
    return isinstance(spec, tuple) and spec[0] in ("mod", "from")


def _timeout_index(path):
    last = path[-1]
    if path[:1] == ("socket",) and last == "create_connection":
        return 1
    if path[:2] == ("urllib", "request") and last in ("urlopen", "open"):
        return 2
    if path[:2] == ("http", "client") and last in ("HTTPConnection", "HTTPSConnection"):
        return 2
    return None


def _has_timeout(path):
    """True when this function takes a timeout. Popen and urlretrieve do not."""
    last = path[-1]
    if path[:1] == ("subprocess",) and last in _RUN_TIMEOUT:
        return True
    if path[:1] in {("requests",), ("httpx",)} and last in _HTTP:
        return True
    if path[:2] == ("urllib", "request") and last in ("urlopen", "open"):
        return True
    if path[:1] == ("socket",) and last == "create_connection":
        return True
    if path[:2] == ("http", "client") and last in ("HTTPConnection", "HTTPSConnection"):
        return True
    return False


def _absence_proves_none(path):
    """True when omitting timeout means there is none. urlopen's default is a timeout, so it is not."""
    last = path[-1]
    if path[:1] == ("requests",) and last in _HTTP:
        return True
    return path[:1] == ("subprocess",) and last in _RUN_TIMEOUT


def _timeout(call, fun, path, scope):
    """passed, none, absent, or None when this call does not prove it."""
    if not _has_timeout(path):
        return None
    positional, named, splat = call_arguments(call)
    if "timeout" in named:
        return "none" if named["timeout"].type == "none" else "passed"
    index = _timeout_index(path)
    if index is not None and not splat and len(positional) > index:
        return "none" if positional[index].type == "none" else "passed"
    if splat or not _module_call(fun, scope) or not _absence_proves_none(path):
        return None
    return "absent"


def sent_body(call, path):
    """The node passed as this call's body, or None when the call does not send one."""
    if not path:
        return None
    positional, named, _splat = call_arguments(call)
    for key in ("data", "json", "body", "content", "files", "payload"):
        node = named.get(key)
        if node is not None and node.type != "none":
            return node
    last = path[-1]
    if last in _DATA or last in ("urlopen", "Request"):
        if len(positional) > 1 and positional[1].type != "none":
            return positional[1]
    return None


def call_way(path):
    """send, connect, command, or None. The same answer a calls-out fact stores."""
    if not path:
        return None
    last = path[-1]
    if path[:1] == ("subprocess",) or path[:2] == ("os", "system"):
        return "command"
    if path[:1] == ("socket",) and last != "socket":
        return "connect"
    if path[:2] == ("http", "client") and last in ("HTTPConnection", "HTTPSConnection"):
        return "connect"
    if path[:2] == ("http", "client") and last == "request":
        return "send"
    if path[:1] in {("requests",), ("httpx",)} and last in _HTTP:
        return "send"
    if path[:2] == ("urllib", "request") and last in ("urlopen", "urlretrieve", "open", "Request"):
        return "send"
    return None


def _env_how(path):
    last = path[-1]
    if last == "pop":
        return "remove"
    if last == "setdefault":
        return "default"
    if last == "update":
        return "write"
    return "read"


def _within(node, *kinds):
    """True when `node` sits in the body of one of `kinds`, not in its header or handler."""
    cur = node.parent
    while cur is not None:
        if cur.type in kinds:
            body = cur.child_by_field_name("body")
            if body is not None and body.start_byte <= node.start_byte and node.end_byte <= body.end_byte:
                return True
        cur = cur.parent
    return False


def _is_target(node):
    parent = node.parent
    if parent is None:
        return False
    kids = list(parent.named_children)
    if parent.type == "augmented_assignment":
        return bool(kids) and node == kids[0]
    return parent.type == "assignment" and len(kids) >= 2 and node in kids[:-1]


def facts(node, module, consts=None):
    """What this box itself proves about env, input, and calls out. [] when nothing here does."""
    outer = dict(consts or {})
    found, claimed = [], []

    def inside(cur):
        return any(start <= cur.start_byte and cur.end_byte <= end for start, end in claimed)

    def add(kind, span, shown, fields):
        if inside(span):
            return
        claimed.append((span.start_byte, span.end_byte))
        fact = {"kind": kind, "at": shown, "start": span.start_byte}
        fact.update(fields)
        found.append(fact)

    def on_call(cur, scope, local):
        if inside(cur):
            return
        chosen = None
        for current, fun, path in reversed(list(_chain(cur, scope))):
            if path is None:
                continue
            if path[0] == "builtin" and path[-1] == "input":
                chosen = (current, fun, path, "input")
                break
            if reads_env(path) and path[-1] in _ENV_OP:
                chosen = (current, fun, path, "env")
                break
            way = call_way(path)
            if reaches_out(path) and way and not (path[-1] == "Request" and sent_body(current, path) is None):
                chosen = (current, fun, path, "calls out")
                break
        if chosen is None:
            return
        current, fun, path, kind = chosen
        # Claim the whole chain so the inner call is not recorded again. Arguments stay outside
        # the callee, so os.environ["K"] passed into another call is still its own fact.
        callee = next(iter(cur.named_children), cur)
        if kind == "input":
            positional, _named, _splat = call_arguments(current)
            prompt = string_literal(positional[0]) if positional else None
            fields = {}
            if prompt:
                fields["prompt"] = prompt
            bit = repr(prompt) if prompt else None
            add("input", callee, _shown(fun, [bit] if bit else []), fields)
            return
        if kind == "env":
            fields = {"how": _env_how(path)}
            bit = None
            if path[-1] in _ENV_KEY_OP:
                positional, named, _splat = call_arguments(current)
                arg = positional[0] if positional else named.get("key")
                found_name = _bound_name(arg, local)
                if found_name:
                    fields["name"] = found_name
                bit = _name_bit(arg, found_name)
            add("env", callee, _shown(fun, [bit] if bit else []), fields)
            return
        fields = {"way": call_way(path), "caught": _within(cur, "try_statement")}
        if _within(cur, "for_statement", "while_statement"):
            fields["repeats"] = True
        timeout = _timeout(current, fun, path, scope)
        if timeout:
            fields["timeout"] = timeout
        if sent_body(current, path) is not None:
            fields["sends"] = True
        host, shown = None, False
        positional, named, _splat = call_arguments(current)
        for candidate in (named.get("url"), positional[0] if positional else None):
            text, literal = _url_text(candidate, local)
            found = host_name(text) if text else None
            if found:
                host, shown = found, literal
                break
        if host:
            fields["host"] = host
            if local_host(host):
                fields["local"] = True
        # A constant URL is a host for the plain note. The security line keeps the call as written.
        add("calls out", callee, _shown(fun, [host] if shown else []), fields)

    def on_subscript(cur, scope, local, how):
        if inside(cur):
            return
        value = cur.child_by_field_name("value")
        path = resolve(value, scope)
        if not reads_env(path):
            return
        index = cur.child_by_field_name("subscript")
        found_name = _bound_name(index, local)
        fields = {"how": how}
        if found_name:
            fields["name"] = found_name
        base = " ".join(value.text.decode().split()) if value is not None else "os.environ"
        if index is not None and index.type == "identifier":
            shown = f"{base}[{index.text.decode()}]"
        elif found_name:
            shown = f"{base}[{found_name!r}]"
        else:
            shown = base
        add("env", cur, shown, fields)

    def visit(cur, scope, local):
        if cur.type in ("import_statement", "import_from_statement"):
            return
        if cur.type == "function_definition":
            inner = layer(cur, scope)
            body = cur.child_by_field_name("body")
            for child in body.children if body is not None else ():
                visit(child, inner, _fn_constants(cur, local))
            return
        if cur.type == "class_definition":
            body = cur.child_by_field_name("body")
            for child in body.children if body is not None else ():
                visit(child, scope, local)
            return
        if cur.type == "delete_statement":
            for target in cur.named_children:
                if target.type == "subscript":
                    on_subscript(target, scope, local, "remove")
            return
        if cur.type == "call":
            on_call(cur, scope, local)
        elif cur.type == "subscript" and not inside(cur):
            on_subscript(cur, scope, local, "set" if _is_target(cur) else "read")
        elif cur.type == "comparison_operator" and not inside(cur):
            kids = list(cur.named_children)
            op = next((c.text.decode() for c in cur.children if c.text.decode() in ("in", "not in")), None)
            if op and len(kids) == 2 and reads_env(resolve(kids[1], scope)):
                path = resolve(kids[1], scope)
                if path is not None and path[-1] == "environ" and len(path) == 2:
                    found_name = _bound_name(kids[0], local)
                    fields = {"how": "check"}
                    if found_name:
                        fields["name"] = found_name
                    add("env", cur, " ".join(cur.text.decode().split()), fields)
        elif cur.type in ("identifier", "attribute") and not inside(cur):
            path = resolve(cur, scope)
            if reads_env(path) and path[-1] in _ENV_OP:
                add("env", cur, _label(cur) or cur.text.decode(), {"how": _env_how(path)})
        for child in cur.children:
            visit(child, scope, local)

    visit(node, dict(module or {}), outer)
    return found


def _plain(fact):
    at, kind = fact["at"], fact["kind"]
    if kind == "input":
        return f"This reads what a person types: {at}."
    if kind == "env":
        how, name = fact.get("how"), fact.get("name")
        if how == "check":
            which = f"whether {name} is set" if name else "the environment"
            return f"This checks {which}, and does not read the value: {at}."
        if how == "remove":
            return f"This removes {name} from the environment: {at}." if name else f"This removes a value from the environment: {at}."
        if how == "default":
            return f"This sets a default for {name} in the environment: {at}." if name else f"This sets a default in the environment: {at}."
        if how == "set":
            return f"This sets {name} in the environment: {at}." if name else f"This sets an environment value: {at}."
        if how == "write":
            return f"This changes the environment: {at}."
        if name:
            return f"This reads {name} from the environment: {at}."
        return f"This reads the environment: {at}."
    if fact.get("way") == "command":
        return f"This runs a command outside this program: {at}."
    if fact.get("way") == "connect":
        return f"This opens a network connection: {at}."
    if fact.get("sends") and fact.get("host") and not fact.get("local") and fact["host"] in at:
        return f"This sends your data to an outside service: {at}."
    if fact.get("sends"):
        return f"This sends data in a network request: {at}."
    return f"This makes a network request: {at}."


def _timeout_line(fact):
    state = fact.get("timeout")
    if state == "passed":
        return "A timeout is passed."
    if state == "none":
        return "The timeout is None."
    if state == "absent":
        return "No timeout is passed."
    return ""


def _expert(fact):
    at = fact["at"]
    if fact["kind"] == "input":
        return f"{at} reads what a person types."
    if fact["kind"] == "env":
        how, name = fact.get("how"), fact.get("name")
        if how == "check":
            which = f"{name} is set" if name else "a name is set"
            return f"{at} checks whether {which}. It does not read the value."
        if how == "remove":
            return f"{at} removes {name} from the environment." if name else f"{at} removes a value from the environment."
        if how == "default":
            return f"{at} sets a default for {name} in the environment." if name else f"{at} sets a default in the environment."
        if how == "set":
            return f"{at} sets {name} in the environment." if name else f"{at} sets an environment value."
        if how == "write":
            return f"{at} changes the environment."
        if name:
            return f"{at} reads {name} from the environment."
        return f"{at} reads the environment. The name is not a literal in this box."
    parts = [f"{at}."]
    timeout = _timeout_line(fact)
    if timeout:
        parts.append(timeout)
    parts.append("This call is inside try." if fact.get("caught") else "This call is not inside try.")
    if fact.get("repeats"):
        parts.append("This call is inside a loop in this box.")
    return " ".join(parts)


def for_box(node, scope, consts):
    """The plain and expert sentences for this box, one pair per fact. [] when there is no fact."""
    return [{"fact": fact, "plain": _plain(fact), "expert": _expert(fact)} for fact in facts(node, scope, consts)]
