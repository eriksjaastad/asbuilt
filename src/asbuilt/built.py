"""As-built chart: the code, read into the ELK JSON the page draws. So far one Python file as a
box, with a note when Python does not accept it: Python's own compile, never run, decides
what is valid; tree-sitter reads only UTF-8 files, and the definitions come next."""

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


def python_file(name, source):
    """The box for the Python file called `name` with these bytes, and its note if any: why
    Python does not accept them, else that they are not UTF-8 so not read, else that
    tree-sitter misreads code Python accepts."""
    note = _note(source)
    if not note:
        text, note = _text(source)
        if not note and PYTHON.parse(text).root_node.has_error:
            note = "tree-sitter could not read it all, though Python accepts it"
    return {"id": name, "labels": [{"text": t} for t in (name, note) if t]}
