"""Up to three everyday sentences for one box, from facts the parser proved.

A docstring is not copied. These sentences do not replace a security sentence:
the page shows the security lines after them, expert included. No model is called.
"""

import json

from asbuilt.built import one_name
from asbuilt.marks import layer, reaches_out, reads_env, resolve
from asbuilt.security import (
    assigned,
    call_arguments,
    call_way,
    facts as security_facts,
    host_name,
    local_host,
    sent_body,
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
        inner = child.child_by_field_name("definition") if child.type == "decorated_definition" else child
        if inner is not None and inner.type == "function_definition":
            yield inner


def _doc(stmt):
    child = next(iter(stmt.named_children), None) if stmt.type == "expression_statement" else None
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
    obj, attr = node.child_by_field_name("object"), node.child_by_field_name("attribute")
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
    return bool(name) and (name == "Exception" or name.endswith(("Error", "Exception", "Failed")))


def _or(items):
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + ", or " + items[-1]


def _walk(node, visit):
    """Visit this statement and what it contains, not a nested function or class."""
    if node.type in _NESTED:
        return
    visit(node)
    for child in node.named_children:
        _walk(child, visit)


def _names(node, assigns, depth=0):
    found = set()
    if node is None:
        return found
    if node.type == "identifier":
        name = node.text.decode()
        found.add(name)
        if depth < 3 and name in assigns:
            found |= _names(assigns[name], assigns, depth + 1)
    for child in node.named_children:
        found |= _names(child, assigns, depth)
    return found


def _env_name(node, scope, words):
    """The one environment name this expression reads, or None."""
    found = []

    def walk(cur):
        if cur.type == "call" and reads_env(resolve(next(iter(cur.named_children), None), scope)):
            positional, named, _splat = call_arguments(cur)
            arg = positional[0] if positional else named.get("key")
            name = string_literal(arg) if arg is not None else None
            if name is None and arg is not None and arg.type == "identifier":
                value = words.get(arg.text.decode())
                name = value if isinstance(value, str) else None
            if name:
                found.append(name)
        for child in cur.named_children:
            walk(child)

    if node is not None:
        walk(node)
    return found[0] if len(set(found)) == 1 else None


def _contains(outer, inner):
    return outer is not None and inner is not None and outer.start_byte <= inner.start_byte and inner.end_byte <= outer.end_byte


def _escapes(node, fn):
    """True when this raise leaves the function: not swallowed by a try here."""
    cur = node.parent
    while cur is not None and cur != fn and cur.type not in ("function_definition", "class_definition"):
        if cur.type == "try_statement":
            body = cur.child_by_field_name("body")
            caught = _contains(body, node) and any(child.type == "except_clause" for child in cur.children)
            if caught:
                return False
        cur = cur.parent
    return True


def _escaping_raise(node, fn):
    found = False

    def visit(cur):
        nonlocal found
        if cur.type == "raise_statement" and _escapes(cur, fn):
            found = True

    if node is not None:
        _walk(node, visit)
    return found


def _in_loop(node, fn):
    cur = node.parent
    while cur is not None and cur != fn and cur.type not in _NESTED:
        if cur.type in ("for_statement", "while_statement"):
            return True
        cur = cur.parent
    return False


def _takes_seconds(path, call, facts):
    """True for a call whose timeout is in seconds, not milliseconds."""
    if path and (path[0] in ("requests", "httpx", "socket", "subprocess") or path[:2] == ("urllib", "request")):
        return True
    for fact in facts:
        if fact.get("timeout") != "passed":
            continue
        if not (call.start_byte <= fact.get("start", -1) < call.end_byte):
            continue
        at = fact.get("at") or ""
        if at.startswith(("urllib.", "requests.", "httpx.", "socket.", "subprocess.")):
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
        if cur.type == "call" and reads_env(resolve(next(iter(cur.named_children), None), scope)):
            reads = True
        if cur.type == "comparison_operator" and "not in" not in cur.text.decode():
            keeps = any(child.text.decode() == "in" for child in cur.children)
            if keeps:
                for child in cur.named_children:
                    if child.type == "identifier" and isinstance(groups.get(child.text.decode()), tuple):
                        fixed = True
                    if child.type in ("tuple", "list") and child.named_children and all(string_literal(c) for c in child.named_children):
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
        return _exec(next(iter(node.named_children), None), assigns, defaults, attrs, depth + 1)
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
        if kind[0] == "other" and expr is not None and expr.type == "call" and _inside(expr, facts):
            kind = ("network", None)
        kinds.append(kind)
    if len(set(kinds)) != 1 or kinds[0][0] == "other":
        return ("other", None)
    return kinds[0]


def _message(expr, words, local_strings):
    """A plain string this raise carries, or None when the message is built up."""
    if expr is None:
        return None
    if expr.type == "string":
        return string_literal(expr)
    if expr.type == "identifier":
        name = expr.text.decode()
        if isinstance(local_strings.get(name), str):
            return local_strings[name]
        value = words.get(name)
        return value if isinstance(value, str) else None
    if expr.type == "call":
        positional, _named, _splat = call_arguments(expr)
        if positional:
            return _message(positional[0], words, local_strings)
    return None


def _bearer(node, assigns, scope, words):
    found = []
    if node.type == "string" and "Bearer" in node.text.decode():
        for child in node.named_children:
            if child.type != "interpolation":
                continue
            ident = next((c for c in child.named_children if c.type == "identifier"), None)
            source = assigns.get(ident.text.decode()) if ident is not None else child
            name = _env_name(source if source is not None else child, scope, words)
            if name:
                found.append(name)
    for child in node.named_children:
        found.extend(_bearer(child, assigns, scope, words))
    return found


class _Story:
    def __init__(self, name):
        self.name = name
        self.exception = False
        self.sends = False
        self.sends_input = False
        self.host = None
        self.command = None
        self.keeps_env = False
        self.is_class = False
        self.timeout = None
        self.timeout_default = False
        self.timeout_loop = False
        self.given = False
        self.given_at = None
        self.skipped_env = None
        self.choices = []
        self.otherwise = None
        self.raises = False
        self.messages = []
        self.missing = []
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
        bits.append(f"{callee} when {env} is {lit}" if index == 0 or not same else f"{callee} when it is {lit}")
    head = "Otherwise it calls" if story.given else f"{story.name} calls"
    if bits and story.otherwise:
        return f"{head} {', '.join(bits)}, and {story.otherwise} otherwise."
    if bits:
        return f"{head} {', '.join(bits)}."
    if story.otherwise:
        return f"{head} {story.otherwise}."
    return None


def _failure(story):
    if not story.raises:
        return None
    guards = []
    for name in story.missing:
        guards.append("the key is missing" if name in story.keys else f"the environment value {name} is missing")
    quoted = [f'"{text}"' for text in story.messages]
    name = story.name
    if guards and quoted:
        return f"If {_or(guards)}, {name} stops with an error, and it stops with an error that says {_or(quoted)}."
    if guards:
        return f"If {_or(guards)}, {name} stops with an error."
    if quoted:
        return f"{name} stops with an error that says {_or(quoted)}."
    return f"{name} stops with an error."


def _sentences(story):
    pending = []
    if story.given:
        skip = f" and does not read {story.skipped_env}" if story.skipped_env else ""
        pending.append(f"If {story.name} is given something to call, it returns whatever that returns{skip}.")
    choice = _choice_line(story)
    if choice:
        pending.append(choice)
    if not story.given and not story.choices and not story.otherwise:
        if story.sends and story.host:
            what = "what it was given" if story.sends_input else "data"
            net = "" if local_host(story.host) else " over the internet"
            key = f", using a key read from {story.bearer}" if story.bearer else ""
            pending.append(f"{story.name} sends {what} to {story.host}{net}{key}.")
        elif isinstance(story.command, str):
            line = f"{story.name} runs the command {story.command}"
            if story.keeps_env:
                line += " and keeps only a fixed set of environment names"
            pending.append(line + ".")
        elif story.exception and not story.returns:
            pending.append(f"{story.name} is an exception.")
    if isinstance(story.timeout, int):
        if story.timeout_loop and story.timeout_default:
            pending.append(f"By default each try, {story.name} waits up to {story.timeout} seconds.")
        elif story.timeout_loop:
            pending.append(f"Each try, {story.name} waits up to {story.timeout} seconds.")
        elif story.timeout_default:
            pending.append(f"By default {story.name} waits up to {story.timeout} seconds.")
        else:
            pending.append(f"{story.name} waits up to {story.timeout} seconds.")
    failed = _failure(story)
    if failed:
        pending.append(failed)
    if story.returns and not story.is_class and not story.given and not story.choices and not story.otherwise:
        pending.append(_return_line(story))
    return [line for line in pending if line][:3]


def _apply_facts(story, node, scope, words):
    for fact in security_facts(node, scope, words):
        if fact["kind"] != "calls out":
            continue
        if fact.get("sends"):
            story.sends = True
        host = fact.get("host")
        if host and story.host is None:
            story.host = host


def _returned_call(block):
    for stmt in block.named_children if block is not None else ():
        if stmt.type != "return_statement":
            continue
        expr = next(iter(stmt.named_children), None)
        if expr is not None and expr.type == "call":
            fun = next(iter(expr.named_children), None)
            return _callee(fun if fun is not None and fun.type == "call" else expr)
    return None


def _read_if(node, story, params, assigns, scope, words, fn):
    cond = node.child_by_field_name("condition")
    body = _block(node)
    if cond is None:
        return
    text = cond.text.decode()
    if cond.type == "not_operator" and body is not None and _escaping_raise(body, fn):
        ident = next((c.text.decode() for c in cond.named_children if c.type == "identifier"), None)
        source = assigns.get(ident) if ident else next(iter(cond.named_children), None)
        name = _env_name(source, scope, words)
        if name and name not in story.missing:
            story.missing.append(name)
    if cond.type == "comparison_operator" and "is not" in text and "None" in text:
        ident = next((c.text.decode() for c in cond.named_children if c.type == "identifier"), None)
        if ident in params and _returned_call(body) == ident:
            story.given = True
            story.given_at = (body.start_byte, body.end_byte) if body is not None else None
    if cond.type != "comparison_operator" or "is not" in text:
        return
    kids = list(cond.named_children)
    if len(kids) < 2 or kids[0].type != "identifier" or kids[-1].type != "string":
        return
    lit = string_literal(kids[-1])
    env = _env_name(assigns.get(kids[0].text.decode()), scope, words)
    call = _returned_call(body)
    if lit and env and call and call not in params and not _exc(call):
        story.choices.append((lit, call, env))


def _function(fn, scope, words, bound, story, attrs):
    facts = security_facts(fn, scope, words)
    head = fn.child_by_field_name("parameters")
    defaults, params = {}, set()
    for child in head.named_children if head is not None else ():
        name = one_name(child)
        if name and name != "self":
            params.add(name)
            defaults[name] = _param_default(child, bound)
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
            if isinstance(defaults.get(name), int):
                return defaults[name], True
            if name in timeout_locals and isinstance(attrs.get(timeout_locals[name]), int):
                return attrs[timeout_locals[name]], True
            if isinstance(bound.get(name), int):
                return bound[name], False
        attr = _self_attr(expr)
        if attr and isinstance(attrs.get(attr), int):
            return attrs[attr], True
        return None

    def from_timeout(expr):
        if expr is None:
            return False
        if expr.type == "identifier" and expr.text.decode() == "timeout" and isinstance(defaults.get("timeout"), int):
            return True
        if expr.type == "identifier" and expr.text.decode() in timeout_locals:
            return True
        attr = _self_attr(expr)
        return bool(attr and attr in story.timeout_attrs)

    def use_timeout(expr, keyword, call, path):
        if not (keyword or from_timeout(expr)) or not _takes_seconds(path, call, facts):
            return
        found = timeout_of(expr)
        if found:
            story.timeout, story.timeout_default = found
            story.timeout_loop = _in_loop(call, fn)

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
                        if value.type == "identifier" and value.text.decode() == "timeout" and isinstance(got, int):
                            story.timeout_attrs.add(attr)
        if cur.type == "if_statement":
            _read_if(cur, story, params, assigns, scope, words, fn)
        if cur.type == "raise_statement" and _escapes(cur, fn):
            story.raises = True
            text = _message(next(iter(cur.named_children), None), words, local_strings)
            if text and text not in story.messages:
                story.messages.append(text)
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
                    story.skipped_env = _env_name(cur, scope, words) or story.skipped_env
            body = sent_body(cur, path)
            if body is not None:
                story.sends = True
                if _names(body, assigns) & params:
                    story.sends_input = True
            url = named.get("url") or (positional[0] if positional else None)
            host = None
            if url is not None and url.type == "string":
                host = host_name(string_literal(url))
            elif url is not None and url.type == "identifier":
                value = bound.get(url.text.decode())
                host = host_name(value) if isinstance(value, str) else None
            if host:
                story.host = host
            if call_way(path) == "command" and positional:
                command = _exec(positional[0], assigns, defaults, attrs)
                if isinstance(command, str):
                    story.command = command if story.command in (None, command) else False
                env = named.get("env")
                if env is not None and (
                    (env.type == "identifier" and env.text.decode() in fixed_env) or _keeps(env, scope, groups)
                ):
                    story.keeps_env = True
            if "timeout" in named:
                use_timeout(named["timeout"], True, cur, path)
            for value in positional:
                use_timeout(value, False, cur, path)
            for name in _bearer(cur, assigns, scope, words):
                story.keys.add(name)
                story.bearer = name
        if cur.type == "string":
            for name in _bearer(cur, assigns, scope, words):
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
    if story.is_class:
        return
    claim = _return_claim(fn, scope, facts)
    if claim is None:
        return
    if claim[0] in ("yield", "other") or (story.returns not in (None, False) and story.returns != claim):
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
    bases = node.child_by_field_name("superclasses") if node.type == "class_definition" else None
    if bases is not None and any(part.type == "identifier" and part.text.decode() == "Exception" for part in bases.children):
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
