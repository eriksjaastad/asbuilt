"""Up to three everyday sentences for one box, from facts the parser proved.

A docstring is not copied. These sentences do not replace a security sentence:
the page shows the security lines after them, expert included. No model is called.
"""

import ast
import ipaddress
from urllib.parse import urlparse

from asbuilt.built import one_name, pattern
from asbuilt.marks import layer, reaches_out, reads_env, resolve
from asbuilt.security import facts as security_facts

_FAIL = (
    ("missing", "the key is missing"),
    ("refused", "the call is refused"),
    ("unreachable", "the site can't be reached"),
    ("unreadable", "the reply has no readable text"),
    ("start", "the command cannot start"),
    ("finish", "it does not finish"),
    ("failed", "the command fails"),
    ("signed", "it is not signed in"),
    ("empty", "it returns no output"),
)
_SEND = ("data", "json", "body", "content", "files", "payload")


def _host_of(text):
    parsed = urlparse(text) if isinstance(text, str) else None
    if parsed is not None and parsed.scheme in ("http", "https") and parsed.hostname:
        return parsed.hostname
    return None


def _public(host):
    if not host or host.lower().rstrip(".") == "localhost":
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (addr.is_loopback or addr.is_unspecified or addr.is_private)


def _literal(node):
    if node is None or node.type != "string":
        return None
    try:
        value = ast.literal_eval(node.text.decode())
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) and value else None


def _string_bits(node):
    lit = _literal(node)
    if lit:
        return lit
    if node is not None and node.type == "string":
        return "".join(c.text.decode() for c in node.children if c.type == "string_content")
    return ""


def _module(node):
    root = node
    while root.parent is not None:
        root = root.parent
    return root


def _assign(node):
    cur = node
    if cur.type == "expression_statement":
        cur = next(iter(cur.named_children), cur)
    if cur.type != "assignment" or len(cur.named_children) < 2:
        return None
    return cur.named_children[0], cur.named_children[-1]


def _ints(node):
    found = {}
    for child in _module(node).named_children:
        pair = _assign(child)
        if pair is None:
            continue
        names, value = pattern(pair[0]), pair[1]
        if len(names) == 1 and value.type == "integer" and names[0] not in found:
            found[names[0]] = int(value.text.decode())
        elif names:
            found[names[0]] = None
    return {name: number for name, number in found.items() if isinstance(number, int)}


def _groups(node):
    """Names assigned once to a tuple or list of strings. A second binding drops the name."""
    found = {}
    for child in _module(node).named_children:
        pair = _assign(child)
        if pair is None:
            continue
        names, value = pattern(pair[0]), pair[1]
        texts = [_literal(c) for c in value.named_children] if value.type in ("tuple", "list") else []
        if len(names) == 1 and texts and all(texts) and names[0] not in found:
            found[names[0]] = True
        elif names:
            found[names[0]] = None
    return {name for name, ok in found.items() if ok}


def _default(node, words, numbers):
    if node.type not in ("default_parameter", "typed_default_parameter"):
        return None
    value = node.named_children[-1]
    if value.type == "integer":
        return int(value.text.decode())
    if value.type == "identifier":
        name = value.text.decode()
        return numbers.get(name, words.get(name))
    bits = []

    def walk(cur):
        lit = _literal(cur)
        if lit:
            bits.append(lit)
            return
        for child in cur.named_children:
            walk(child)

    walk(value)
    return bits[0] if len(bits) == 1 else None


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


def _self_attr(node):
    if node is None or node.type != "attribute":
        return None
    obj, attr = node.child_by_field_name("object"), node.child_by_field_name("attribute")
    if obj is not None and obj.type == "identifier" and obj.text.decode() == "self" and attr is not None:
        return attr.text.decode()
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


def _is_exc(name):
    return bool(name) and (name == "Exception" or name.endswith(("Error", "Exception", "Failed")))


def _items(node):
    if node is not None and node.type in ("pattern_list", "expression_list", "tuple", "list"):
        return list(node.named_children)
    return [node]


def _block(node):
    for field in ("consequence", "body"):
        found = node.child_by_field_name(field)
        if found is not None:
            return found
    return None


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


def _bound_string(node, words):
    if node is None:
        return None
    if node.type == "string":
        return _literal(node)
    if node.type == "identifier":
        value = words.get(node.text.decode())
        return value if isinstance(value, str) else None
    return None


def _env_name(node, words):
    """The environment name this expression reads, when it is a string or one string constant."""
    if node is None:
        return None
    if node.type == "call":
        args = node.child_by_field_name("arguments")
        kids = list(args.named_children) if args is not None else []
        if kids:
            return _bound_string(kids[0], words)
    if node.type == "subscript" and "environ" in node.text.decode():
        kids = list(node.named_children)
        if len(kids) >= 2:
            return _bound_string(kids[-1], words)
    for child in node.named_children:
        found = _env_name(child, words)
        if found:
            return found
    return None


def _from_env(node):
    return node is not None and ("environ" in node.text.decode() or "getenv" in node.text.decode())


def _from_default(value, defaults):
    if value is None:
        return None
    if value.type == "identifier" and value.text.decode() in defaults:
        return defaults[value.text.decode()]
    if value.type == "call" and _callee(value) in ("list", "tuple"):
        args = value.child_by_field_name("arguments")
        kids = [c for c in (args.named_children if args is not None else ()) if c.type == "identifier"]
        if len(kids) == 1 and kids[0].text.decode() in defaults:
            return defaults[kids[0].text.decode()]
    return None


def _content_key(node):
    if node is None or node.type != "subscript":
        return False
    kids = list(node.named_children)
    return len(kids) >= 2 and _string_bits(kids[-1]) == "content"


def _fixed_filter(node, groups):
    if node is None or "environ.items" not in node.text.decode():
        return False

    def walk(cur):
        if cur.type == "comparison_operator" and " in " in f" {cur.text.decode()} ":
            for child in cur.named_children:
                if child.type == "identifier" and child.text.decode() in groups:
                    return True
                if child.type in ("tuple", "list") and all(_literal(c) for c in child.named_children):
                    return True
        return any(walk(c) for c in cur.named_children)

    return walk(node)


def _is_command(path):
    return bool(path) and (path[0] == "subprocess" or path[:2] == ("os", "system"))


def _contains_network(expr, scope):
    if expr is None:
        return False
    if expr.type == "call":
        fun = next(iter(expr.named_children), None)
        path = resolve(fun, scope) if fun is not None else None
        if reaches_out(path) and not _is_command(path):
            return True
    return any(_contains_network(child, scope) for child in expr.named_children)


def _args(node):
    named, positional = {}, []
    args = node.child_by_field_name("arguments")
    for child in args.named_children if args is not None else ():
        if child.type == "keyword_argument":
            key, val = child.child_by_field_name("name"), child.child_by_field_name("value")
            if key is not None and val is not None:
                named[key.text.decode()] = val
        elif child.type not in ("list_splat", "dictionary_splat"):
            positional.append(child)
    return named, positional


class _Story:
    def __init__(self, name):
        self.name = name
        self.exception = False
        self.bearer = None
        self.sends = False
        self.sends_prompt = False
        self.sends_input = False
        self.host = None
        self.network = False
        self.runs = False
        self.command = None
        self.keeps_env = False
        self.timeout = None
        self.timeout_default = False
        self.failures = []
        self.choices = []
        self.otherwise = None
        self.given = False
        self.given_at = None
        self.skipped_env = None
        self.returns_reply = False
        self.returns_output = False
        self.returns_network = False
        self.returns_none = False
        self.literal = None
        self.error_from = None

    def mark(self, tag):
        if tag not in self.failures:
            self.failures.append(tag)


def _tag(story, text):
    if "nothing it could read" in text:
        story.mark("unreadable")
    elif "replied with nothing" in text:
        story.mark("empty")
    if "refused" in text:
        story.mark("refused")
    if "could not be reached" in text or "cannot be reached" in text:
        story.mark("unreachable")
    if "not signed in" in text:
        story.mark("signed")
    if "was not found" in text or "cannot be started" in text:
        story.mark("start")
    if "gave no reply" in text:
        story.mark("finish")


def _chunks(node, words):
    found = []
    if node.type == "identifier" and isinstance(words.get(node.text.decode()), str):
        found.append(words[node.text.decode()])
    bit = _string_bits(node)
    if bit:
        found.append(bit)
    for child in node.named_children:
        found.extend(_chunks(child, words))
    return found


def _or(items):
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + ", or " + items[-1]


def _fail(story):
    bits = [phrase for tag, phrase in _FAIL if tag in story.failures]
    if not bits:
        return None
    line = f"If {_or(bits)}, it stops with an error"
    if story.returns_reply and not story.runs:
        return line + "; otherwise it returns the text from the reply."
    if story.returns_output and not story.runs and not story.command:
        return line + "; otherwise it returns the command's output."
    return line + "."


def _choice_line(story):
    if not story.choices and not story.otherwise:
        return None
    same = len({item[2] for item in story.choices}) == 1 if story.choices else False
    bits = []
    for index, (lit, callee, env) in enumerate(story.choices):
        if index == 0 or not same:
            bits.append(f"{callee} when {env} is {lit}")
        else:
            bits.append(f"{callee} when it is {lit}")
    head = "Otherwise it calls" if story.given else "It calls"
    if bits and story.otherwise:
        return f"{head} {', '.join(bits)}, and {story.otherwise} otherwise."
    if bits:
        return f"{head} {', '.join(bits)}."
    if story.otherwise:
        return f"{head} {story.otherwise}."
    return None


def _sentences(story):
    out = []

    def add(text):
        if text and len(out) < 3:
            out.append(text)

    if story.given or story.choices or story.otherwise:
        if story.given:
            skip = f" and does not read {story.skipped_env}" if story.skipped_env else ""
            add(f"If {story.name} is given something to call, it returns whatever that returns{skip}.")
        add(_choice_line(story))
        return out
    if story.exception and not (story.network or story.runs or story.failures or story.literal or story.returns_reply):
        return [f"{story.name} is an exception."]
    if isinstance(story.literal, str) and not (story.network or story.runs or story.failures or story.returns_reply):
        return [f"{story.name} returns the text {story.literal}."]
    if story.returns_none and not (
        story.network or story.runs or story.failures or story.returns_reply or story.returns_output or story.literal
    ):
        return [f"{story.name} returns nothing."]
    if story.sends and story.host and _public(story.host):
        what = "a prompt" if story.sends_prompt else "what it was given" if story.sends_input else "data"
        key = f", using a key read from {story.bearer}" if story.bearer else ""
        add(f"{story.name} sends {what} to {story.host} over the internet{key}.")
    elif story.sends and story.host and not _public(story.host):
        what = "a prompt" if story.sends_prompt else "what it was given" if story.sends_input else "data"
        add(f"{story.name} sends {what} to {story.host}.")
    elif story.runs:
        shown = story.command or "a command"
        line = f"{story.name} runs the command {shown}" if story.command else f"{story.name} runs a command"
        if story.keeps_env and story.returns_output:
            line += ", keeps only a fixed set of environment names, and returns the command's output"
        elif story.keeps_env:
            line += " and keeps only a fixed set of environment names"
        elif story.returns_output:
            line += " and returns the command's output"
        add(line + ".")
    elif story.returns_network:
        add(f"{story.name} returns whatever the network request returns.")
    elif story.error_from:
        add(f"{story.name} returns an error made from {story.error_from}.")
    if isinstance(story.timeout, int):
        add(
            f"By default it waits up to {story.timeout} seconds."
            if story.timeout_default
            else f"It waits up to {story.timeout} seconds."
        )
    add(_fail(story))
    return out


def _apply_facts(story, node, scope, words):
    for fact in security_facts(node, scope, words):
        if fact["kind"] != "calls out":
            continue
        if fact.get("way") == "command":
            story.runs = True
        if fact.get("way") in ("send", "connect"):
            story.network = True
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
            if fun is not None and fun.type == "call":
                return _callee(fun)
            return _callee(expr)
    return None


def _read_if(node, story, params, assigns, words):
    cond = node.child_by_field_name("condition")
    body = _block(node)
    if cond is None:
        return
    text = cond.text.decode()
    if cond.type == "not_operator":
        ident = next((c.text.decode() for c in cond.named_children if c.type == "identifier"), None)
        if ident and _from_env(assigns.get(ident)):
            story.mark("missing")
    if "returncode" in text and body is not None and "raise" in body.text.decode():
        story.mark("failed")
    if "stdout" in text and body is not None and "raise" in body.text.decode():
        story.mark("empty")
    if cond.type == "comparison_operator" and "is not" in text and "None" in text:
        name = next((c.text.decode() for c in cond.named_children if c.type == "identifier"), None)
        if name in params and _returned_call(body) == name:
            story.given = True
            story.given_at = (body.start_byte, body.end_byte) if body is not None else None
    if cond.type != "comparison_operator" or "is not" in text:
        return
    kids = list(cond.named_children)
    if len(kids) < 2 or kids[0].type != "identifier" or kids[-1].type != "string":
        return
    lit = _literal(kids[-1])
    env = _env_name(assigns.get(kids[0].text.decode()), words)
    call = _returned_call(body)
    if lit and env and call and call not in params and not _is_exc(call):
        story.choices.append((lit, call, env))


def _last_output_line(expr, assigns):
    """True when this call takes the last line of stdout or stderr."""

    def walk(cur):
        if cur.type == "attribute":
            attr = cur.child_by_field_name("attribute")
            obj = cur.child_by_field_name("object")
            if attr is not None and attr.text.decode() == "splitlines" and obj is not None:
                source = obj.text.decode() if obj.type != "identifier" else None
                if obj.type == "identifier":
                    earlier = assigns.get(obj.text.decode())
                    source = earlier.text.decode() if earlier is not None else ""
                if source and ("stdout" in source or "stderr" in source):
                    return True
        return any(walk(child) for child in cur.named_children)

    return walk(expr)


def _read_return(node, story, assigns, params, scope, in_if):
    expr = next(iter(node.named_children), None)
    if expr is None or expr.type == "none":
        story.returns_none = True
        return
    if expr.type == "string":
        lit = _literal(expr)
        story.literal = lit if story.literal in (None, lit) else False
        return
    if expr.type == "attribute" and _self_attr(expr) is None:
        attr = expr.child_by_field_name("attribute")
        if attr is not None and attr.text.decode() == "stdout":
            story.returns_output = True
        return
    if expr.type == "identifier" and _content_key(assigns.get(expr.text.decode())):
        story.returns_reply = True
        return
    if _content_key(expr):
        story.returns_reply = True
        return
    if expr.type != "call":
        return
    fun = next(iter(expr.named_children), None)
    name = _callee(fun if fun is not None and fun.type == "call" else expr)
    if _is_exc(name):
        raw = expr.text.decode()
        bits = []
        if "returncode" in raw:
            bits.append("the command's exit code")
        if _last_output_line(expr, assigns):
            bits.append("the last line of its output")
        if bits:
            story.error_from = " and ".join(bits)
        return
    if name in params:
        return
    if story.choices and name and not in_if:
        story.otherwise = name
        return
    if _contains_network(expr, scope):
        story.returns_network = True
        story.network = True


def _read_call(node, story, params, assigns, words, groups, fixed_env, attrs, number_of, scope):
    fun = next(iter(node.named_children), None)
    path = resolve(fun, scope)
    named, positional = _args(node)
    if reads_env(path):
        span = story.given_at
        outside = span is None or not (span[0] <= node.start_byte and node.end_byte <= span[1])
        if story.given and outside:
            story.skipped_env = _env_name(node, words) or story.skipped_env
    sent = None
    for key in _SEND:
        if key in named:
            sent = named[key]
            story.sends = True
    if path and path[-1] in ("post", "put", "patch", "Request") and len(positional) > 1 and sent is None:
        sent = positional[1]
        story.sends = True
    if sent is not None:
        names = _names(sent, assigns)
        if "prompt" in names and "prompt" in params:
            story.sends_prompt = True
        elif names & params:
            story.sends_input = True
    url = named.get("url") or (positional[0] if positional else None)
    host = None
    if url is not None and url.type == "string":
        host = _host_of(_literal(url))
    elif url is not None and url.type == "identifier":
        host = _host_of(words.get(url.text.decode()))
    if host:
        story.host = host
    if reaches_out(path) or (path and path[-1] in ("Request", "urlopen", "open")):
        story.network = True
    if _is_command(path):
        story.runs = True
        if "command" in _names(node, assigns) and isinstance(attrs.get("command"), str):
            story.command = attrs["command"]
        env = named.get("env")
        if env is not None and (
            (env.type == "identifier" and env.text.decode() in fixed_env) or _fixed_filter(env, groups)
        ):
            story.keeps_env = True
    if "timeout" in named:
        number_of(named["timeout"])
    for value in positional:
        if _self_attr(value) == "timeout" or (value.type == "identifier" and value.text.decode() == "timeout"):
            number_of(value)


def _function(fn, scope, words, numbers, groups, story, attrs):
    head = fn.child_by_field_name("parameters")
    defaults, params = {}, set()
    for child in head.named_children if head is not None else ():
        name = one_name(child)
        if name and name != "self":
            params.add(name)
            defaults[name] = _default(child, words, numbers)
    assigns, local, fixed_env = {}, {}, set()

    def number_of(expr):
        if expr is None:
            return
        found, defaulted = None, False
        if expr.type == "integer":
            found = int(expr.text.decode())
        elif expr.type == "identifier":
            name = expr.text.decode()
            if isinstance(defaults.get(name), int):
                found, defaulted = defaults[name], True
            elif name in local:
                found, defaulted = local[name]
            elif isinstance(numbers.get(name), int):
                found = numbers[name]
        else:
            attr = _self_attr(expr)
            if attr and isinstance(attrs.get(attr), int):
                found, defaulted = attrs[attr], True
        if isinstance(found, int):
            story.timeout = found
            story.timeout_default = defaulted

    def visit(cur, in_if=False):
        if cur.type in ("function_definition", "class_definition", "lambda"):
            return
        if cur.type == "assignment":
            kids = list(cur.named_children)
            if len(kids) >= 2:
                for target, value in zip(_items(kids[0]), _items(kids[-1])):
                    if target is None or value is None:
                        continue
                    if target.type == "identifier":
                        assigns[target.text.decode()] = value
                        attr = _self_attr(value)
                        if attr and isinstance(attrs.get(attr), int):
                            local[target.text.decode()] = (attrs[attr], True)
                        if _fixed_filter(value, groups):
                            fixed_env.add(target.text.decode())
                    got = _from_default(value, defaults)
                    attr = _self_attr(target)
                    if attr and got is not None:
                        attrs[attr] = got
        if cur.type == "if_statement":
            _read_if(cur, story, params, assigns, words)
        if cur.type in ("raise_statement", "except_clause"):
            for chunk in _chunks(cur, words):
                _tag(story, chunk)
        if cur.type == "return_statement":
            _read_return(cur, story, assigns, params, scope, in_if)
        if cur.type == "call":
            _read_call(cur, story, params, assigns, words, groups, fixed_env, attrs, number_of, scope)
        if cur.type == "string" and "Bearer" in cur.text.decode():
            for child in cur.named_children:
                if child.type != "interpolation":
                    continue
                ident = next((c for c in child.named_children if c.type == "identifier"), None)
                source = assigns.get(ident.text.decode()) if ident is not None else None
                if _from_env(source):
                    story.bearer = _env_name(source, words) or story.bearer
        nested = in_if or cur.type == "if_statement"
        for child in cur.named_children:
            visit(child, nested)

    body = _block(fn)
    for stmt in body.named_children if body is not None else ():
        if _doc(stmt):
            continue
        visit(stmt)


def for_box(node, scope, words):
    """At most three plain sentences. [] when this box proves nothing of the sort."""
    if node.type not in ("function_definition", "class_definition"):
        return []
    label = node.child_by_field_name("name")
    story = _Story(label.text.decode() if label is not None else "This")
    bases = node.child_by_field_name("superclasses") if node.type == "class_definition" else None
    if bases is not None and any(part.type == "identifier" and part.text.decode() == "Exception" for part in bases.children):
        story.exception = True
    words = words or {}
    numbers, groups, attrs = _ints(node), _groups(node), {}
    _apply_facts(story, node, scope, words)
    for fn in _methods(node):
        _function(fn, layer(fn, scope), words, numbers, groups, story, attrs)
    return _sentences(story)
