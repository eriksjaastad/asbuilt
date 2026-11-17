"""Up to three everyday sentences for one box, from facts the parser proved.

A docstring is not copied. These sentences do not replace a security sentence:
the page shows the security lines after them, expert included. No model is called.
"""

import builtins
import json

from asbuilt.built import one_name
from asbuilt.marks import layer, reaches_out, reads_env, resolve
from asbuilt.security import (
    assigned,
    call_arguments,
    call_way,
    facts as security_facts,
    string_literal,
)

_NESTED = ("function_definition", "class_definition", "lambda")
_LISTS = ("list", "tuple", "list_pattern", "pattern_list", "expression_list")


def _module(node):
    root = node
    while root.parent is not None:
        root = root.parent
    return root


def _methods(node):
    if node.type == "function_definition":
        yield node
        return
    body = node.child_by_field_name("body")
    for child in body.named_children if body is not None else ():
        inner = (
            child.child_by_field_name("definition")
            if child.type == "decorated_definition"
            else child
        )
        if inner is not None and inner.type == "function_definition":
            yield inner


def _doc(stmt):
    child = (
        next(iter(stmt.named_children), None)
        if stmt.type == "expression_statement"
        else None
    )
    return child is not None and child.type == "string"


def _block(node):
    for field in ("consequence", "body"):
        found = node.child_by_field_name(field)
        if found is not None:
            return found
    return None


def _callee(node):
    if node is None:
        return None
    if node.type == "identifier":
        return node.text.decode()
    if node.type == "attribute":
        attr = node.child_by_field_name("attribute")
        return attr.text.decode() if attr is not None else None
    if node.type == "call":
        return _callee(next(iter(node.named_children), None))
    return None


def _self_attr(node):
    """The attribute on self, including a starred `*self.name` in a list."""
    if node is None:
        return None
    if node.type == "list_splat":
        return _self_attr(next(iter(node.named_children), None))
    if node.type != "attribute":
        return None
    obj, attr = (
        node.child_by_field_name("object"),
        node.child_by_field_name("attribute"),
    )
    if attr is None:
        return None
    if obj is not None and obj.type == "list_splat":
        obj = next(iter(obj.named_children), None)
    if obj is not None and obj.type == "identifier" and obj.text.decode() == "self":
        return attr.text.decode()
    return None


def _items(node):
    if node is not None and node.type in _LISTS:
        return list(node.named_children)
    return [node]


def _jsonish(text):
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(value, (dict, list))


def _exc(name):
    return bool(name) and (
        name == "Exception" or name.endswith(("Error", "Exception", "Failed"))
    )


def _join(items, word):
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {word} {items[1]}"
    return ", ".join(items[:-1]) + f", {word} " + items[-1]


def _or(items):
    return _join(items, "or")


def _and(items):
    return _join(items, "and")


def _walk(node, visit):
    """Visit this statement and what it contains, not a nested function or class."""
    if node.type in _NESTED:
        return
    visit(node)
    for child in node.named_children:
        _walk(child, visit)


def _fact_name(node, facts):
    """The one environment name security.facts recorded inside this expression."""
    if node is None:
        return None
    names = [
        fact["name"]
        for fact in facts
        if fact.get("kind") == "env"
        and fact.get("name")
        and node.start_byte <= fact.get("start", -1) < node.end_byte
    ]
    return names[0] if len(set(names)) == 1 else None


def _contains(outer, inner):
    return (
        outer is not None
        and inner is not None
        and outer.start_byte <= inner.start_byte
        and inner.end_byte <= outer.end_byte
    )


def _escapes(node, fn):
    """True when this raise leaves the function: not swallowed by a try here."""
    cur = node.parent
    while (
        cur is not None
        and cur != fn
        and cur.type not in ("function_definition", "class_definition")
    ):
        if cur.type == "try_statement":
            body = cur.child_by_field_name("body")
            caught = _contains(body, node) and any(
                child.type == "except_clause" for child in cur.children
            )
            if caught:
                return False
        cur = cur.parent
    return True


def _in_retry(node, fn):
    """A loop that catches and tries again. A plain loop over items is not one."""
    cur = node.parent
    while cur is not None and cur != fn and cur.type not in _NESTED:
        if cur.type in ("for_statement", "while_statement"):
            body = cur.child_by_field_name("body")
            if body is not None and any(
                child.type == "try_statement" for child in body.children
            ):
                return True
            return False
        cur = cur.parent
    return False


# These calls wait. A client constructor does not, unless it is given a timeout.
_WAITS = {
    "run",
    "call",
    "check_call",
    "check_output",
    "Popen",
    "urlopen",
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "request",
    "send",
    "create_connection",
    "create_server",
    "SMTP",
    "SMTP_SSL",
    "IMAP4",
    "IMAP4_SSL",
}


def _takes_seconds(path, call, facts):
    """True for a call whose timeout is in seconds, not milliseconds."""
    roots = ("requests", "httpx", "socket", "subprocess", "smtplib", "imaplib")
    if path and (path[0] in roots or path[:2] == ("urllib", "request")):
        return True
    for fact in facts:
        if fact.get("timeout") != "passed":
            continue
        if not (call.start_byte <= fact.get("start", -1) < call.end_byte):
            continue
        at = fact.get("at") or ""
        if at.startswith(
            (
                "urllib.",
                "requests.",
                "httpx.",
                "socket.",
                "subprocess.",
                "smtplib.",
                "imaplib.",
            )
        ):
            return True
    return False


def _param_default(node, bound):
    if node.type not in ("default_parameter", "typed_default_parameter"):
        return None
    value = node.named_children[-1]
    if value.type == "integer":
        return int(value.text.decode())
    if value.type == "identifier":
        return bound.get(value.text.decode())
    if value.type == "string":
        return string_literal(value)
    if value.type in _LISTS and value.named_children:
        return string_literal(value.named_children[0])
    return None


def _from_param(value, defaults):
    if value is None:
        return None
    if value.type == "identifier" and value.text.decode() in defaults:
        return defaults[value.text.decode()]
    if value.type == "call" and _callee(value) in ("list", "tuple"):
        positional, _named, _splat = call_arguments(value)
        if len(positional) == 1 and positional[0].type == "identifier":
            return defaults.get(positional[0].text.decode())
    return None


def _keeps(node, scope, groups):
    """True when this dict reads the environment and keeps only a fixed set of names."""
    if node is None or node.type != "dictionary_comprehension":
        return False
    reads = fixed = False

    def walk(cur):
        nonlocal reads, fixed
        if cur.type == "call" and reads_env(
            resolve(next(iter(cur.named_children), None), scope)
        ):
            reads = True
        if cur.type == "comparison_operator":
            keeps = any(child.text.decode() == "in" for child in cur.children)
            if keeps:
                for child in cur.named_children:
                    if child.type == "identifier" and isinstance(
                        groups.get(child.text.decode()), tuple
                    ):
                        fixed = True
                    if (
                        child.type in ("tuple", "list")
                        and child.named_children
                        and all(string_literal(c) for c in child.named_children)
                    ):
                        fixed = True
        for child in cur.named_children:
            walk(child)

    walk(node)
    return reads and fixed


def _exec(node, assigns, defaults, attrs, depth=0):
    """A literal command name this argument holds, or None."""
    if node is None or depth > 4:
        return None
    if node.type == "string":
        return string_literal(node)
    if node.type == "list_splat":
        return _exec(
            next(iter(node.named_children), None), assigns, defaults, attrs, depth + 1
        )
    if node.type in _LISTS and node.named_children:
        return _exec(node.named_children[0], assigns, defaults, attrs, depth + 1)
    if node.type == "identifier":
        name = node.text.decode()
        if name in assigns:
            return _exec(assigns[name], assigns, defaults, attrs, depth + 1)
        value = defaults.get(name)
        return value if isinstance(value, str) else None
    attr = _self_attr(node)
    if attr and isinstance(attrs.get(attr), str):
        return attrs[attr]
    if node.type == "call" and _callee(node) in ("list", "tuple"):
        positional, _named, _splat = call_arguments(node)
        if positional:
            return _exec(positional[0], assigns, defaults, attrs, depth + 1)
    if node.type == "parenthesized_expression" and node.named_children:
        return _exec(node.named_children[0], assigns, defaults, attrs, depth + 1)
    return None


def _command_stdout(expr, assigns, scope, fn):
    """True when this return is the stdout of a command this box runs."""
    if expr is None or expr.type != "attribute":
        return False
    attr = expr.child_by_field_name("attribute")
    obj = expr.child_by_field_name("object")
    if (
        attr is None
        or attr.text.decode() != "stdout"
        or obj is None
        or obj.type != "identifier"
    ):
        return False
    source = assigns.get(obj.text.decode())
    if source is None or source.type != "call":
        return False
    fun = next(iter(source.named_children), None)
    path = resolve(fun, scope)
    if (
        path
        and path[0] == "subprocess"
        and path[-1] in ("run", "check_output", "call", "check_call")
    ):
        return True
    if fun is None or fun.type != "attribute" or _callee(fun) != "run":
        return False
    owner = fun.child_by_field_name("object")
    if owner is None or owner.type != "identifier" or owner.text.decode() != "self":
        return False
    cls = fn.parent
    while cls is not None and cls.type != "class_definition":
        cls = cls.parent
    if cls is None:
        return False
    for method in _methods(cls):
        label = method.child_by_field_name("name")
        if label is None or label.text.decode() != "run":
            continue
        return _returns_subprocess(method, scope)
    return False


def _returns_subprocess(method, scope):
    inner = layer(method, scope)
    found = False

    def walk(node):
        nonlocal found
        if node.type in _NESTED:
            return
        if node.type == "return_statement":
            expr = next(iter(node.named_children), None)
            if expr is not None and expr.type == "call":
                path = resolve(next(iter(expr.named_children), None), inner)
                if (
                    path
                    and path[0] == "subprocess"
                    and path[-1] in ("run", "check_output", "call", "check_call")
                ):
                    found = True
        for child in node.named_children:
            walk(child)

    body = _block(method)
    if body is not None:
        walk(body)
    return found


def _network(expr, scope):
    found = False

    def walk(cur):
        nonlocal found
        if cur.type == "call":
            path = resolve(next(iter(cur.named_children), None), scope)
            if reaches_out(path) and call_way(path) in ("send", "connect"):
                found = True
        for child in cur.named_children:
            walk(child)

    walk(expr)
    return found


def _kind(expr, scope):
    """What one return expression is, or other when this note should not name it."""
    if expr is None or expr.type == "none":
        return ("none", None)
    if expr.type == "string":
        text = string_literal(expr)
        if not text:
            return ("other", None)
        return ("json", None) if _jsonish(text) else ("text", text)
    if expr.type == "call" and _exc(_callee(expr)):
        return ("error", None)
    if expr.type == "call" and _network(expr, scope):
        return ("network", None)
    return ("other", None)


def _inside(expr, facts):
    """True when a send or connect fact sits inside this return expression."""
    if expr is None:
        return False
    for fact in facts:
        if fact.get("kind") == "calls out" and fact.get("way") in ("send", "connect"):
            if expr.start_byte <= fact.get("start", -1) < expr.end_byte:
                return True
    return False


def _return_claim(fn, scope, facts):
    """The return every path in this function shares. None when they disagree or it yields."""
    body = _block(fn)
    exprs, yielded = [], False

    def visit(cur):
        nonlocal yielded
        if cur.type == "yield":
            yielded = True
        if cur.type == "return_statement":
            exprs.append(next(iter(cur.named_children), None))

    for stmt in body.named_children if body is not None else ():
        if _doc(stmt):
            continue
        _walk(stmt, visit)
    if yielded:
        return ("yield", None)
    if not exprs:
        return None
    kinds = []
    for expr in exprs:
        kind = _kind(expr, scope)
        if (
            kind[0] == "other"
            and expr is not None
            and expr.type == "call"
            and _inside(expr, facts)
        ):
            kind = ("network", None)
        kinds.append(kind)
    if len(set(kinds)) != 1 or kinds[0][0] == "other":
        return ("other", None)
    return kinds[0]


def _bearer(node, assigns, facts):
    found = []
    if node.type == "string" and "Bearer" in node.text.decode():
        for child in node.named_children:
            if child.type != "interpolation":
                continue
            ident = next(
                (c for c in child.named_children if c.type == "identifier"), None
            )
            source = assigns.get(ident.text.decode()) if ident is not None else child
            name = _fact_name(source if source is not None else child, facts)
            if name:
                found.append(name)
    for child in node.named_children:
        found.extend(_bearer(child, assigns, facts))
    return found


class _Story:
    def __init__(self, name):
        self.name = name
        self.exception = False
        self.sends = False
        self.send_host = None
        self.send_local = False
        self.command = None
        self.command_output = None
        self.keeps_env = False
        self.is_class = False
        self.timeout = None
        self.timeout_default = False
        self.timeout_loop = False
        self.time_bad = False
        self.given = False
        self.given_at = None
        self.skipped_env = None
        self.choices = []
        self.otherwise = None
        self.always = True
        self.raises = False
        self.events = []
        self.keys = set()
        self.bearer = None
        self.timeout_attrs = set()
        self.returns = None


def _return_line(story):
    claim = story.returns
    if not claim:
        return None
    kind, detail = claim
    name = story.name
    if kind == "none":
        return f"{name} returns nothing."
    if kind == "text":
        return f"{name} returns the text {detail}."
    if kind == "json":
        return f"{name} returns a fixed text."
    if kind == "error":
        return f"{name} returns an error."
    if kind == "network":
        return f"{name} returns whatever the network request returns."
    return None


def _choice_line(story):
    if not story.choices and not story.otherwise:
        return None
    same = len({item[2] for item in story.choices}) == 1 if story.choices else False
    bits = []
    for index, (lit, callee, env) in enumerate(story.choices):
        bits.append(
            f"{callee} when {env} is {lit}"
            if index == 0 or not same
            else f"{callee} when it is {lit}"
        )
    head = "Otherwise it calls" if story.given else f"{story.name} calls"
    if bits and story.otherwise:
        return f"{head} {', '.join(bits)}, and {story.otherwise} otherwise."
    if bits:
        return f"{head} {', '.join(bits)}."
    if story.otherwise:
        return f"{head} {story.otherwise}."
    return None


def _phrase(cond, keys):
    """A missing environment value. A parameter is not a plain condition."""
    if not cond or cond[0] != "env":
        return None
    name = cond[1]
    return (
        "the key is missing"
        if name in keys
        else f"the environment value {name} is missing"
    )


def _finish(text):
    if text.endswith(('."', '!"', '?"', ".", "!", "?")):
        return text
    return text + "."


def _verb(story, branch):
    """stops when this branch, or every path, raises. Otherwise the call can succeed."""
    return "stops" if story.always or branch else "can stop"


def _failure(story):
    """One sentence. A quote stays with its condition. "among others" only beside a quote."""
    if not story.raises:
        return []
    name = story.name
    tied = None
    bare = None
    loose = []
    for cond, msg, branch in story.events:
        phrase = _phrase(cond, story.keys)
        if phrase and msg and tied is None:
            tied = (phrase, msg, branch)
        elif phrase and bare is None:
            bare = (phrase, branch)
        elif msg and msg not in loose and len(loose) < 2:
            loose.append(msg)
    shown = []
    if tied:
        phrase, msg, branch = tied
        line = f'If {phrase}, {name} {_verb(story, branch)} with an error that says "{msg}"'
        shown = [msg]
    elif bare:
        phrase, branch = bare
        line = f"If {phrase}, {name} {_verb(story, branch)} with an error"
    elif loose:
        shown = loose
        line = f"{name} {_verb(story, False)} with an error that says {_or([f'"{text}"' for text in loose])}"
    else:
        line = f"{name} {_verb(story, False)} with an error"
    if shown and any(msg not in shown for _cond, msg, _branch in story.events):
        line += ", among others"
    return [_finish(line)]


def _sentences(story):
    pending = []
    if story.given:
        skip = f" and does not read {story.skipped_env}" if story.skipped_env else ""
        pending.append(
            f"If {story.name} is given something to call, it returns whatever that returns{skip}."
        )
    choice = _choice_line(story)
    if choice:
        pending.append(choice)
    if not story.given and not story.choices and not story.otherwise:
        if story.send_host:
            net = "" if story.send_local else " over the internet"
            key = f", using a key read from {story.bearer}" if story.bearer else ""
            pending.append(f"{story.name} sends data to {story.send_host}{net}{key}.")
        elif story.sends and story.bearer:
            pending.append(
                f"{story.name} sends data, using a key read from {story.bearer}."
            )
        elif isinstance(story.command, str):
            bits = [f"runs the command {story.command}"]
            if story.keeps_env:
                bits.append("keeps only a fixed set of environment names")
            if story.command_output:
                bits.append("returns the command's output")
            pending.append(f"{story.name} " + _and(bits) + ".")
        elif story.exception and not story.returns:
            pending.append(f"{story.name} is an exception.")
    if isinstance(story.timeout, int):
        if story.timeout_loop and story.timeout_default:
            pending.append(
                f"By default each try, {story.name} waits up to {story.timeout} seconds."
            )
        elif story.timeout_loop:
            pending.append(
                f"Each try, {story.name} waits up to {story.timeout} seconds."
            )
        elif story.timeout_default:
            pending.append(
                f"By default {story.name} waits up to {story.timeout} seconds."
            )
        else:
            pending.append(f"{story.name} waits up to {story.timeout} seconds.")
    pending.extend(_failure(story))
    if story.returns and not story.given and not story.choices and not story.otherwise:
        pending.append(_return_line(story))
    return [line for line in pending if line][:3]


def _apply_facts(story, node, scope, words):
    """Sends and host come only from security facts, and only from the same call."""
    for fact in security_facts(node, scope, words):
        if fact["kind"] != "calls out" or not fact.get("sends"):
            continue
        story.sends = True
        if fact.get("host") and story.send_host is None:
            story.send_host = fact["host"]
            story.send_local = bool(fact.get("local"))


def _condition(cond, assigns, facts, params):
    """('env', name) when a `not` check reads one environment value. A parameter is not named."""
    if cond is None or cond.type != "not_operator":
        return None
    ident = next(
        (c.text.decode() for c in cond.named_children if c.type == "identifier"), None
    )
    if ident in params:
        return None
    source = assigns.get(ident) if ident else next(iter(cond.named_children), None)
    name = _fact_name(source, facts)
    return ("env", name) if name else None


def _guard(node, fn, assigns, facts, params):
    cur = node.parent
    while (
        cur is not None
        and cur != fn
        and cur.type not in ("function_definition", "class_definition")
    ):
        if cur.type == "if_statement":
            body = _block(cur)
            if _contains(body, node):
                return _condition(
                    cur.child_by_field_name("condition"), assigns, facts, params
                )
        cur = cur.parent
    return None


def _if_stops(node, fn):
    """True when the if that holds this raise always raises in that branch."""
    cur = node.parent
    while (
        cur is not None
        and cur != fn
        and cur.type not in ("function_definition", "class_definition")
    ):
        if cur.type == "if_statement":
            body = _block(cur)
            if body is not None and _contains(body, node):
                return _seq(body.named_children, fn) == "raise"
        cur = cur.parent
    return False


def _builtin_error(name):
    """True for a real builtin exception, including ones a short name list would miss."""
    obj = getattr(builtins, name or "", None)
    return isinstance(obj, type) and issubclass(obj, BaseException)


def _class_node(module, name):
    for child in module.named_children if module is not None else ():
        node = (
            child.child_by_field_name("definition")
            if child.type == "decorated_definition"
            else child
        )
        if node is None or node.type != "class_definition":
            continue
        label = node.child_by_field_name("name")
        if label is not None and label.text.decode() == name:
            return node
    return None


def _init_method(cls):
    body = cls.child_by_field_name("body")
    for child in body.named_children if body is not None else ():
        node = (
            child.child_by_field_name("definition")
            if child.type == "decorated_definition"
            else child
        )
        if node is None or node.type != "function_definition":
            continue
        label = node.child_by_field_name("name")
        if label is not None and label.text.decode() == "__init__":
            return node
    return None


def _param_names(fn):
    head = fn.child_by_field_name("parameters")
    names = []
    for child in head.named_children if head is not None else ():
        name = one_name(child)
        if name and name not in ("self", "cls"):
            names.append(name)
    return names


def _super_message(init):
    """The parameter this __init__ passes to super().__init__, if that is the whole message."""
    params = set(_param_names(init))
    found = []

    def walk(node):
        if node.type in _NESTED:
            return
        if node.type == "call":
            fun = next(iter(node.named_children), None)
            attr = (
                fun.child_by_field_name("attribute")
                if fun is not None and fun.type == "attribute"
                else None
            )
            obj = (
                fun.child_by_field_name("object")
                if fun is not None and fun.type == "attribute"
                else None
            )
            owner = (
                obj.text.decode()
                if obj is not None and obj.type == "identifier"
                else _callee(obj)
            )
            if (
                attr is not None
                and attr.text.decode() == "__init__"
                and (owner == "super" or _builtin_error(owner))
            ):
                positional, _named, splat = call_arguments(node)
                if obj is not None and obj.type == "identifier" and positional:
                    first = positional[0]
                    if first.type == "identifier" and first.text.decode() in (
                        "self",
                        "cls",
                    ):
                        positional = positional[1:]
                if (
                    not splat
                    and len(positional) == 1
                    and positional[0].type == "identifier"
                ):
                    name = positional[0].text.decode()
                    if name in params:
                        found.append(name)
        for child in node.named_children:
            walk(child)

    body = _block(init)
    if body is not None:
        walk(body)
    return found[0] if len(set(found)) == 1 else None


def _message_slot(name, module):
    """How this error takes its message: one positional, the first, or a named parameter."""
    if _builtin_error(name):
        return ("sole", None)
    cls = _class_node(module, name)
    if cls is None:
        return None
    init = _init_method(cls)
    if init is not None:
        param = _super_message(init)
        return ("param", param) if param else None
    bases = cls.child_by_field_name("superclasses")
    names = (
        [part.text.decode() for part in bases.children if part.type == "identifier"]
        if bases is not None
        else []
    )
    if any(_builtin_error(base) for base in names):
        return ("sole", None)
    for base in names:
        slot = _message_slot(base, module)
        if slot:
            return slot
    return None


def _fstring(node, words):
    """An f-string made only of literals and module constants, or None."""
    raw = node.text.decode().lstrip()
    if raw[:1].lower() != "f" and raw[:2].lower() not in ("fr", "rf"):
        return None
    parts = []
    for child in node.named_children:
        if child.type in ("string_start", "string_end"):
            continue
        if child.type == "string_content":
            text = child.text.decode()
            if "\\" in text or "{{" in text or "}}" in text:
                return None
            parts.append(text)
            continue
        if child.type != "interpolation":
            return None
        body = child.text.decode()[1:-1]
        if "!" in body or ":" in body or "=" in body:
            return None
        expr = next(iter(child.named_children), None)
        if expr is not None and expr.type == "string":
            value = string_literal(expr)
        elif expr is not None and expr.type == "identifier":
            value = words.get(expr.text.decode())
        else:
            value = None
        if not isinstance(value, str):
            return None
        parts.append(value)
    return "".join(parts) if parts else None


def _string_value(node, words, local_strings):
    if node is None:
        return None
    if node.type == "concatenated_string":
        parts = [
            _string_value(child, words, local_strings) for child in node.named_children
        ]
        return None if any(part is None for part in parts) else "".join(parts)
    if node.type == "string":
        return string_literal(node) or _fstring(node, words)
    if node.type == "identifier":
        value = local_strings.get(node.text.decode(), words.get(node.text.decode()))
        return value if isinstance(value, str) else None
    return None


def _click_message(expr, scope):
    """click.ClickException and click.UsageError take the message first. A same-named class does not."""
    fun = next(iter(expr.named_children), None) if expr is not None else None
    path = resolve(fun, scope)
    return path in {("click", "ClickException"), ("click", "UsageError")}


def _quoted_message(expr, words, local_strings, module, scope):
    """The message, when this raise proves which argument is that message. A helper's argument is not one."""
    if expr is None or expr.type != "call":
        return None
    slot = (
        ("first", None)
        if _click_message(expr, scope)
        else _message_slot(_callee(expr), module)
    )
    if slot is None:
        return None
    positional, named, splat = call_arguments(expr)
    if splat:
        return None
    kind, param = slot
    if kind == "sole":
        if named or len(positional) != 1:
            return None
        return _string_value(positional[0], words, local_strings)
    if kind == "first":
        arg = positional[0] if positional else named.get("message")
        return _string_value(arg, words, local_strings)
    cls = _class_node(module, _callee(expr))
    init = _init_method(cls) if cls is not None else None
    names = _param_names(init) if init is not None else []
    if param not in names:
        return None
    bound = {}
    for index, arg in enumerate(positional):
        if index >= len(names):
            return None
        bound[names[index]] = arg
    for key, arg in named.items():
        if key not in names:
            return None
        bound[key] = arg
    return _string_value(bound.get(param), words, local_strings)


def _stmt_exit(stmt, fn):
    node = (
        stmt.named_children[0]
        if stmt.type == "expression_statement" and stmt.named_children
        else stmt
    )
    if node.type == "raise_statement":
        return "raise" if _escapes(node, fn) else "fall"
    if node.type in ("return_statement", "yield"):
        return "return"
    if node.type == "if_statement":
        body = _block(node)
        then = _seq(body.named_children if body is not None else [], fn)
        alt = node.child_by_field_name("alternative")
        if alt is None:
            other = "fall"
        elif alt.type == "else_clause":
            block = _block(alt)
            other = _seq(block.named_children if block is not None else [], fn)
        elif alt.type == "if_statement":
            other = _stmt_exit(alt, fn)
        else:
            other = "mixed"
        return then if then == other else "mixed"
    if node.type in (
        "for_statement",
        "while_statement",
        "try_statement",
        "with_statement",
        "match_statement",
    ):
        return "mixed"
    return "fall"


def _seq(stmts, fn):
    for stmt in stmts:
        if _doc(stmt):
            continue
        kind = _stmt_exit(stmt, fn)
        if kind != "fall":
            return kind
    return "fall"


def _always_raises(fn):
    body = _block(fn)
    return body is not None and _seq(body.named_children, fn) == "raise"


def _returned_call(block):
    for stmt in block.named_children if block is not None else ():
        if stmt.type != "return_statement":
            continue
        expr = next(iter(stmt.named_children), None)
        if expr is not None and expr.type == "call":
            fun = next(iter(expr.named_children), None)
            return _callee(fun if fun is not None and fun.type == "call" else expr)
    return None


def _read_if(node, story, params, assigns, facts, fn):
    cond = node.child_by_field_name("condition")
    body = _block(node)
    if cond is None:
        return
    text = cond.text.decode()
    if cond.type == "comparison_operator" and "is not" in text and "None" in text:
        ident = next(
            (c.text.decode() for c in cond.named_children if c.type == "identifier"),
            None,
        )
        if ident in params and _returned_call(body) == ident:
            story.given = True
            story.given_at = (
                (body.start_byte, body.end_byte) if body is not None else None
            )
    if cond.type != "comparison_operator" or "is not" in text:
        return
    kids = list(cond.named_children)
    if len(kids) < 2 or kids[0].type != "identifier" or kids[-1].type != "string":
        return
    lit = string_literal(kids[-1])
    env = _fact_name(assigns.get(kids[0].text.decode()), facts)
    call = _returned_call(body)
    if lit and env and call and call not in params and not _exc(call):
        story.choices.append((lit, call, env))


def _function(fn, scope, words, bound, story, attrs):
    facts = security_facts(fn, scope, words)
    head = fn.child_by_field_name("parameters")
    defaults, params, literal_defaults = {}, set(), set()
    for child in head.named_children if head is not None else ():
        name = one_name(child)
        if name and name != "self":
            params.add(name)
            defaults[name] = _param_default(child, bound)
            if child.type in ("default_parameter", "typed_default_parameter"):
                if child.named_children[-1].type == "integer":
                    literal_defaults.add(name)
    assigns, local_strings, fixed_env = {}, {}, set()
    timeout_locals = {}
    groups = {name: value for name, value in bound.items() if isinstance(value, tuple)}

    def timeout_of(expr):
        if expr is None:
            return None
        if expr.type == "integer":
            try:
                return int(expr.text.decode(), 0), False
            except ValueError:
                return None
        if expr.type == "identifier":
            name = expr.text.decode()
            # A named constant or an expression is not a readable literal.
            if name in literal_defaults and isinstance(defaults.get(name), int):
                return defaults[name], True
            attr = timeout_locals.get(name)
            if attr in story.timeout_attrs and isinstance(attrs.get(attr), int):
                return attrs[attr], True
        attr = _self_attr(expr)
        if attr in story.timeout_attrs and isinstance(attrs.get(attr), int):
            return attrs[attr], True
        return None

    def from_timeout(expr):
        if expr is None or expr.type != "identifier":
            return False
        name = expr.text.decode()
        if name in literal_defaults:
            return True
        if name in timeout_locals and timeout_locals[name] in story.timeout_attrs:
            return True
        attr = _self_attr(expr)
        return bool(attr and attr in story.timeout_attrs)

    def note_time(call, expr):
        """One shared time, or none. Every blocking call must show the same literal."""
        if story.time_bad:
            return
        found = timeout_of(expr)
        if not found:
            story.time_bad = True
            story.timeout = None
            return
        number, default = found
        loop = _in_retry(call, fn)
        if story.timeout is None:
            story.timeout, story.timeout_default, story.timeout_loop = (
                number,
                default,
                loop,
            )
            return
        if (story.timeout, story.timeout_default, story.timeout_loop) != (
            number,
            default,
            loop,
        ):
            story.time_bad = True
            story.timeout = None

    def visit(cur, in_if=False):
        if cur.type in _NESTED:
            return
        if cur.type == "assignment":
            kids = list(cur.named_children)
            if len(kids) >= 2:
                for target, value in zip(_items(kids[0]), _items(kids[-1])):
                    if target is None or value is None:
                        continue
                    if target.type == "identifier":
                        name = target.text.decode()
                        assigns[name] = value
                        text = string_literal(value)
                        if text:
                            local_strings[name] = text
                        else:
                            local_strings.pop(name, None)
                        if _keeps(value, scope, groups):
                            fixed_env.add(name)
                        attr = _self_attr(value)
                        if attr and attr in story.timeout_attrs:
                            timeout_locals[name] = attr
                    got = _from_param(value, defaults)
                    attr = _self_attr(target)
                    if attr and got is not None:
                        attrs[attr] = got
                        if (
                            value.type == "identifier"
                            and value.text.decode() in literal_defaults
                            and isinstance(got, int)
                        ):
                            story.timeout_attrs.add(attr)
        if cur.type == "if_statement":
            _read_if(cur, story, params, assigns, facts, fn)
        if cur.type == "raise_statement" and _escapes(cur, fn):
            story.raises = True
            expr = next(iter(cur.named_children), None)
            cond = _guard(cur, fn, assigns, facts, params)
            text = _quoted_message(expr, words, local_strings, _module(fn), scope)
            story.events.append((cond, text, _if_stops(cur, fn) if cond else False))
        if cur.type == "return_statement":
            expr = next(iter(cur.named_children), None)
            stdout = bool(_command_stdout(expr, assigns, scope, fn))
            # Every return has to be that stdout. One path that returns something else drops the claim.
            story.command_output = (
                stdout
                if story.command_output is None
                else story.command_output and stdout
            )
        if cur.type == "return_statement" and story.choices and not in_if:
            expr = next(iter(cur.named_children), None)
            if expr is not None and expr.type == "call":
                fun = next(iter(expr.named_children), None)
                name = _callee(fun if fun is not None and fun.type == "call" else expr)
                if name and name not in params and not _exc(name):
                    story.otherwise = name
        if cur.type == "call":
            fun = next(iter(cur.named_children), None)
            path = resolve(fun, scope)
            positional, named, _splat = call_arguments(cur)
            if reads_env(path) and story.given and story.given_at is not None:
                start, end = story.given_at
                if not (start <= cur.start_byte and cur.end_byte <= end):
                    story.skipped_env = _fact_name(cur, facts) or story.skipped_env
            if call_way(path) == "command" and positional:
                command = _exec(positional[0], assigns, defaults, attrs)
                named_command = command if isinstance(command, str) else False
                if story.command is None:
                    story.command = named_command
                elif story.command != named_command:
                    story.command = False
                env = named.get("env")
                if env is not None and (
                    (env.type == "identifier" and env.text.decode() in fixed_env)
                    or _keeps(env, scope, groups)
                ):
                    story.keeps_env = True
            if _takes_seconds(path, cur, facts):
                expr = named.get("timeout")
                if expr is None:
                    expr = next(
                        (value for value in positional if from_timeout(value)), None
                    )
                if expr is not None or (path and path[-1] in _WAITS):
                    note_time(cur, expr)
            for name in _bearer(cur, assigns, facts):
                story.keys.add(name)
                story.bearer = name
        nested = in_if or cur.type == "if_statement"
        for child in cur.named_children:
            visit(child, nested)

    body = _block(fn)
    for stmt in body.named_children if body is not None else ():
        if _doc(stmt):
            continue
        visit(stmt)
    if not _always_raises(fn):
        story.always = False
    if story.is_class:
        return
    claim = _return_claim(fn, scope, facts)
    if claim is None:
        return
    if claim[0] in ("yield", "other") or (
        story.returns not in (None, False) and story.returns != claim
    ):
        story.returns = False
    elif story.returns is not False:
        story.returns = claim


def for_box(node, scope, words):
    """At most three plain sentences. [] when this box proves nothing of the sort."""
    if node.type not in ("function_definition", "class_definition"):
        return []
    label = node.child_by_field_name("name")
    story = _Story(label.text.decode() if label is not None else "This")
    story.is_class = node.type == "class_definition"
    bases = (
        node.child_by_field_name("superclasses")
        if node.type == "class_definition"
        else None
    )
    if bases is not None and any(
        part.type == "identifier" and part.text.decode() == "Exception"
        for part in bases.children
    ):
        story.exception = True
    words = words or {}
    bound = assigned(list(_module(node).named_children))
    bound.update(words)
    attrs = {}
    _apply_facts(story, node, scope, words)
    for fn in _methods(node):
        _function(fn, layer(fn, scope), words, bound, story, attrs)
    if story.returns is False:
        story.returns = None
    return _sentences(story)
