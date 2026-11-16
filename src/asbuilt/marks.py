"""The marks a box can prove from its own code. Not from a box it calls.

A kind is one row: the plain name, then which dotted calls count. Nested defs count,
because they are not their own boxes. Add a kind by appending a row.
"""

# (name, when a dotted call matches). Display order is this order.
_KINDS = (
    ("env", lambda name, _call: name == "os.environ" or name.startswith(("os.environ.", "environ.")) or name in ("os.getenv", "getenv")),
    ("input", lambda name, _call: name == "input"),
    ("writes", lambda name, call: name.endswith((".write_text", ".write_bytes")) or (name == "open" and _open_writes(call))),
    ("calls out", lambda name, _call: _head(name) in ("requests", "httpx", "urllib", "socket", "subprocess", "openai", "anthropic") or name.startswith("http.client.") or name == "http.client"),
)


def _dotted(node):
    """identifier, or object.attr, as text. Anything else, a call or a subscript, is None."""
    if node is None:
        return None
    if node.type == "identifier":
        return node.text.decode()
    if node.type == "attribute":
        left, right = _dotted(node.child_by_field_name("object")), node.child_by_field_name("attribute")
        if left is not None and right is not None:
            return f"{left}.{right.text.decode()}"
    return None


def _head(name):
    return name.split(".", 1)[0]


def _write_mode(text):
    """True when a string literal is a mode that writes. The quotes stay in the node's text."""
    if len(text) < 2 or text[0] not in "\"'" or text[-1] != text[0]:
        return False
    body = text[1:-1]
    return bool(body) and (body[0] in "wax" or "+" in body)


def _open_writes(call):
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
    return len(positional) >= 2 and positional[1].type == "string" and _write_mode(positional[1].text.decode())


def _seen(node):
    """(dotted name, call node) for each call, and dotted names that are subscripted."""
    calls, subs, todo = [], [], [node]
    while todo:
        cur = todo.pop()
        if cur.type == "call":
            name = _dotted(next(iter(cur.named_children), None))
            if name is not None:
                calls.append((name, cur))
        elif cur.type == "subscript":
            name = _dotted(cur.child_by_field_name("value"))
            if name is not None:
                subs.append(name)
        todo.extend(cur.children)
    return calls, subs


def of(node):
    """The mark names this box's own tree proves, in display order. [] when it proves none."""
    calls, subs = _seen(node)
    hits = calls + [(name, None) for name in subs]
    return [name for name, kind in _KINDS if any(kind(dotted, call) for dotted, call in hits)]
