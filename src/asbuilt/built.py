"""As-built chart: the code, read with tree-sitter into the ELK JSON the page draws. So far one
Python file as a box; the definitions inside it come next. A file that does not parse cleanly is
boxed with why as its note (D-012): Python's own compile, never run, decides what is valid."""

import io
import tokenize
import warnings

import tree_sitter
import tree_sitter_python

PYTHON = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))


def _utf8(source):
    """The bytes as UTF-8, decoded as Python reads source (a coding line, a BOM), so tree-sitter
    reads what Python reads; bytes Python cannot decode stay as they are, and the note says why."""
    try:
        encoding = tokenize.detect_encoding(io.BytesIO(source).readline)[0]
        return source.decode(encoding).encode()
    except (SyntaxError, UnicodeDecodeError):
        return source


def _note(source, tree):
    """Why the file does not parse cleanly, or "": Python's verdict from compiling (never
    running) the bytes, else tree-sitter's when it misreads code that Python accepts."""
    try:
        with warnings.catch_warnings():  # a warning, like a bad escape, is not an error
            warnings.simplefilter("ignore")
            compile(source, "", "exec", dont_inherit=True)
    except SyntaxError as e:  # also IndentationError, bad bytes, an unknown coding line
        return f"does not parse{f': line {e.lineno}' if e.lineno else ''} (Python: {e.msg})"
    except (MemoryError, RecursionError) as e:  # too deep for Python's own parser
        return f"does not parse (Python: {type(e).__name__}: {e})"
    return "tree-sitter could not read it all" if tree.root_node.has_error else ""


def python_file(name, source):
    """The box for the Python file called `name` with these bytes, and its note if any."""
    tree = PYTHON.parse(_utf8(source))
    labels = [{"text": t} for t in (name, _note(source, tree)) if t]
    return {"id": name, "labels": labels}
