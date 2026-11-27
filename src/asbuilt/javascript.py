"""Chart a JS/TS file or folder: python -m asbuilt.javascript PATH OUT.

Top-level functions (including component functions), classes and function-valued
variables use built.py's box format. Unique local names, relative ES-module
imports, static CommonJS requires, unambiguous directory index files, and
tsconfig/jsconfig path aliases prove calls. Nested callbacks, methods, JSX uses,
dynamic requires, and re-exports do not prove call arrows. No code is run.
Tests, dependencies, generated folders, declaration files and symlinks are skipped.
In a folder chart, unreadable or unparseable files are skipped and listed on stderr
unless none remain; a single unparseable file fails visibly and leaves OUT unchanged.
"""

import argparse
import json
import posixpath
import re
import sys
from collections import Counter
from pathlib import Path

import tree_sitter
import tree_sitter_javascript
import tree_sitter_typescript

from asbuilt import output
from asbuilt.built import PASSES, _SKIP_DIRS, _line, _source
from asbuilt.compare import check
from asbuilt.marks import _ORDER as _MARK_ORDER
from asbuilt.plan import read_bytes
from asbuilt.security import host_name
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
PARSERS[".mjs"] = PARSERS[".cjs"] = PARSERS[".js"]
PARSERS[".mts"] = PARSERS[".cts"] = PARSERS[".ts"]
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


def _is_module_exports(node):
    return (
        node is not None
        and node.type == "member_expression"
        and _word(node.child_by_field_name("object")) == "module"
        and _word(node.child_by_field_name("property")) == "exports"
    )


def _cjs_stable(root):
    # Node's exports are whatever module.exports holds when loading ends. Only plain
    # top-level writes are followed; a shadowed, aliased, deleted, nested or
    # computed write could change that object, so the file proves no export.
    names = set(_assigned(root))
    for node in _walk(root, True):
        if node.type in FUNCTIONS | CLASSES | {"method_definition"}:
            names.add(_word(node.child_by_field_name("name")))
            names.update(
                _pattern(
                    node.child_by_field_name("parameters")
                    or node.child_by_field_name("parameter")
                )
            )
        elif node.type == "variable_declarator":
            names.update(_pattern(node.child_by_field_name("name")))
        elif node.type == "catch_clause":
            names.update(_pattern(node.child_by_field_name("parameter")))
        elif node.type == "for_in_statement":
            names.update(_pattern(node.child_by_field_name("left")))
    if names & {"module", "exports"}:
        return False
    for base in _walk(root, True):
        if not (
            (base.type == "identifier" and _word(base) == "exports")
            or _is_module_exports(base)
        ):
            continue
        top = base
        while (
            top.parent is not None
            and top.parent.type in {"member_expression", "subscript_expression"}
            and top.parent.child_by_field_name("object") == top
        ):
            top = top.parent
        parent = top.parent
        if parent is None:
            continue
        if parent.type == "unary_expression" and _word(
            parent.child_by_field_name("operator")
        ) == "delete":
            return False
        target = {
            "assignment_expression": "left",
            "augmented_assignment_expression": "left",
            "update_expression": "argument",
        }.get(parent.type)
        if target and parent.child_by_field_name(target) == top:
            plain = (
                parent.type == "assignment_expression"
                and parent.parent.type == "expression_statement"
                and parent.parent.parent == root
                and (
                    (top == base and base.type == "member_expression")
                    or (
                        top.type == "member_expression"
                        and top.child_by_field_name("object") == base
                        and top.child_by_field_name("property").type
                        == "property_identifier"
                    )
                )
            )
            if not plain:
                return False
        elif top == base:
            return False  # the object itself escapes (aliased, passed, chained)
    return True


def _cjs_export(expr, outer, found, cjs):
    # cjs["names"] maps export name -> definition key; replacing module.exports
    # discards earlier properties, and after it exports.x no longer reaches it.
    # A "default" property is skipped: Node and Babel interop disagree on it.
    names = cjs["names"]

    def put(name, key):
        names[name] = None if name in names else key

    left = expr.child_by_field_name("left")
    right = expr.child_by_field_name("right")
    if left is None or right is None or left.type != "member_expression":
        return
    if _is_module_exports(left):
        names.clear()
        cjs["replaced"] = True
        if right.type == "identifier":
            put("module.exports", _word(right))
            put("default", _word(right))
        elif right.type in FUNCTIONS | CLASSES:
            name = right.child_by_field_name("name")
            label = (
                _word(name)
                if name is not None
                and name.type in {"identifier", "type_identifier"}
                else "default"
            )
            found.append((label, right, outer, "module.exports"))
            put("module.exports", "module.exports")
            put("default", "module.exports")
        elif right.type == "object":
            for prop in right.named_children:
                if prop.type == "shorthand_property_identifier":
                    ident = _word(prop)
                    put(ident, ident)
                elif prop.type == "pair":
                    key = prop.child_by_field_name("key")
                    val = prop.child_by_field_name("value")
                    if (
                        key is not None
                        and key.type == "property_identifier"
                        and val is not None
                    ):
                        k = _word(key)
                        if val.type == "identifier":
                            put(k, _word(val))
                        elif val.type in FUNCTIONS | CLASSES:
                            found.append((k, val, prop, f"exports.{k}"))
                            put(k, f"exports.{k}")
        return
    obj = left.child_by_field_name("object")
    prop = left.child_by_field_name("property")
    via_exports = obj is not None and obj.type == "identifier" and _word(obj) == "exports"
    if (
        prop is not None
        and prop.type == "property_identifier"
        and (via_exports or _is_module_exports(obj))
    ):
        export_name = _word(prop)
        live = not (via_exports and cjs["replaced"]) and export_name != "default"
        if right.type == "identifier":
            if live:
                put(export_name, _word(right))
        elif right.type in FUNCTIONS | CLASSES:
            key = f"exports.{export_name}"
            found.append((export_name, right, outer, key))
            if live:
                put(export_name, key)


_WRAPPERS = frozenset(
    {
        ("memo",),
        ("React", "memo"),
        ("forwardRef",),
        ("React", "forwardRef"),
        ("defineComponent",),
        ("Vue", "defineComponent"),
    }
)


def _unwrap_expr(node):
    while node is not None and node.type in {
        "parenthesized_expression",
        "non_null_expression",
        "as_expression",
        "satisfies_expression",
    }:
        node = node.named_children[0] if node.named_children else None
    return node


def _wrapper_callee(node):
    attrs = []
    cur = _unwrap_expr(node)
    while cur is not None and cur.type == "member_expression":
        prop = cur.child_by_field_name("property")
        if prop is None or prop.type != "property_identifier":
            return None
        attrs.append(_word(prop))
        cur = _unwrap_expr(cur.child_by_field_name("object"))
    if cur is None or cur.type != "identifier":
        return None
    attrs.reverse()
    return (_word(cur), *attrs)


def _unwrap_component(node):
    node = _unwrap_expr(node)
    if node is None:
        return None, False
    if node.type in FUNCTIONS | CLASSES:
        return node, False
    depth = 0
    cur = node
    while cur is not None and cur.type == "call_expression":
        if _wrapper_callee(cur.child_by_field_name("function")) not in _WRAPPERS:
            return None, False
        args = cur.child_by_field_name("arguments")
        if args is None or not (1 <= len(args.named_children) <= 2):
            return None, False
        cur = _unwrap_expr(args.named_children[0])
        depth += 1
    if depth and cur is not None and (cur.type in FUNCTIONS or cur.type == "identifier"):
        return cur, True
    return None, False


def _default_stem(file_name):
    if not file_name:
        return "default"
    stem = Path(file_name).stem
    if (
        not stem
        or stem == "index"
        or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", stem)
    ):
        return "default"
    return stem


def _definitions(root, file_name=""):
    # Each entry is (label, node, outer, key). The key is the module-scope name a
    # bare call can reach; a CommonJS property or an anonymous default has none, so
    # its key holds a dot and only an import through exports can reach it.
    found, exports = [], {}
    anon_defaults = []
    wrapper_refs = []
    cjs = {"names": {}, "replaced": False}

    def expose(name, ident):
        exports[name] = None if name in exports else ident

    for outer in root.named_children:
        if outer.type == "expression_statement" and len(outer.named_children) == 1:
            expr = outer.named_children[0]
            if expr.type == "assignment_expression":
                _cjs_export(expr, outer, found, cjs)
                continue
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
            raw_value = (
                item.child_by_field_name("value")
                if item.type == "variable_declarator"
                else item
            )
            name = item.child_by_field_name("name")
            if name is not None and name.type not in {"identifier", "type_identifier"}:
                continue
            target, wrapped = _unwrap_component(raw_value)
            if target is None:
                continue
            raw_name = (
                _word(name)
                if not wrapped or item.type == "variable_declarator"
                else ""
            )
            if target.type == "identifier":
                if raw_name:
                    wrapper_refs.append((len(found), raw_name, _word(target), outer))
                    found.append(None)
                    if exported:
                        expose("default" if default else raw_name, raw_name)
                elif default:
                    idx = len(found)
                    found.append(None)
                    anon_defaults.append((idx, None, outer, _word(target)))
            else:
                if raw_name:
                    found.append((raw_name, target, outer, raw_name))
                    if exported:
                        expose("default" if default else raw_name, raw_name)
                elif default:
                    idx = len(found)
                    found.append(None)
                    anon_defaults.append((idx, target, outer, None))
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
    if _cjs_stable(root):
        for name, key in cjs["names"].items():
            expose(name, key)
    fn_by_ident = {}
    fn_counts = Counter()
    for entry in found:
        if entry is not None:
            _, target_node, _, key = entry
            if target_node.type in FUNCTIONS:
                fn_counts[key] += 1
                fn_by_ident[key] = target_node
    for idx, ident, ref_name, outer in wrapper_refs:
        if fn_counts[ref_name] == 1:
            found[idx] = (ident, fn_by_ident[ref_name], outer, ident)
        else:
            for exp_name, exp_target in list(exports.items()):
                if exp_target == ident:
                    exports.pop(exp_name, None)
    if len(anon_defaults) == 1:
        idx, target_node, outer, ref_name = anon_defaults[0]
        if ref_name is not None:
            target_node = fn_by_ident.get(ref_name) if fn_counts[ref_name] == 1 else None
        if target_node is not None:
            taken = {e[0] for e in found if e is not None} | set(_bindings(root)) | _assigned(root)
            stem = _default_stem(file_name)
            ident = stem if stem not in taken else "default"
            found[idx] = (ident, target_node, outer, "export.default")
            expose("default", "export.default")
    elif len(anon_defaults) > 1:
        for idx, target_node, outer, ref_name in anon_defaults:
            if ref_name is not None:
                target_node = fn_by_ident.get(ref_name) if fn_counts[ref_name] == 1 else None
            if target_node is not None:
                found[idx] = ("default", target_node, outer, "export.default")
                expose("default", "export.default")
    return [e for e in found if e is not None], exports


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


def _require_spec(node):
    if node is None or node.type != "call_expression":
        return None
    func = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if (
        func is None
        or func.type != "identifier"
        or _word(func) != "require"
        or args is None
        or len(args.named_children) != 1
    ):
        return None
    arg = args.named_children[0]
    if arg.type != "string":
        return None
    raw = _word(arg)
    if "\\" in raw or len(raw) < 2:
        return None
    return raw[1:-1]


def _cjs_imports(root):
    found = []
    for stmt in root.named_children:
        if stmt.type == "expression_statement" and len(stmt.named_children) == 1:
            spec = _require_spec(stmt.named_children[0])
            if spec is not None:
                found.append((spec, {}))
        elif stmt.type in {"lexical_declaration", "variable_declaration"}:
            for decl in stmt.named_children:
                if decl.type != "variable_declarator":
                    continue
                spec = _require_spec(decl.child_by_field_name("value"))
                if spec is None:
                    continue
                name = decl.child_by_field_name("name")
                bindings = {}
                if name is not None and name.type == "identifier":
                    # require() returns module.exports itself, never an ES default.
                    bindings[_word(name)] = "module.exports"
                elif name is not None and name.type == "object_pattern":
                    for item in name.named_children:
                        if item.type == "shorthand_property_identifier_pattern":
                            ident = _word(item)
                            bindings[ident] = ident
                        elif item.type == "pair_pattern":
                            key = item.child_by_field_name("key")
                            val = item.child_by_field_name("value")
                            if (
                                key is not None
                                and key.type == "property_identifier"
                                and val is not None
                                and val.type == "identifier"
                            ):
                                bindings[_word(val)] = _word(key)
                found.append((spec, bindings))
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


_KNOWN_MODULES = {
    "process": "process",
    "node:process": "process",
    "readline": "readline",
    "node:readline": "readline",
    "readline/promises": "readline",
    "node:readline/promises": "readline",
    "fs": "fs",
    "node:fs": "fs",
    "fs/promises": "fs/promises",
    "node:fs/promises": "fs/promises",
    "http": "http",
    "node:http": "http",
    "https": "https",
    "node:https": "https",
    "axios": "axios",
    "child_process": "child_process",
    "node:child_process": "child_process",
    "openai": "openai",
    "@anthropic-ai/sdk": "@anthropic-ai/sdk",
    "@google/genai": "@google/genai",
    "@google/generative-ai": "@google/generative-ai",
    "ai": "ai",
}
_FS_WRITES = frozenset(
    {
        "writeFile",
        "writeFileSync",
        "appendFile",
        "appendFileSync",
        "createWriteStream",
        "mkdir",
        "mkdirSync",
        "rm",
        "rmSync",
        "unlink",
        "unlinkSync",
        "rename",
        "renameSync",
        "copyFile",
        "copyFileSync",
    }
)
_HTTP_CALLS = frozenset(
    {
        ("http", "request"),
        ("http", "get"),
        ("https", "request"),
        ("https", "get"),
    }
)
# Only these send a request; helpers such as isAxiosError, getUri or create do not.
_AXIOS_REQUESTS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request"}
)


def _axios_request(attrs):
    # attrs follow the axios module or an axios.create() client: axios(...) or a verb.
    return attrs == () or (len(attrs) == 1 and attrs[0] in _AXIOS_REQUESTS)


_CHILD_PROCESS_CALLS = frozenset(
    {"exec", "execSync", "spawn", "spawnSync", "execFile", "execFileSync"}
)
_MODEL_CONSTRUCTORS = frozenset(
    {
        ("openai",),
        ("openai", "OpenAI"),
        ("openai", "AzureOpenAI"),
        ("@anthropic-ai/sdk",),
        ("@anthropic-ai/sdk", "Anthropic"),
        ("@google/genai",),
        ("@google/genai", "GoogleGenAI"),
        ("@google/generative-ai",),
        ("@google/generative-ai", "GoogleGenerativeAI"),
        ("@google/generative-ai", "GoogleGenAI"),
    }
)
_MODEL_CALLS = frozenset({("ai", "generateText"), ("ai", "streamText")})
_TYPE_NODES = frozenset(
    {
        "type_annotation",
        "type_arguments",
        "type_parameters",
        "interface_declaration",
        "type_alias_declaration",
    }
)


def _unwrap(node):
    while node is not None and node.type in {
        "parenthesized_expression",
        "non_null_expression",
    }:
        node = node.named_children[0] if node.named_children else None
    return node


def _member_chain(node):
    attrs = []
    cur = _unwrap(node)
    while cur is not None and cur.type == "member_expression":
        prop = cur.child_by_field_name("property")
        if prop is None or prop.type != "property_identifier":
            return None, ()
        attrs.append(_word(prop))
        cur = _unwrap(cur.child_by_field_name("object"))
    attrs.reverse()
    return cur, tuple(attrs)


def _resolve_mark_path(node, scope, dynamic):
    root, attrs = _member_chain(node)
    if root is None:
        return None
    if root.type == "meta_property" and _word(root) == "import.meta":
        return ("import.meta", *attrs)
    if root.type != "identifier" or dynamic:
        return None
    name = _word(root)
    if name not in scope:
        return ("global", name, *attrs)
    binding = scope[name]
    if binding is None:
        return None
    if binding[0] == "client":
        return ("client", binding[1], *attrs)
    _, mod, exported = binding
    if exported in ("default", "*", "module.exports"):
        return ("module", mod, *attrs)
    return ("module", mod, exported, *attrs)


def _client_kind(expr, scope, dynamic):
    expr = _unwrap(expr)
    if expr is None:
        return None
    if expr.type == "new_expression":
        path = _resolve_mark_path(
            expr.child_by_field_name("constructor"), scope, dynamic
        )
        if path is not None and path[0] == "module" and path[1:] in _MODEL_CONSTRUCTORS:
            return "model"
        if path == ("global", "XMLHttpRequest"):
            return "xhr"
    elif expr.type == "call_expression":
        path = _resolve_mark_path(expr.child_by_field_name("function"), scope, dynamic)
        if path is not None:
            if path[0] == "module" and path[1:] == ("axios", "create"):
                return "axios"
            if path[0] == "client" and path[1] == "model":
                return "model"
    return None


def _mark_scope(root, imports, counts, assigned, dynamic):
    scope = dict.fromkeys(set(counts) | set(assigned), None)
    for spec, bindings in imports:
        mod = _KNOWN_MODULES.get(spec)
        if mod is None:
            continue
        for ident, exported in bindings.items():
            if counts[ident] == 1 and ident not in assigned:
                scope[ident] = ("import", mod, exported)
    if dynamic:
        return scope
    for outer in root.named_children:
        node = (
            outer.child_by_field_name("declaration")
            if outer.type == "export_statement"
            else outer
        )
        if node is None or node.type not in {
            "lexical_declaration",
            "variable_declaration",
        }:
            continue
        for decl in node.named_children:
            if decl.type != "variable_declarator":
                continue
            name = decl.child_by_field_name("name")
            value = decl.child_by_field_name("value")
            if name is None or name.type != "identifier" or value is None:
                continue
            ident = _word(name)
            if counts[ident] == 1 and ident not in assigned:
                kind = _client_kind(value, scope, dynamic)
                if kind is not None:
                    scope[ident] = ("client", kind)
    return scope


def _hoisted_bindings(body):
    names = []
    todo = [body] if body is not None else []
    while todo:
        cur = todo.pop()
        if cur.type in FUNCTIONS | CLASSES:
            if cur.type in {"function_declaration", "generator_function_declaration"}:
                name = cur.child_by_field_name("name")
                if name is not None:
                    names.append(_word(name))
            continue
        if cur.type == "variable_declaration":
            for decl in cur.named_children:
                if decl.type == "variable_declarator":
                    names.extend(_pattern(decl.child_by_field_name("name")))
        todo.extend(reversed(cur.named_children))
    return names


def _block_statements(block):
    # A switch body is one lexical scope; its declarations sit inside the cases.
    if block.type != "switch_body":
        return list(block.named_children)
    return [
        child
        for case in block.named_children
        for i, child in enumerate(case.children)
        if case.field_name_for_child(i) == "body"
    ]


def _lexical_names(stmts):
    names = []
    for stmt in stmts:
        if stmt.type == "lexical_declaration":
            for decl in stmt.named_children:
                if decl.type == "variable_declarator":
                    names.extend(_pattern(decl.child_by_field_name("name")))
        elif stmt.type in FUNCTIONS | CLASSES and stmt.type.endswith("declaration"):
            names.append(_word(stmt.child_by_field_name("name")))
    return names


def _free_writes(root):
    """Names written under root that no scope nested inside root declares.

    A write to a parameter or local of an inner function or block cannot change
    the binding root sees, so it must not hide that binding's work marks.
    """
    out = set()
    kinds = {
        "assignment_expression": "left",
        "augmented_assignment_expression": "left",
        "update_expression": "argument",
    }

    todo = [(c, frozenset()) for c in (root.named_children if root else ())]
    while todo:
        cur, bound = todo.pop()
        kind = cur.type
        if kind in FUNCTIONS or kind == "method_definition":
            params = cur.child_by_field_name("parameters") or cur.child_by_field_name(
                "parameter"
            )
            body = cur.child_by_field_name("body")
            inner = bound | set(_pattern(params)) | set(_hoisted_bindings(body))
            if kind in {"function_expression", "generator_function"}:
                inner |= {_word(cur.child_by_field_name("name"))}
            name = cur.child_by_field_name("name")
            if kind == "method_definition" and name is not None:
                if name.type == "computed_property_name":
                    todo.append((name, bound))
            todo.extend((c, inner) for c in (params, body) if c is not None)
            continue
        if kind in {"statement_block", "switch_body", "class_body"}:
            bound = bound | set(_lexical_names(_block_statements(cur)))
        elif kind in {"for_statement", "for_in_statement"}:
            head = cur.child_by_field_name(
                "initializer" if kind == "for_statement" else "left"
            )
            if kind == "for_statement" and head is not None:
                bound = bound | set(_lexical_names([head]))
            elif _word(cur.child_by_field_name("kind")) in {"let", "const"}:
                bound = bound | set(_pattern(head))
            elif cur.child_by_field_name("kind") is None:
                out.update(n for n in _pattern(head) if n not in bound)
        elif kind == "catch_clause":
            bound = bound | set(_pattern(cur.child_by_field_name("parameter")))
        elif kind in kinds:
            target = cur.child_by_field_name(kinds[kind])
            out.update(n for n in _pattern(target) if n not in bound)
        todo.extend((c, bound) for c in cur.named_children)
    return out


def _on_mark_call(node, scope, dynamic, add):
    path = _resolve_mark_path(node.child_by_field_name("function"), scope, dynamic)
    if path is None:
        return
    if path[0] == "global":
        rest = path[1:]
        if rest == ("Deno", "env", "get"):
            add("env")
        elif rest == ("prompt",):
            add("input")
        elif rest in {("localStorage", "setItem"), ("sessionStorage", "setItem")}:
            add("writes")
        elif rest == ("fetch",):
            add("calls out")
        return
    if path[0] == "client":
        kind, attrs = path[1], path[2:]
        if kind == "model":
            add("model")
        elif (kind == "axios" and _axios_request(attrs)) or (
            kind == "xhr" and attrs == ("send",)
        ):
            add("calls out")
        return
    if path[0] != "module":
        return
    mod_path = path[1:]
    if mod_path == ("readline", "createInterface"):
        add("input")
    elif mod_path[0] == "fs" and (
        (len(mod_path) == 2 and mod_path[1] in _FS_WRITES)
        or (len(mod_path) == 3 and mod_path[1] == "promises" and mod_path[2] in _FS_WRITES)
    ):
        add("writes")
    elif mod_path[0] == "fs/promises" and len(mod_path) == 2 and mod_path[1] in _FS_WRITES:
        add("writes")
    elif mod_path in _HTTP_CALLS or (
        mod_path[0] == "axios" and _axios_request(mod_path[1:])
    ):
        add("calls out")
    elif (
        mod_path[0] == "child_process"
        and len(mod_path) == 2
        and mod_path[1] in _CHILD_PROCESS_CALLS
    ):
        add("calls out")
    elif mod_path in _MODEL_CALLS:
        add("model")


def _on_mark_new(node, scope, dynamic, add):
    path = _resolve_mark_path(node.child_by_field_name("constructor"), scope, dynamic)
    if path is None:
        return
    if path == ("global", "WebSocket"):
        add("calls out")  # constructing a WebSocket opens the connection
    elif path[0] == "module" and path[1:] in _MODEL_CONSTRUCTORS:
        add("model")


def _on_mark_assign(node, scope, dynamic, add):
    path = _resolve_mark_path(node.child_by_field_name("left"), scope, dynamic)
    if path == ("global", "document", "cookie"):
        add("writes")


def _on_mark_ref(node, scope, dynamic, add, details=None):
    path = _resolve_mark_path(node, scope, dynamic)
    if path in {
        ("global", "process", "env"),
        ("module", "process", "env"),
        ("import.meta", "env"),
    }:
        add("env")
    elif path in {
        ("global", "process", "argv"),
        ("module", "process", "argv"),
        ("global", "process", "stdin"),
        ("module", "process", "stdin"),
    }:
        add("input")
        if details is not None and path[-1] == "argv":
            details.add("argv")


def _js_calls(node):
    count, todo = 0, [node] if node is not None else []
    while todo:
        cur = todo.pop()
        if cur.type in _TYPE_NODES:
            continue
        if cur.type in {"call_expression", "new_expression"}:
            count += 1
        todo.extend(cur.named_children)
    return count


def _unwrap_return_expr(node):
    while node is not None and node.type in {
        "parenthesized_expression",
        "await_expression",
        "non_null_expression",
        "as_expression",
        "satisfies_expression",
    }:
        node = node.named_children[0] if node.named_children else None
    return node


def _js_passes(node):
    if node is None or node.type not in FUNCTIONS:
        return False
    body = node.child_by_field_name("body")
    if body is None:
        return False
    if body.type == "statement_block":
        stmts = [c for c in body.named_children if c.type != "comment"]
        if len(stmts) != 1 or stmts[0].type != "return_statement":
            return False
        raw = next(
            (c for c in stmts[0].named_children if c.type != "comment"), None
        )
    else:
        raw = body
    expr = _unwrap_return_expr(raw)
    return (
        expr is not None
        and expr.type == "call_expression"
        and _js_calls(body) == 1
    )


def _js_string_literal(node):
    node = _unwrap_expr(node)
    if node is None or node.type != "string":
        return None
    raw = _word(node)
    if len(raw) < 2 or "\\" in raw:
        return None
    return raw[1:-1] or None


def _env_read_info(node, scope, dynamic):
    if node.type == "member_expression":
        prop = node.child_by_field_name("property")
        if prop is None or prop.type != "property_identifier":
            return None
        key = _word(prop)
        if not key:
            return None
        base = _resolve_mark_path(node.child_by_field_name("object"), scope, dynamic)
        if base in {
            ("global", "process", "env"),
            ("module", "process", "env"),
            ("import.meta", "env"),
        }:
            return key, _word(node)
        return None
    if node.type == "subscript_expression":
        key = _js_string_literal(node.child_by_field_name("index"))
        if not key:
            return None
        base = _resolve_mark_path(node.child_by_field_name("object"), scope, dynamic)
        if base in {
            ("global", "process", "env"),
            ("module", "process", "env"),
            ("import.meta", "env"),
        }:
            return key, _word(node)
        return None
    if node.type == "call_expression":
        path = _resolve_mark_path(node.child_by_field_name("function"), scope, dynamic)
        if path == ("global", "Deno", "env", "get"):
            args = node.child_by_field_name("arguments")
            if args is not None and len(args.named_children) >= 1:
                key = _js_string_literal(args.named_children[0])
                if key:
                    return key, _word(node)
    return None


def _call_security_info(node, scope, dynamic):
    callee = node.child_by_field_name("function")
    path = _resolve_mark_path(callee, scope, dynamic)
    if path is None:
        return None
    callee_text = _word(_unwrap_expr(callee))
    args = node.child_by_field_name("arguments")
    named_args = args.named_children if args is not None else ()
    first_str = _js_string_literal(named_args[0]) if named_args else None

    if (
        (path == ("global", "fetch"))
        or (path[0] == "module" and path[1] == "axios" and _axios_request(path[2:]))
        or (path[0] == "module" and path[1:] in _HTTP_CALLS)
    ):
        host = host_name(first_str) if first_str else None
        if host:
            return (
                ("net", host),
                {
                    "fact": {"kind": "net", "how": "http", "host": host},
                    "plain": f"This connects to {host} over the network.",
                    "expert": f"Connects to {host} via {callee_text}.",
                },
                f"Talks to {host} over the network.",
            )
        return None

    if path[0] == "module":
        mod_path = path[1:]
        if (
            mod_path[0] == "child_process"
            and len(mod_path) == 2
            and mod_path[1] in _CHILD_PROCESS_CALLS
        ):
            return (
                ("proc", callee_text),
                {
                    "fact": {"kind": "proc", "how": "run"},
                    "plain": "This runs another program on the computer.",
                    "expert": f"Runs a subprocess via {callee_text}.",
                },
                "Runs another program on the computer.",
            )
        if (
            mod_path[0] == "fs"
            and (
                (len(mod_path) == 2 and mod_path[1] in _FS_WRITES)
                or (
                    len(mod_path) == 3
                    and mod_path[1] == "promises"
                    and mod_path[2] in _FS_WRITES
                )
            )
        ) or (
            mod_path[0] == "fs/promises"
            and len(mod_path) == 2
            and mod_path[1] in _FS_WRITES
        ):
            return (
                ("file", callee_text),
                {
                    "fact": {"kind": "file", "how": "write"},
                    "plain": "This writes or changes files on disk.",
                    "expert": f"Writes to disk via {callee_text}.",
                },
                "Writes or changes files on disk.",
            )
    return None


def _throw_literal_message(node):
    if node.type != "throw_statement" or not node.named_children:
        return None
    expr = _unwrap_expr(node.named_children[0])
    if expr is None or expr.type not in {"new_expression", "call_expression"}:
        return None
    field = "constructor" if expr.type == "new_expression" else "function"
    callee = _unwrap_expr(expr.child_by_field_name(field))
    if callee is None or callee.type != "identifier":
        return None
    cname = _word(callee)
    if cname != "Error" and not cname.endswith("Error"):
        return None
    args = expr.child_by_field_name("arguments")
    if args is None or not args.named_children:
        return None
    return _js_string_literal(args.named_children[0])


def _throw_caught(node, top):
    cur = node.parent
    while cur is not None and cur != top:
        if cur.type == "try_statement":
            body = cur.child_by_field_name("body")
            handler = cur.child_by_field_name("handler")
            if (
                body is not None
                and handler is not None
                and body.start_byte <= node.start_byte
                and node.end_byte <= body.end_byte
            ):
                return True
        cur = cur.parent
    return False


def _marks(node, module_scope, dynamic, details=None, security_out=None, plain_out=None):
    found = set()
    add = found.add
    seen_sec = set()
    seen_plain = set()
    throws = []

    def add_sec(dedup_key, entry):
        if security_out is not None and dedup_key not in seen_sec:
            seen_sec.add(dedup_key)
            security_out.append(entry)

    def add_plain(line):
        if plain_out is not None and line and line not in seen_plain:
            seen_plain.add(line)
            plain_out.append(line)

    def record_env(cur, scope):
        info = _env_read_info(cur, scope, dynamic)
        if info is not None:
            key, expr_text = info
            add_sec(
                ("env", key),
                {
                    "fact": {"kind": "env", "how": "read", "name": key},
                    "plain": f"This reads {key} from the environment.",
                    "expert": f"{expr_text} reads {key} from the environment.",
                },
            )
            add_plain(f"Reads {key} from the environment.")

    def record_call_sec(cur, scope):
        info = _call_security_info(cur, scope, dynamic)
        if info is not None:
            dedup_key, sec_entry, plain_line = info
            add_sec(dedup_key, sec_entry)
            add_plain(plain_line)

    def visit_block(block, scope):
        block_scope = dict(scope)
        decl_counts = Counter()
        stmts = _block_statements(block)
        for stmt in stmts:
            if stmt.type == "lexical_declaration":
                for decl in stmt.named_children:
                    if decl.type == "variable_declarator":
                        for ident in _pattern(decl.child_by_field_name("name")):
                            decl_counts[ident] += 1
                            block_scope[ident] = None
            elif stmt.type == "variable_declaration":
                for decl in stmt.named_children:
                    if decl.type == "variable_declarator":
                        for ident in _pattern(decl.child_by_field_name("name")):
                            decl_counts[ident] += 1
            elif stmt.type in {
                "class_declaration",
                "abstract_class_declaration",
                "function_declaration",
                "generator_function_declaration",
            }:
                name = stmt.child_by_field_name("name")
                if name is not None:
                    block_scope[_word(name)] = None
        block_assigned = _free_writes(block)
        if block.type == "switch_body":
            for case in block.named_children:
                visit(case.child_by_field_name("value"), block_scope)
        for stmt in stmts:
            if stmt.type in {"lexical_declaration", "variable_declaration"}:
                for decl in stmt.named_children:
                    if decl.type != "variable_declarator":
                        visit(decl, block_scope)
                        continue
                    name_node = decl.child_by_field_name("name")
                    val_node = decl.child_by_field_name("value")
                    visit(name_node, block_scope)
                    visit(val_node, block_scope)
                    if (
                        name_node is not None
                        and name_node.type == "identifier"
                        and val_node is not None
                    ):
                        ident = _word(name_node)
                        if decl_counts[ident] == 1 and ident not in block_assigned:
                            kind = _client_kind(val_node, block_scope, dynamic)
                            if kind is not None:
                                block_scope[ident] = ("client", kind)
            else:
                visit(stmt, block_scope)

    def visit(cur, scope):
        if cur is None or cur.type in _TYPE_NODES:
            return
        kind = cur.type
        if kind in FUNCTIONS:
            fn_scope = dict(scope)
            if kind in {"function_expression", "generator_function"}:
                name = cur.child_by_field_name("name")
                if name is not None:
                    fn_scope[_word(name)] = None
            params = cur.child_by_field_name("parameters") or cur.child_by_field_name(
                "parameter"
            )
            for ident in _pattern(params):
                fn_scope[ident] = None
            body = cur.child_by_field_name("body")
            for ident in _hoisted_bindings(body):
                fn_scope[ident] = None
            visit(params, fn_scope)
            visit(body, fn_scope)
            return
        if kind == "method_definition":
            name = cur.child_by_field_name("name")
            if name is not None and name.type == "computed_property_name":
                visit(name, scope)
            method_scope = dict(scope)
            params = cur.child_by_field_name("parameters")
            for ident in _pattern(params):
                method_scope[ident] = None
            body = cur.child_by_field_name("body")
            for ident in _hoisted_bindings(body):
                method_scope[ident] = None
            visit(params, method_scope)
            visit(body, method_scope)
            return
        if kind in CLASSES:
            class_scope = dict(scope)
            name = cur.child_by_field_name("name")
            if kind == "class" and name is not None:
                class_scope[_word(name)] = None
            for child in cur.named_children:
                if child == name:
                    continue
                visit(
                    child,
                    scope if child.type == "class_heritage" else class_scope,
                )
            return
        if kind in {"statement_block", "switch_body"}:
            visit_block(cur, scope)
            return
        if kind == "for_statement":
            loop_scope = dict(scope)
            init = cur.child_by_field_name("initializer")
            if init is not None and init.type == "lexical_declaration":
                for decl in init.named_children:
                    if decl.type == "variable_declarator":
                        for ident in _pattern(decl.child_by_field_name("name")):
                            loop_scope[ident] = None
            for child in cur.named_children:
                visit(child, loop_scope)
            return
        if kind == "for_in_statement":
            loop_scope = dict(scope)
            for ident in _pattern(cur.child_by_field_name("left")):
                loop_scope[ident] = None
            for child in cur.named_children:
                visit(child, loop_scope)
            return
        if kind == "catch_clause":
            catch_scope = dict(scope)
            for ident in _pattern(cur.child_by_field_name("parameter")):
                catch_scope[ident] = None
            visit(cur.child_by_field_name("body"), catch_scope)
            return
        if kind in {"as_expression", "satisfies_expression"}:
            if cur.named_children:
                visit(cur.named_children[0], scope)
            return
        if kind == "call_expression":
            _on_mark_call(cur, scope, dynamic, add)
            record_env(cur, scope)
            record_call_sec(cur, scope)
        elif kind == "new_expression":
            _on_mark_new(cur, scope, dynamic, add)
        elif kind in {"assignment_expression", "augmented_assignment_expression"}:
            _on_mark_assign(cur, scope, dynamic, add)
        elif kind in {"member_expression", "identifier"}:
            _on_mark_ref(cur, scope, dynamic, add, details)
            if kind == "member_expression":
                record_env(cur, scope)
        elif kind == "subscript_expression":
            record_env(cur, scope)
        elif kind == "throw_statement" and not _throw_caught(cur, node):
            msg = _throw_literal_message(cur)
            if msg and msg not in throws:
                throws.append(msg)
        for child in cur.named_children:
            visit(child, scope)

    visit(node, module_scope)
    if len(throws) == 1:
        add_plain(f'Can stop with an error: "{throws[0]}".')
    if plain_out is not None and len(plain_out) > 3:
        del plain_out[3:]
    return [kind for kind in _MARK_ORDER if kind in found]


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
    definitions, exports = _definitions(root, name)
    es_imports = _imports(root)
    counts = Counter(
        _bindings(root) + [key for _, bindings in es_imports for key in bindings]
    )
    assigned = _assigned(root)
    cjs_imports = (
        [] if counts["require"] or "require" in assigned else _cjs_imports(root)
    )
    imports = es_imports + cjs_imports
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
    def_counts = Counter(key for _, _, _, key in definitions)
    mark_scope = _mark_scope(root, imports, counts, _free_writes(root), dynamic)
    children, local, defs, used_ids = [], {}, [], set()
    for ident, node, outer, key in definitions:
        start, end = outer.start_point[0] + 1, _line(outer.end_point)
        cid = f"{name}:{ident}:{start}"
        if cid in used_ids:
            col = node.start_point[1] + 1
            cid = f"{name}:{ident}:{start}:{col}"
            n = 2
            while cid in used_ids:
                cid = f"{name}:{ident}:{start}:{col}:{n}"
                n += 1
        used_ids.add(cid)
        defs.append((cid, node))
        info = _source(text, start, end)
        if _js_passes(node):
            info["proven"] = PASSES
        sec_list, plain_list = [], []
        badges = _marks(
            node,
            mark_scope,
            dynamic,
            security_out=sec_list,
            plain_out=plain_list,
        )
        if badges:
            info["badges"] = badges
        if sec_list:
            info["security"] = sec_list
        if plain_list:
            info["plain"] = plain_list
        children.append(
            {
                "id": cid,
                "labels": [{"text": ident if node.type in CLASSES else f"{ident}()"}],
                "asbuilt": info,
            }
        )
        if counts[key] <= 1 and def_counts[key] == 1 and key not in assigned:
            local[key] = cid
    box = {
        "id": name,
        "labels": [{"text": name}],
        **({"children": children} if children else {}),
    }
    return box, defs, local, exports, imports, counts, assigned


def _own(path):
    return (
        path.suffix in PARSERS
        and not path.name.startswith((".", "test_"))
        and not any(s in path.name for s in (".test.", ".spec.", ".d.ts", ".d.mts", ".d.cts"))
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


def _has_project_marker(folder):
    return (folder / ".git").exists() or (folder / "package.json").is_file()


def _excluded_dir_root(path):
    folder = path.resolve()
    if folder.name.startswith(".") or folder.name == "node_modules":
        return True
    if folder.name not in SKIP:
        return False
    if not _has_project_marker(folder):
        return True
    return any(_has_project_marker(p) for p in folder.parents)


def _files(path):
    if path.is_symlink():
        raise PlanError(f"{path.name}: a symlink is not own source")
    if path.is_dir():
        if _excluded_dir_root(path):
            raise PlanError(
                f"{path.name}: a test or dependency folder is not own source"
            )
    elif path.resolve().name in SKIP or path.resolve().name.startswith("."):
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


TS_FALLBACK = {
    ".js": (".ts", ".tsx"),
    ".jsx": (".tsx", ".ts"),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
}
INDEX_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
_JSONC_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
_JSONC_TRAILING_COMMAS = re.compile(r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])')


def _strip_jsonc(text):
    no_comments = _JSONC_COMMENTS.sub(
        lambda m: m.group(0) if m.group(0).startswith('"') else " ", text
    )
    return _JSONC_TRAILING_COMMAS.sub(
        lambda m: m.group(0) if m.group(0).startswith('"') else "", no_comments
    )


def _norm_config_path(raw):
    norm = posixpath.normpath(raw.replace("\\", "/"))
    if norm.startswith("/") or norm == ".." or norm.startswith("../"):
        return None
    return norm


def _load_aliases(root_dir):
    if not root_dir.is_dir():
        return None
    config = next(
        (
            p
            for name in ("tsconfig.json", "jsconfig.json")
            if (p := root_dir / name).is_file() and not p.is_symlink()
        ),
        None,
    )
    if config is None:
        return None
    data = None
    try:
        text = read_bytes(config).decode("utf-8-sig")
        data = json.loads(_strip_jsonc(text))
    except (OSError, PlanError, UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or "extends" in data:
        return None  # an inherited config may override paths; never guess them
    opts = data.get("compilerOptions")
    if not isinstance(opts, dict):
        return None
    base = "."
    has_base_url = False
    if "baseUrl" in opts:
        raw_base = opts["baseUrl"]
        if not isinstance(raw_base, str):
            return None
        norm_base = _norm_config_path(raw_base)
        if norm_base is None:
            return None
        base = norm_base
        has_base_url = True
    paths = {}
    raw_paths = opts.get("paths")
    if isinstance(raw_paths, dict):
        for pattern, targets in raw_paths.items():
            if (
                isinstance(pattern, str)
                and pattern.count("*") <= 1
                and isinstance(targets, list)
            ):
                clean = [t for t in targets if isinstance(t, str)]
                if clean:
                    paths[pattern] = clean
    if not has_base_url and not paths:
        return None
    return (base, has_base_url, paths)


def _resolve_candidates(target, files, dir_only=False, root=None):
    # A folder's package.json "main" or "exports" can name a file other than its
    # index, so under a charted root such a folder proves no directory import.
    if not dir_only:
        suffix = Path(target).suffix
        if suffix in PARSERS or suffix in TS_FALLBACK:
            if target in files:
                return {target}
            alts = TS_FALLBACK.get(suffix, ())
            stem = target[: -len(suffix)]
            return {stem + ext for ext in alts} & files.keys()
        direct = {target + ext for ext in PARSERS} & files.keys()
        if direct:
            return direct
    if (
        root is not None
        and target != ".."
        and not target.startswith("../")
        and (root / target / "package.json").exists()
    ):
        return set()
    index_candidates = {
        posixpath.normpath(posixpath.join(target, f"index{ext}")) for ext in INDEX_EXTS
    }
    return index_candidates & files.keys()


def _resolve_alias(spec, files, aliases, root=None):
    base, has_base_url, paths = aliases
    matches = []
    for pattern, targets in paths.items():
        if "*" not in pattern:
            if spec == pattern:
                matches.append(((1, len(pattern), 0), targets, None))
        else:
            prefix, suffix = pattern.split("*", 1)
            if (
                spec.startswith(prefix)
                and spec.endswith(suffix)
                and len(spec) >= len(prefix) + len(suffix)
            ):
                end = len(spec) - len(suffix) if suffix else len(spec)
                matches.append(
                    ((0, len(prefix), len(suffix)), targets, spec[len(prefix) : end])
                )
    if matches:
        best = max(k for k, _, _ in matches)
        found = set()
        for k, targets, star in matches:
            if k != best:
                continue
            for rep in targets:
                expanded = (
                    rep.replace("*", star, 1) if star is not None and "*" in rep else rep
                )
                candidate = _norm_config_path(posixpath.join(base, expanded))
                if candidate is None:
                    continue
                found.update(
                    _resolve_candidates(
                        candidate,
                        files,
                        dir_only=spec.endswith("/") or expanded.endswith("/"),
                        root=root,
                    )
                )
        return next(iter(found)) if len(found) == 1 else None
    if has_base_url:
        candidate = _norm_config_path(posixpath.join(base, spec))
        if candidate is not None:
            found = _resolve_candidates(
                candidate, files, dir_only=spec.endswith("/"), root=root
            )
            return next(iter(found)) if len(found) == 1 else None
    return None


def _nested_config(root, source):
    # A tsconfig/jsconfig below the charted root governs its own subtree.
    folder = posixpath.dirname(source)
    while folder:
        if any((root / folder / n).exists() for n in ("tsconfig.json", "jsconfig.json")):
            return True
        folder = posixpath.dirname(folder)
    return False


def _module(source, spec, files, aliases=None, root=None):
    # files holds every discovered name, parsed or skipped, so a skipped file still
    # counts as the exact match or as a rival candidate; callers check it parsed.
    # root is the charted folder, read for package.json and nested configs.
    if spec in (".", "..") or spec.startswith(("./", "../")):
        dir_only = spec in (".", "..") or spec.endswith("/")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), spec))
        found = _resolve_candidates(target, files, dir_only=dir_only, root=root)
        return next(iter(found)) if len(found) == 1 else None
    if aliases is not None and spec and not spec.startswith("/"):
        if root is not None and _nested_config(root, source):
            return None
        return _resolve_alias(spec, files, aliases, root)
    return None


def chart(path, skipped=None):
    """Existing ELK plan shape, with file import arrows and proven definition calls."""
    files = {}
    unparseable = []
    is_dir = path.is_dir()
    aliases = _load_aliases(path) if is_dir else None
    found = _files(path)
    known = dict.fromkeys(name for name, _ in found)
    for name, file in found:
        try:
            files[name] = _file(name, read_bytes(file))
        except PlanError:
            if not is_dir:
                raise
            unparseable.append(name)
    if not files:
        detail = f" (skipped {', '.join(unparseable)})" if unparseable else ""
        raise PlanError(
            f"{path.name}: no parseable JavaScript/TypeScript files to chart{detail}"
        )
    if skipped is not None:
        skipped.extend(unparseable)
    edges = {}
    for name, (box, definitions, local, _, imports, counts, assigned) in files.items():
        calls = {}
        scope = dict(local)
        for spec, bindings in imports:
            target = _module(name, spec, known, aliases, path if is_dir else None)
            if target is None or target == name or target not in files:
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
        for source, node in definitions:
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


COMMANDS, WEB_PAGES, SCRIPTS, OTHER = "commands", "web pages", "scripts", "other"
KINDS = (COMMANDS, WEB_PAGES, SCRIPTS, OTHER)
NO_PATH = "no path from an entry point found by asBuilt"
(
    _BIN_CMD,
    _SHEBANG_CMD,
    _ARGV_CMD,
    _WEB_PAGE_FILE,
    _WEB_MOUNT,
    _REACT_COMP,
    _TOP_SCRIPT,
) = range(7)
_DOM_MOUNTS = frozenset({"createRoot", "hydrateRoot"})
_ROUTE_RECEIVERS = frozenset({"app", "router", "server", "fastify"})
_ROUTE_VERBS = frozenset(
    {"get", "post", "put", "delete", "patch", "use", "route", "all"}
)
_APP_FACTORIES = frozenset({"express", "Router", "Fastify", "fastify", "Hono", "Koa"})
_SHEBANG_RUNTIMES = ("node", "bun", "deno", "tsx", "ts-node")


def _component_kind(hints):
    tags = {tag for tag, _ in hints}
    if tags & {_BIN_CMD, _SHEBANG_CMD}:
        return COMMANDS
    if tags & {_WEB_PAGE_FILE, _WEB_MOUNT, _REACT_COMP}:
        return WEB_PAGES
    if _ARGV_CMD in tags:
        return COMMANDS
    if _TOP_SCRIPT in tags:
        return SCRIPTS
    return OTHER


def _file_hint(rel):
    path_obj = Path(rel)
    if path_obj.stem == "index" and len(path_obj.parts) >= 2:
        return path_obj.parts[-2]
    return path_obj.stem


def _page_hint(rel):
    path_obj = Path(rel)
    if path_obj.stem in {"page", "route", "index"} and len(path_obj.parts) >= 2:
        parent = path_obj.parts[-2]
        if parent not in {"pages", "app", "src"}:
            return parent
    return path_obj.stem


def _def_hint(rel, ident):
    if not ident or ident == "default":
        return _file_hint(rel)
    return ident


def _script_hint(rel, ident):
    if not ident or ident in {"main", "init", "run", "start", "boot", "default"}:
        return _file_hint(rel)
    return ident


def _is_web_page_path(rel):
    norm = posixpath.normpath(rel.replace("\\", "/"))
    parts = norm.split("/")
    if parts[0] == "pages" and len(parts) >= 2:
        return True
    if parts[:2] == ["src", "pages"] and len(parts) >= 3:
        return True
    if Path(norm).stem in {"page", "route"} and (
        (parts[0] == "app" and len(parts) >= 2)
        or (parts[:2] == ["src", "app"] and len(parts) >= 3)
    ):
        return True
    return False


def _package_bins(path, charted_files):
    is_dir = path.is_dir()
    root_dir = path.resolve() if is_dir else path.resolve().parent
    pkg_file = None
    for cur in [root_dir, *root_dir.parents]:
        if cur.name == "node_modules" or "node_modules" in cur.parts:
            break
        candidate = cur / "package.json"
        if candidate.is_file() and not candidate.is_symlink():
            pkg_file = candidate
            break
        if (cur / ".git").exists():
            break
    if pkg_file is None:
        return [], []
    data = None
    try:
        raw_text = read_bytes(pkg_file).decode("utf-8-sig")
        data = json.loads(raw_text)
    except (OSError, PlanError, UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        return [], []
    raw_bin = data.get("bin")
    raw_name = data.get("name")
    default_cmd = (
        raw_name.split("/")[-1].strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else None
    )
    entries = []
    if isinstance(raw_bin, str) and raw_bin.strip():
        target_str = raw_bin.strip()
        entries.append((default_cmd or Path(target_str).stem, target_str))
    elif isinstance(raw_bin, dict):
        for cmd_key, target_val in sorted(raw_bin.items()):
            if (
                isinstance(cmd_key, str)
                and cmd_key.strip()
                and isinstance(target_val, str)
                and target_val.strip()
            ):
                entries.append((cmd_key.strip(), target_val.strip()))
    found_bins = []
    unproven = []
    single_resolved = path.resolve() if not is_dir else None
    for cmd_name, raw_target in entries:
        target_abs = (pkg_file.parent / raw_target).resolve()
        if not is_dir:
            stem_matches = {single_resolved}
            suffix = target_abs.suffix
            for alt in TS_FALLBACK.get(suffix, ()):
                stem_matches.add(target_abs.with_suffix(alt))
            if single_resolved in stem_matches and path.name in charted_files:
                found_bins.append((cmd_name, raw_target, path.name))
            continue
        rel_target = None
        try:
            rel_target = target_abs.relative_to(root_dir).as_posix()
        except ValueError:
            rel_target = None
        matched = None
        if rel_target is not None:
            candidates = _resolve_candidates(rel_target, charted_files)
            if len(candidates) == 1:
                matched = next(iter(candidates))
            elif not candidates and rel_target.startswith(("dist/", "build/", "lib/")):
                rest = rel_target.split("/", 1)[1]
                fallback = _resolve_candidates(
                    rest, charted_files
                ) or _resolve_candidates(f"src/{rest}", charted_files)
                if len(fallback) == 1:
                    matched = next(iter(fallback))
        if matched is not None:
            found_bins.append((cmd_name, raw_target, matched))
        else:
            unproven.append(
                f"package.json bin {cmd_name} -> {raw_target}: not a file charted here"
            )
    return found_bins, unproven


def _app_var_names(root, mark_scope):
    imported_known = {k for k, v in mark_scope.items() if v is not None}
    names = set(_ROUTE_RECEIVERS) - imported_known
    for node in _walk(root, nested=True):
        if node.type != "variable_declarator":
            continue
        lhs = node.child_by_field_name("name")
        rhs = _unwrap_expr(node.child_by_field_name("value"))
        if lhs is None or lhs.type != "identifier" or rhs is None:
            continue
        if rhs.type not in {"call_expression", "new_expression"}:
            continue
        field = "function" if rhs.type == "call_expression" else "constructor"
        callee = _unwrap_expr(rhs.child_by_field_name(field))
        if callee is None:
            continue
        if callee.type == "identifier" and _word(callee) in _APP_FACTORIES:
            names.add(_word(lhs))
        elif callee.type == "member_expression":
            prop = callee.child_by_field_name("property")
            if prop is not None and _word(prop) in _APP_FACTORIES:
                names.add(_word(lhs))
    return names


def _collect_arg_targets(call_node, scope, hidden):
    targets = []
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return targets
    for raw_arg in args.named_children:
        arg = _unwrap_expr(raw_arg)
        if arg is None:
            continue
        if arg.type == "identifier":
            ident = _word(arg)
            if ident not in hidden and ident in scope:
                targets.append(scope[ident])
        elif arg.type in FUNCTIONS:
            cb_body = arg.child_by_field_name("body")
            cb_params = arg.child_by_field_name("parameters") or arg.child_by_field_name(
                "parameter"
            )
            cb_hidden = (
                set(hidden)
                | set(_bindings(cb_body) + _pattern(cb_params))
                | _assigned(cb_body)
            )
            for sub in _walk(cb_body, nested=False):
                field = {
                    "call_expression": "function",
                    "new_expression": "constructor",
                }.get(sub.type)
                callee = sub.child_by_field_name(field) if field else None
                if (
                    callee is not None
                    and callee.type == "identifier"
                    and _word(callee) not in cb_hidden
                    and _word(callee) in scope
                ):
                    targets.append(scope[_word(callee)])
        for sub in _walk(arg, nested=True):
            if sub.type in {"jsx_opening_element", "jsx_self_closing_element"}:
                tag = sub.child_by_field_name("name")
                if tag is not None and tag.type == "identifier":
                    tname = _word(tag)
                    if tname not in hidden and tname in scope:
                        targets.append(scope[tname])
    return targets


def _scan_entry_calls(ast_node, scope, mark_scope, dynamic, app_vars, hidden):
    dom_mounts = []
    http_routes = []
    dom_listeners = []
    wired = []
    render_calls = []

    for cur in _walk(ast_node, nested=True):
        if cur.type != "call_expression":
            continue
        callee = _unwrap_expr(cur.child_by_field_name("function"))
        if callee is None:
            continue
        if callee.type == "identifier":
            cname = _word(callee)
            if cname in _DOM_MOUNTS and cname not in hidden:
                dom_mounts.append(f"{cname}()")
                wired.extend(_collect_arg_targets(cur, scope, hidden))
            elif cname == "addEventListener" and cname not in hidden:
                dom_listeners.append("addEventListener()")
                wired.extend(_collect_arg_targets(cur, scope, hidden))
            elif cname == "createServer" and cname not in hidden:
                path = _resolve_mark_path(callee, mark_scope, dynamic)
                if path in {
                    ("module", "http", "createServer"),
                    ("module", "https", "createServer"),
                }:
                    http_routes.append((path[1], "createServer", ""))
                    wired.extend(_collect_arg_targets(cur, scope, hidden))
        elif callee.type == "member_expression":
            obj = _unwrap_expr(callee.child_by_field_name("object"))
            prop = callee.child_by_field_name("property")
            if prop is None or prop.type != "property_identifier":
                continue
            pname = _word(prop)
            if pname in _DOM_MOUNTS:
                dom_mounts.append(f"{pname}()")
                wired.extend(_collect_arg_targets(cur, scope, hidden))
            elif (
                pname in {"render", "hydrate"}
                and obj is not None
                and obj.type == "identifier"
                and _word(obj) == "ReactDOM"
            ):
                dom_mounts.append(f"ReactDOM.{pname}()")
                wired.extend(_collect_arg_targets(cur, scope, hidden))
            elif pname == "render":
                render_calls.append(cur)
            elif pname == "addEventListener":
                dom_listeners.append("addEventListener()")
                wired.extend(_collect_arg_targets(cur, scope, hidden))
            elif pname == "createServer":
                path = _resolve_mark_path(callee, mark_scope, dynamic)
                obj_name = _word(obj) if obj is not None and obj.type == "identifier" else ""
                if path in {
                    ("module", "http", "createServer"),
                    ("module", "https", "createServer"),
                    ("global", "http", "createServer"),
                    ("global", "https", "createServer"),
                } or obj_name in {"http", "https"}:
                    http_routes.append((obj_name or "http", "createServer", ""))
                    wired.extend(_collect_arg_targets(cur, scope, hidden))
            elif (
                pname in _ROUTE_VERBS
                and obj is not None
                and obj.type == "identifier"
                and _word(obj) in app_vars
                and _word(obj) not in hidden
            ):
                args = cur.child_by_field_name("arguments")
                named_args = args.named_children if args is not None else []
                valid_route = (pname in {"use", "route"} and len(named_args) >= 1) or (
                    pname not in {"use", "route"}
                    and (
                        len(named_args) >= 2
                        or (
                            len(named_args) == 1
                            and named_args[0].type
                            in {
                                "string",
                                "template_string",
                                "identifier",
                                "arrow_function",
                                "function_expression",
                            }
                        )
                    )
                )
                if valid_route:
                    route_arg = (
                        _word(named_args[0])
                        if named_args and named_args[0].type == "string"
                        else ""
                    )
                    http_routes.append((_word(obj), pname, route_arg))
                    wired.extend(_collect_arg_targets(cur, scope, hidden))

    if dom_mounts and render_calls:
        for rcall in render_calls:
            wired.extend(_collect_arg_targets(rcall, scope, hidden))

    dedup_wired = []
    for item in wired:
        if item not in dedup_wired:
            dedup_wired.append(item)
    return dom_mounts, http_routes, dom_listeners, dedup_wired


def find_components(
    path: Path,
    skipped: list[str] | None = None,
    *,
    chart_data: dict | None = None,
) -> dict:
    """Group JS/TS definitions into components, shared helpers, and no_path_found using parser-proven facts."""
    path = Path(path)
    local_skipped: list[str] = []
    if chart_data is not None:
        plan = chart_data
        if skipped is not None:
            local_skipped = list(skipped)
    else:
        plan = chart(path, skipped=local_skipped)
        if skipped is not None:
            skipped.extend(local_skipped)

    is_dir = path.is_dir()
    file_boxes = (
        plan["children"][0].get("children", [])
        if is_dir
        else plan.get("children", [])
    )
    source_map = dict(_files(path))
    aliases = _load_aliases(path) if is_dir else None

    names: dict[str, str] = {}
    file_of: dict[str, str] = {}
    lines_of: dict[str, list[int]] = {}
    defs_by_file: dict[str, list[str]] = {}
    edges: list[tuple[str, str]] = []

    for fbox in file_boxes:
        rel = fbox["id"]
        defs_by_file[rel] = []
        for child in fbox.get("children", []):
            cid = child["id"]
            names[cid] = child["labels"][0]["text"]
            file_of[cid] = rel
            lines_of[cid] = list(child.get("asbuilt", {}).get("lines", []))
            defs_by_file[rel].append(cid)
        for edge in fbox.get("edges", []):
            if not edge.get("labels"):
                edges.append((edge["sources"][0], edge["targets"][0]))

    parsed_files = {}
    for fbox in file_boxes:
        rel = fbox["id"]
        disk_path = source_map[rel]
        raw_bytes = read_bytes(disk_path)
        text = raw_bytes.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        root = _tree(Path(rel).suffix, text.encode())
        definitions, exports = _definitions(root, rel)
        es_imports = _imports(root)
        counts = Counter(
            _bindings(root) + [key for _, bindings in es_imports for key in bindings]
        )
        assigned = _assigned(root)
        cjs_imports = (
            [] if counts["require"] or "require" in assigned else _cjs_imports(root)
        )
        imports = es_imports + cjs_imports
        dynamic = any(
            n.type == "with_statement"
            or (
                n.type == "call_expression"
                and _word(n.child_by_field_name("function")) == "eval"
            )
            for n in _walk(root, True)
        )
        if dynamic:
            assigned.update(counts)
        def_counts = Counter(key for _, _, _, key in definitions)
        mark_scope = _mark_scope(root, imports, counts, _free_writes(root), dynamic)
        local = {}
        def_records = []
        for child, (ident, node, outer, key) in zip(
            fbox.get("children", []), definitions, strict=False
        ):
            cid = child["id"]
            def_records.append((cid, ident, node, outer, key))
            if counts[key] <= 1 and def_counts[key] == 1 and key not in assigned:
                local[key] = cid
        parsed_files[rel] = {
            "text": text,
            "root": root,
            "definitions": def_records,
            "local": local,
            "exports": exports,
            "imports": imports,
            "counts": counts,
            "assigned": assigned,
            "mark_scope": mark_scope,
            "dynamic": dynamic,
        }

    file_keys = dict.fromkeys(source_map)
    for rel, pdata in parsed_files.items():
        scope = dict(pdata["local"])
        for spec, bindings in pdata["imports"]:
            target = _module(rel, spec, file_keys, aliases, path if is_dir else None)
            if target is None or target == rel or target not in parsed_files:
                continue
            target_exports = parsed_files[target]["exports"]
            target_local = parsed_files[target]["local"]
            for ident, exported in bindings.items():
                actual = target_exports.get(exported)
                if (
                    pdata["counts"][ident] == 1
                    and ident not in pdata["assigned"]
                    and actual in target_local
                ):
                    scope[ident] = target_local[actual]
        pdata["scope"] = scope

    all_edges: list[tuple[str, str]] = list(edges)
    edge_set = set(all_edges)

    def add_edge(src, tgt):
        if src != tgt and (src, tgt) not in edge_set:
            edge_set.add((src, tgt))
            all_edges.append((src, tgt))

    ident_of: dict[str, str] = {}
    exported_defs_by_file: dict[str, list[str]] = {}
    explicit_entry_defs_by_file: dict[str, list[str]] = {}
    top_calls_by_file: dict[str, list[str]] = {}
    def_entries = []
    react_candidates = []
    top_entries = []

    for rel, pdata in parsed_files.items():
        root = pdata["root"]
        scope = pdata["scope"]
        mark_scope = pdata["mark_scope"]
        dynamic = pdata["dynamic"]
        exports = pdata["exports"]
        exported_idents = {v for v in exports.values() if v}
        app_vars = _app_var_names(root, mark_scope)
        exported_defs_by_file[rel] = []
        explicit_entry_defs_by_file[rel] = []
        top_calls_by_file[rel] = []
        def_outers = {outer for _, _, _, outer, _ in pdata["definitions"]}

        for cid, ident, node, outer, key in pdata["definitions"]:
            ident_of[cid] = ident
            is_exported = (outer.type == "export_statement") or (
                key in exported_idents
            )
            if is_exported:
                exported_defs_by_file[rel].append(cid)

            details: set[str] = set()
            _marks(node, mark_scope, dynamic, details=details)
            reads_argv = "argv" in details

            if node.type not in CLASSES:
                body = node.child_by_field_name("body")
                params = node.child_by_field_name(
                    "parameters"
                ) or node.child_by_field_name("parameter")
                hidden = (
                    set(_bindings(body) + _pattern(params)) | _assigned(body)
                )
            else:
                body = node
                hidden = set()

            for sub in _walk(body, nested=True):
                if sub.type in {"jsx_opening_element", "jsx_self_closing_element"}:
                    tag = sub.child_by_field_name("name")
                    if tag is not None and tag.type == "identifier":
                        tname = _word(tag)
                        if tname not in hidden and tname in scope:
                            add_edge(cid, scope[tname])

            dom_mounts, http_routes, dom_listeners, wired = _scan_entry_calls(
                node, scope, mark_scope, dynamic, app_vars, hidden
            )
            for t_cid in wired:
                add_edge(cid, t_cid)

            if reads_argv:
                explicit_entry_defs_by_file[rel].append(cid)
                def_entries.append(
                    (
                        ("def", cid),
                        f"process.argv in {rel}: {names[cid]}",
                        [cid],
                        (_ARGV_CMD, _def_hint(rel, ident)),
                    )
                )

            if dom_mounts or http_routes or dom_listeners:
                explicit_entry_defs_by_file[rel].append(cid)
                for m_desc in dom_mounts:
                    def_entries.append(
                        (
                            ("def", cid),
                            f"{m_desc} in {rel}: {names[cid]}",
                            [cid],
                            (_WEB_MOUNT, _def_hint(rel, ident)),
                        )
                    )
                for obj_name, verb, route_arg in http_routes:
                    at = f" {route_arg}" if route_arg else ""
                    way = (
                        f"{verb.upper()}{at} on {obj_name} in {rel}: {names[cid]}"
                        if verb != "createServer"
                        else f"{obj_name}.createServer() in {rel}: {names[cid]}"
                    )
                    def_entries.append(
                        (
                            ("routes", rel, obj_name),
                            way,
                            [cid, *wired],
                            (_WEB_MOUNT, _def_hint(rel, ident)),
                        )
                    )
                for l_desc in dom_listeners:
                    def_entries.append(
                        (
                            ("def", cid),
                            f"{l_desc} in {rel}: {names[cid]}",
                            [cid],
                            (_WEB_MOUNT, _file_hint(rel)),
                        )
                    )

            returns_jsx = any(
                n.type in {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
                for n in _walk(node, nested=True)
            )
            if (
                is_exported
                and ident
                and ident[0].isupper()
                and ident[0].isalpha()
                and returns_jsx
            ):
                react_candidates.append((rel, cid, ident))

        top_dom_mounts = []
        top_http_routes = []
        top_dom_listeners = []
        top_wired = []
        top_details: set[str] = set()

        for outer in root.named_children:
            if outer in def_outers or outer.type == "import_statement":
                continue
            _marks(outer, mark_scope, dynamic, details=top_details)
            for cur in _walk(outer, nested=False):
                field = {
                    "call_expression": "function",
                    "new_expression": "constructor",
                }.get(cur.type)
                callee = cur.child_by_field_name(field) if field else None
                if (
                    callee is not None
                    and callee.type == "identifier"
                    and _word(callee) in scope
                ):
                    t_cid = scope[_word(callee)]
                    if t_cid not in top_calls_by_file[rel]:
                        top_calls_by_file[rel].append(t_cid)
                    if file_of.get(t_cid) == rel and t_cid not in explicit_entry_defs_by_file[rel]:
                        explicit_entry_defs_by_file[rel].append(t_cid)
                if cur.type == "call_expression":
                    for cb_target in _collect_arg_targets(cur, scope, set()):
                        if cb_target not in top_wired:
                            top_wired.append(cb_target)

            m_list, r_list, l_list, w_list = _scan_entry_calls(
                outer, scope, mark_scope, dynamic, app_vars, set()
            )
            top_dom_mounts.extend(m_list)
            top_http_routes.extend(r_list)
            top_dom_listeners.extend(l_list)
            for w_cid in w_list:
                if w_cid not in top_wired:
                    top_wired.append(w_cid)

        for t_cid in top_calls_by_file[rel]:
            if file_of.get(t_cid) == rel:
                top_entries.append(
                    (
                        ("def", t_cid),
                        f"top-level call {names[t_cid]} in {rel}",
                        [t_cid],
                        (_TOP_SCRIPT, _script_hint(rel, ident_of.get(t_cid, ""))),
                    )
                )

        if top_dom_mounts or top_http_routes or top_dom_listeners or "argv" in top_details:
            top_entries.append(
                (
                    "top_file",
                    rel,
                    top_dom_mounts,
                    top_http_routes,
                    top_dom_listeners,
                    "argv" in top_details,
                    top_wired,
                )
            )

    incoming_targets = {tgt for _, tgt in all_edges}
    internal_incoming = {
        tgt for src, tgt in all_edges if file_of.get(src) == file_of.get(tgt)
    }

    entries = list(def_entries)
    for rel, cid, ident in react_candidates:
        if cid not in incoming_targets or _is_web_page_path(rel):
            if cid not in explicit_entry_defs_by_file[rel]:
                explicit_entry_defs_by_file[rel].append(cid)
            entries.append(
                (
                    ("def", cid),
                    f"React component {names[cid]} in {rel}",
                    [cid],
                    (_REACT_COMP, _def_hint(rel, ident)),
                )
            )

    def file_entry_roots(rel, extra_roots=()):
        roots = []
        for candidate in (
            *extra_roots,
            *top_calls_by_file.get(rel, ()),
            *explicit_entry_defs_by_file.get(rel, ()),
        ):
            if candidate not in roots:
                roots.append(candidate)
        if roots:
            return roots
        exported = [
            c
            for c in exported_defs_by_file.get(rel, ())
            if c not in internal_incoming
        ] or list(exported_defs_by_file.get(rel, ()))
        if exported:
            return exported
        own_defs = defs_by_file.get(rel, ())
        return [c for c in own_defs if c not in internal_incoming] or list(own_defs)

    for item in top_entries:
        if item[0] != "top_file":
            entries.append(item)
            continue
        _, rel, t_mounts, t_routes, t_listeners, has_argv, t_wired = item
        f_roots = file_entry_roots(rel, t_wired)
        for m_desc in t_mounts:
            entries.append(
                (
                    ("file", rel),
                    f"{m_desc} in {rel}",
                    f_roots,
                    (_WEB_MOUNT, _file_hint(rel)),
                )
            )
        for obj_name, verb, route_arg in t_routes:
            at = f" {route_arg}" if route_arg else ""
            way = (
                f"{verb.upper()}{at} on {obj_name} in {rel}"
                if verb != "createServer"
                else f"{obj_name}.createServer() in {rel}"
            )
            entries.append(
                (
                    ("routes", rel, obj_name),
                    way,
                    f_roots,
                    (_WEB_MOUNT, _file_hint(rel)),
                )
            )
        for l_desc in t_listeners:
            entries.append(
                (
                    ("file", rel),
                    f"{l_desc} in {rel}",
                    f_roots,
                    (_WEB_MOUNT, _file_hint(rel)),
                )
            )
        if has_argv:
            entries.append(
                (
                    ("file", rel),
                    f"process.argv in {rel}",
                    f_roots,
                    (_ARGV_CMD, _file_hint(rel)),
                )
            )

    found_bins, unproven = _package_bins(path, parsed_files)
    for cmd_name, _raw_target, matched_rel in found_bins:
        entries.append(
            (
                ("file", matched_rel),
                f"package.json bin {cmd_name} -> {matched_rel}",
                file_entry_roots(matched_rel),
                (_BIN_CMD, cmd_name),
            )
        )

    for rel, pdata in parsed_files.items():
        first_line = pdata["text"].splitlines()[0].strip() if pdata["text"] else ""
        if first_line.startswith("#!") and any(
            rt in first_line.lower() for rt in _SHEBANG_RUNTIMES
        ):
            entries.append(
                (
                    ("file", rel),
                    f"shebang {first_line} in {rel}",
                    file_entry_roots(rel),
                    (_SHEBANG_CMD, _file_hint(rel)),
                )
            )
        if _is_web_page_path(rel):
            p_roots = file_entry_roots(rel)
            if len(p_roots) == 1 and ident_of.get(p_roots[0], "default") != "default":
                p_hint = ident_of[p_roots[0]]
            else:
                p_hint = _page_hint(rel)
            entries.append(
                (
                    ("file", rel),
                    f"web page {rel}",
                    p_roots,
                    (_WEB_PAGE_FILE, p_hint),
                )
            )

    after: dict[str, list[str]] = {}
    for src, tgt in all_edges:
        after.setdefault(src, []).append(tgt)

    def walk(roots):
        paths = {r: [r] for r in roots}
        todo = list(roots)
        for cur in todo:
            for nxt in after.get(cur, ()):
                if nxt not in paths:
                    paths[nxt] = [*paths[cur], nxt]
                    todo.append(nxt)
        return paths

    union = {}

    def find_set(key):
        while union.get(key, key) != key:
            key = union[key]
        return key

    keyed, nothing = {}, []
    for key, way, roots, hint in entries:
        if not roots:
            if way not in nothing:
                nothing.append(way)
            continue
        slot = keyed.setdefault(key, {"ways": [], "roots": [], "hints": []})
        if way not in slot["ways"]:
            slot["ways"].append(way)
        slot["roots"] += [r for r in roots if r not in slot["roots"]]
        if hint not in slot["hints"]:
            slot["hints"].append(hint)

    owner = {}
    for key, slot in keyed.items():
        for r in slot["roots"]:
            union[find_set(key)] = find_set(owner.setdefault(r, key))

    root_to_key = {r: key for key, slot in keyed.items() for r in slot["roots"]}
    for key, slot in keyed.items():
        for box in walk(slot["roots"]):
            if box in root_to_key:
                union[find_set(root_to_key[box])] = find_set(key)

    merged = {}
    for key, slot in keyed.items():
        into = merged.setdefault(find_set(key), {"ways": [], "roots": [], "hints": []})
        for field in ("ways", "roots", "hints"):
            into[field] += [x for x in slot[field] if x not in into[field]]

    comps = []
    why: dict[str, dict[str, list[str]]] = {}
    all_roots: list[str] = []
    for slot in sorted(merged.values(), key=lambda s: min(s["hints"])):
        name = min(slot["hints"])[1]
        while name in [c["name"] for c in comps]:
            name += "'"
        kind = _component_kind(slot["hints"])
        comps.append(
            {
                "id": name,
                "name": name,
                "kind": kind,
                "ways_in": slot["ways"],
                "roots": slot["roots"],
            }
        )
        for r in slot["roots"]:
            if r not in all_roots:
                all_roots.append(r)
        for box, chain in walk(slot["roots"]).items():
            why.setdefault(box, {})[name] = chain

    def because(chain):
        via = (
            f" via {', '.join(names.get(p, p) for p in chain[1:-1])}"
            if len(chain) > 2
            else ""
        )
        return (
            "entry point"
            if len(chain) == 1
            else f"reached from {names[chain[0]]}{via}"
        )

    users: dict[str, set[str]] = {}
    for box, held in why.items():
        if box in names and len(held) > 1:
            users.setdefault(file_of[box], set()).update(held)
    short = {f: f.rsplit("/", 1)[-1] for f in users}
    short = {
        f: n if list(short.values()).count(n) == 1 else f for f, n in short.items()
    }

    def place(box):
        held = why.get(box, {})
        if len(held) < 2:
            return next(iter(held), NO_PATH)
        return short[file_of[box]]

    group = {box: place(box) for box in names}
    for comp in comps:
        comp["members"] = [
            {
                "id": b,
                "name": names[b],
                "file": file_of[b],
                "lines": lines_of[b],
                "why": because(why[b][comp["name"]]),
            }
            for b in names
            if group[b] == comp["name"]
        ]

    shared = {}
    comp_names = {c["name"] for c in comps}
    for b in names:
        if group[b] not in comp_names and group[b] != NO_PATH:
            f_rel = file_of[b]
            box = shared.setdefault(
                group[b],
                {
                    "name": group[b],
                    "file": f_rel,
                    "used_by": sorted(users[f_rel]),
                    "members": [],
                },
            )
            box["members"].append(
                {
                    "id": b,
                    "name": names[b],
                    "file": f_rel,
                    "lines": lines_of[b],
                    "used_by": sorted(why[b]),
                }
            )

    arrows = {}
    for src, tgt in all_edges:
        if src in group and tgt in group and group[src] != group[tgt]:
            arrows.setdefault((group[src], group[tgt]), []).append(
                {"from": names[src], "to": names[tgt]}
            )

    grouped = [
        {"kind": kind, "components": names_in_kind}
        for kind in KINDS
        if (names_in_kind := [c["name"] for c in comps if c["kind"] == kind])
    ]

    return {
        "components": comps,
        "groups": grouped,
        "shared": list(shared.values()),
        "no_path_found": [
            {
                "id": b,
                "name": names[b],
                "file": file_of[b],
                "lines": lines_of[b],
            }
            for b in names
            if group[b] == NO_PATH
        ],
        "unproven": unproven,
        "roots": all_roots,
        "arrows": [
            {"from": a, "to": b, "calls": calls} for (a, b), calls in arrows.items()
        ],
        "entries_reaching_nothing": nothing,
        "not_proven": unproven,
        "files_not_read": list(local_skipped),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        sources = _files(args.path)
        target = args.out.resolve()
        if target.suffix.lower() != ".json" or (
            target.exists() and any(target.samefile(p) for _, p in sources)
        ):
            raise PlanError(
                f"{args.out.name}: OUT must be JSON and must not replace source"
            )
        skipped = []
        result = chart(args.path, skipped=skipped)
        check(result, args.out.name)
        output.write_atomic(args.out, output.plan_bytes(result, args.out.name))
        if skipped:
            print(f"asbuilt.javascript: skipped {', '.join(skipped)}", file=sys.stderr)
    except (PlanError, OSError) as exc:
        return output.fail("asbuilt.javascript", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
