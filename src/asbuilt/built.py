"""As-built chart: the code, read into the ELK JSON the page draws. So far one Python file as a
box, with a note when Python does not accept it (D-012): Python's own compile, never run, decides
what is valid. The definitions inside, read with tree-sitter (PRD), come next."""

import warnings


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


def python_file(name, source):
    """The box for the Python file called `name` with these bytes, and its note if any."""
    return {"id": name, "labels": [{"text": t} for t in (name, _note(source)) if t]}
