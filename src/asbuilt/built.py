"""As-built chart: the code, read into the ELK JSON the page draws. One Python file is a box.
When Python accepts it and tree-sitter can read it, each top-level function and class is a box
inside it, with the lines it occupies. A note says when Python does not accept the file, when it
is not UTF-8, or when tree-sitter misreads code Python accepts. Python's own compile, never run,
decides what is valid; tree-sitter reads only UTF-8 files."""

import tokenize
import warnings

import tree_sitter
import tree_sitter_python

PYTHON = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))


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
        found.append((name.text.decode(), node.type == "class_definition", start, _line(outer.end_point)))
    return found


def python_file(name, source):
    """The box for the Python file called `name` with these bytes, and its note if any: why
    Python does not accept them, else that they are not UTF-8 so not read, else that
    tree-sitter misreads code Python accepts. A file that reads cleanly holds a child box for
    each top-level function and class."""
    note = _note(source)
    definitions = []
    if not note:
        text, note = _text(source)
        if not note:
            root = PYTHON.parse(text).root_node
            if root.has_error:
                note = "tree-sitter could not read it all, though Python accepts it"
            else:
                definitions = _definitions(root)
    box = {"id": name, "labels": [{"text": t} for t in (name, note) if t]}
    if definitions:
        box["children"] = [
            {
                "id": f"{name}:{ident}:{start}",
                "labels": [{"text": ident if is_class else f"{ident}()"}],
                "asbuilt": {"lines": [start, end]},
            }
            for ident, is_class, start, end in definitions
        ]
    return box
