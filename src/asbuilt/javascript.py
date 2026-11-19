"""Chart a JS/TS file or folder: python -m asbuilt.javascript PATH OUT.

Top-level functions (including component functions), classes and function-valued
variables use built.py's box format. Only unique local names and relative ES-module
imports prove calls. Nested callbacks, methods, JSX uses, CommonJS, re-exports and
configured aliases do not prove call arrows in this first slice. No code is run.
Tests, dependencies, generated folders, declaration files and symlinks are skipped.
Unreadable or malformed source fails visibly and leaves OUT unchanged.
"""

import argparse
import posixpath
import sys
from collections import Counter
from pathlib import Path

import tree_sitter
import tree_sitter_javascript
import tree_sitter_typescript

from asbuilt import output
from asbuilt.built import _SKIP_DIRS, _line, _source
from asbuilt.compare import check
from asbuilt.plan import read_bytes
from asbuilt.state import PlanError

PARSERS = {
    ".js": tree_sitter.Parser(tree_sitter.Language(tree_sitter_javascript.language())),
    ".ts": tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_typescript.language_typescript())
    ),
    ".tsx": tree_sitter.Parser(
        tree_sitter.Language(tree_sitter_typescript.language_tsx())
    ),
}
PARSERS[".jsx"] = PARSERS[".js"]
FUNCTIONS = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "generator_function",
    "arrow_function",
}
CLASSES = {"class_declaration", "abstract_class_declaration", "class"}
SKIP = _SKIP_DIRS | {"test", "__tests__", "vendor", "coverage"}


def _walk(node, nested=False):
    todo = [node] if node is not None else []
    while todo:
        cur = todo.pop()
        yield cur
        if not nested and cur.type == "method_definition":
            # Computing the key runs here; the method's parameters and body do not.
            name = cur.child_by_field_name("name")
            if name is not None and name.type == "computed_property_name":
                todo.append(name)
        elif nested or cur.type not in FUNCTIONS | CLASSES:
            todo.extend(reversed(cur.named_children))


def _word(node):
    return node.text.decode() if node is not None else ""


def _pattern(node):
    # Looking only inside a binding target avoids mistaking its value for a name.
    names, todo = [], [node] if node is not None else []
    while todo:
        cur = todo.pop()
        if cur.type in {"identifier", "shorthand_property_identifier_pattern"}:
            names.append(_word(cur))
        elif cur.type in {"required_parameter", "optional_parameter"}:
            todo.extend(
                c for c in [cur.child_by_field_name("pattern")] if c is not None
            )
        elif cur.type in {
            "assignment_pattern",
            "object_assignment_pattern",
            "pair_pattern",
        }:
            target = cur.child_by_field_name(
                "value" if cur.type == "pair_pattern" else "left"
            )
            if target is not None:
                todo.append(target)
        elif cur.type in {
            "formal_parameters",
            "array_pattern",
            "object_pattern",
            "rest_pattern",
            "parenthesized_expression",
        }:
            todo.extend(cur.named_children)
    return names


def _bindings(root):
    fields = {
        "variable_declarator": "name",
        "catch_clause": "parameter",
        "for_in_statement": "left",
    }
    names = []
    for node in _walk(root):
        if node.type in FUNCTIONS | CLASSES:
            names.append(_word(node.child_by_field_name("name")))
        elif node.type in fields:
            names.extend(_pattern(node.child_by_field_name(fields[node.type])))
    return names


def _assigned(root):
    # Any write makes that spelling unsafe as a module target, even in a nested body.
    kinds = {
        "assignment_expression": "left",
        "augmented_assignment_expression": "left",
        "update_expression": "argument",
        "for_in_statement": "left",
    }
    return {
        name
        for node in _walk(root, True)
        if node.type in kinds
        for name in _pattern(node.child_by_field_name(kinds[node.type]))
    }


def _definitions(root):
    found, exports = [], {}

    def expose(name, ident):
        exports[name] = None if name in exports else ident

    for outer in root.named_children:
        exported = outer.type == "export_statement"
        if exported and any(c.type == "type" for c in outer.children):
            continue  # `export type {Model}` never exposes a runtime binding
        default = exported and any(c.type == "default" for c in outer.children)
        node = (
            (
                outer.child_by_field_name("declaration")
                or outer.child_by_field_name("value")
            )
            if exported
            else outer
        )
        candidates = (
            node.named_children
            if node is not None
            and node.type in {"lexical_declaration", "variable_declaration"}
            else [node]
        )
        for item in candidates:
            if item is None:
                continue
            value = (
                item.child_by_field_name("value")
                if item.type == "variable_declarator"
                else item
            )
            name = item.child_by_field_name("name")
            if (
                value is not None
                and value.type in FUNCTIONS | CLASSES
                and (name is None or name.type in {"identifier", "type_identifier"})
            ):
                ident = _word(name) or ("default" if default else "")
                if ident:
                    found.append((ident, value, outer))
                    if exported:
                        expose("default" if default else ident, ident)
        if default and node is not None and node.type == "identifier":
            expose("default", _word(node))
        if exported and outer.child_by_field_name("source") is None:
            for item in _walk(outer):
                if item.type == "export_specifier" and not any(
                    c.type == "type" for c in item.children
                ):
                    expose(
                        _word(
                            item.child_by_field_name("alias")
                            or item.child_by_field_name("name")
                        ),
                        _word(item.child_by_field_name("name")),
                    )
    return found, exports


def _imports(root):
    found = []
    for stmt in root.named_children:
        if stmt.type != "import_statement" or any(
            c.type == "type" for c in stmt.children
        ):
            continue
        raw = _word(stmt.child_by_field_name("source"))
        if "\\" in raw:
            continue  # escaped specifiers need decoding; never guess their target
        bindings = {}
        clause = next(
            (n for n in stmt.named_children if n.type == "import_clause"), None
        )
        for node in _walk(clause):
            if node.type == "identifier" and node.parent == clause:
                bindings[_word(node)] = "default"
            elif node.type == "import_specifier" and not any(
                c.type == "type" for c in node.children
            ):
                bindings[
                    _word(
                        node.child_by_field_name("alias")
                        or node.child_by_field_name("name")
                    )
                ] = _word(node.child_by_field_name("name"))
            elif node.type == "namespace_import":
                bindings[_word(node.named_children[0])] = "*"
        if clause is None or bindings:
            found.append((raw[1:-1], bindings))
    return found


def _tree(suffix, source):
    parser = PARSERS[suffix]
    root = parser.parse(source).root_node
    if root.has_error and b"&" in source and suffix != ".ts":
        # These grammars require entities for JSX ampersands, although JSX allows
        # literal '&'. A provisional parse locates literals; only those bytes are
        # masked in the final parse. JS operators, expressions and source stay intact.
        hint = parser.parse(source.replace(b"&", b"|")).root_node
        if not hint.has_error:
            masked = bytearray(source)
            for node in _walk(hint, True):
                if node.type == "jsx_text" or (
                    node.type == "string" and node.parent.type == "jsx_attribute"
                ):
                    start, end = node.start_byte, node.end_byte
                    masked[start:end] = source[start:end].replace(b"&", b"|")
            root = parser.parse(bytes(masked)).root_node
    return root


def _file(name, source):
    try:
        text = source.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise PlanError(f"{name}: source is not UTF-8") from None
    root = _tree(Path(name).suffix, text.encode())
    if root.has_error or any(
        _word(n.child_by_field_name("open_tag").child_by_field_name("name"))
        != _word(n.child_by_field_name("close_tag").child_by_field_name("name"))
        for n in _walk(root, True)
        if n.type == "jsx_element"
    ):
        raise PlanError(f"{name}: JavaScript/TypeScript does not parse")
    definitions, exports = _definitions(root)
    imports = _imports(root)
    counts = Counter(
        _bindings(root) + [key for _, bindings in imports for key in bindings]
    )
    assigned = _assigned(root)
    dynamic = any(
        n.type == "with_statement"
        or n.type == "call_expression"
        and _word(n.child_by_field_name("function")) == "eval"
        for n in _walk(root, True)
    )
    if dynamic:
        assigned.update(
            counts
        )  # dynamic scope cannot prove which binding a call reaches
    children, local = [], {}
    for ident, node, outer in definitions:
        start, end = outer.start_point[0] + 1, _line(outer.end_point)
        cid = f"{name}:{ident}:{start}"
        children.append(
            {
                "id": cid,
                "labels": [{"text": ident if node.type in CLASSES else f"{ident}()"}],
                "asbuilt": _source(text, start, end),
            }
        )
        if counts[ident] <= 1 and ident not in assigned:
            local[ident] = cid
    box = {
        "id": name,
        "labels": [{"text": name}],
        **({"children": children} if children else {}),
    }
    return box, definitions, local, exports, imports, counts, assigned


def _own(path):
    return (
        path.suffix in PARSERS
        and not path.name.startswith((".", "test_"))
        and not any(s in path.name for s in (".test.", ".spec.", ".d.ts"))
        and not path.stem.endswith("_test")
        and not path.is_symlink()
    )


def _excluded_file_folder(path):
    # File input has no selected directory: Git, then the outermost package,
    # bounds its project. A dependency's own package.json cannot erase its host.
    file = path.resolve()
    parents = list(file.parents)
    root = next((p for p in parents if (p / ".git").exists()), None)
    if root is None:
        if "node_modules" in file.parts:
            return True  # a dependency's package.json is not a project boundary
        root = next(
            (p for p in reversed(parents) if (p / "package.json").is_file()),
            parents[-1],
        )
    return any(
        part in SKIP or part.startswith(".")
        for part in (root.name, *file.relative_to(root).parts[:-1])
    )


def _files(path):
    if path.is_symlink():
        raise PlanError(f"{path.name}: a symlink is not own source")
    if path.resolve().name in SKIP or path.resolve().name.startswith("."):
        raise PlanError(f"{path.name}: a test or dependency folder is not own source")
    if path.is_file():
        if not _own(path) or _excluded_file_folder(path):
            raise PlanError(
                f"{path.name}: not an own-code JavaScript/TypeScript source file"
            )
        return [(path.name, path)]
    found, todo = [], [path]
    while todo:
        for child in sorted(todo.pop().iterdir()):
            if child.is_symlink() or child.name.startswith(".") or child.name in SKIP:
                continue
            if child.is_dir():
                todo.append(child)
            elif child.is_file() and _own(child):
                found.append((child.relative_to(path).as_posix(), child))
    if not found:
        raise PlanError(
            f"{path.name}: no own-code JavaScript/TypeScript files to chart"
        )
    return sorted(found)


def _module(source, spec, files):
    if not spec.startswith(("./", "../")):
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source), spec))
    candidates = {target} if Path(target).suffix else {target + ext for ext in PARSERS}
    found = candidates & files.keys()
    return next(iter(found)) if len(found) == 1 else None


def chart(path):
    """Existing ELK plan shape, with file import arrows and proven definition calls."""
    files = {name: _file(name, read_bytes(file)) for name, file in _files(path)}
    edges = {}
    for name, (box, definitions, local, _, imports, counts, assigned) in files.items():
        calls = {}
        scope = dict(local)
        for spec, bindings in imports:
            target = _module(name, spec, files)
            if target is None or target == name:
                continue
            key = f"{name}>import:{target}"
            edges[key] = {
                "id": key,
                "sources": [name],
                "targets": [target],
                "labels": [{"text": "imports"}],
            }
            for ident, exported in bindings.items():
                actual = files[target][3].get(exported)
                if (
                    counts[ident] == 1
                    and ident not in assigned
                    and actual in files[target][2]
                ):
                    scope[ident] = files[target][2][actual]
        for ident, node, outer in definitions:
            if node.type in CLASSES:
                continue
            body = node.child_by_field_name("body")
            hidden = set(
                _bindings(body)
                + _pattern(
                    node.child_by_field_name("parameters")
                    or node.child_by_field_name("parameter")
                )
            ) | _assigned(body)
            for call in _walk(body):
                field = {
                    "call_expression": "function",
                    "new_expression": "constructor",
                }.get(call.type)
                callee = call.child_by_field_name(field) if field else None
                target = (
                    scope.get(_word(callee))
                    if callee is not None
                    and callee.type == "identifier"
                    and _word(callee) not in hidden
                    else None
                )
                if target:
                    source = f"{name}:{ident}:{outer.start_point[0] + 1}"
                    key = f"{source}>{target}"
                    calls[key] = {"id": key, "sources": [source], "targets": [target]}
        if calls:
            box["edges"] = list(calls.values())
    boxes = [data[0] for data in files.values()]
    if path.is_dir():
        boxes = [
            {
                "id": path.resolve().name,
                "labels": [{"text": path.resolve().name}],
                "children": boxes,
            }
        ]
    return {
        "id": "asbuilt:built",
        "children": boxes,
        **({"edges": list(edges.values())} if edges else {}),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        target = args.out.resolve()
        if target.suffix.lower() != ".json" or (
            target.exists() and any(target.samefile(p) for _, p in _files(args.path))
        ):
            raise PlanError(
                f"{args.out.name}: OUT must be JSON and must not replace source"
            )
        result = chart(args.path)
        check(result, args.out.name)
        output.write_atomic(args.out, output.plan_bytes(result, args.out.name))
    except (PlanError, OSError) as exc:
        return output.fail("asbuilt.javascript", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
