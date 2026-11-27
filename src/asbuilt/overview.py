"""Build the JSON project overview the overview page draws, from parsing alone.

Usage: python -m asbuilt.overview PATH

The payload holds each language's components as asbuilt.components.find or
asbuilt.javascript.find_components return them, every charted function or class
by id, the files they sit in, and the packages the project's manifests declare.
What could not be read is named, never left out: files that did not parse
(`not_read`), folders skipped (`skipped`), manifests that could not be read
(`packages_unreadable`), declarations in the manifests read (`packages_read`) that
are not read (`packages_not_read`), each reader's entry points it could not follow
(their components' `not_proven` and `entries_reaching_nothing`), and, in a folder with Python and JavaScript/TypeScript,
a JavaScript/TypeScript reader failure (`js_error`) beside the Python components.
"""

import argparse
import json
import sys
from pathlib import Path

from asbuilt import built, components, javascript, output, packages
from asbuilt.plan import read_bytes
from asbuilt.state import PlanError


def _file_boxes(children):
    """The file boxes under built.python_dir's folder boxes, flattened."""
    found = []
    for child in children:
        if child["id"].endswith("/"):
            found.extend(_file_boxes(child.get("children", [])))
        else:
            found.append(child)
    return found


def _flatten(file_boxes, imports=None):
    """(files, boxes by id) from chart file boxes: each box with its file, lines, marks and calls."""
    files, boxes = [], {}
    for fbox in file_boxes:
        file_id = fbox["id"]
        labels = fbox.get("labels", [])
        calls = {}
        for edge in fbox.get("edges", []):
            targets = calls.setdefault(edge["sources"][0], [])
            if edge["targets"][0] not in targets:
                targets.append(edge["targets"][0])
        for child in fbox.get("children", []):
            info = child.get("asbuilt", {})
            child_labels = child.get("labels", [])
            boxes[child["id"]] = {
                "id": child["id"],
                "file": file_id,
                "name": child_labels[0]["text"] if child_labels else child["id"],
                "lines": list(info.get("lines", [])),
                "plain": list(info.get("plain", [])),
                "proven": info.get("proven"),
                "badges": list(info.get("badges", [])),
                "security": list(info.get("security", [])),
                "text": info.get("text", ""),
                "rest": info.get("rest"),
                "calls": calls.get(child["id"], []),
            }
        entry = {"id": file_id, "boxes": [c["id"] for c in fbox.get("children", [])]}
        if imports is not None:
            entry["imports"] = imports.get(file_id, [])
        if len(labels) > 1:  # the reader's note on why the file was not read
            entry["note"] = labels[1]["text"]
        files.append(entry)
    return files, boxes


def _has_js(folder):
    """True when the folder holds a JavaScript/TypeScript file the JS reader charts (its own rule)."""
    todo = [folder]
    while todo:
        for child in sorted(todo.pop().iterdir()):
            if child.is_symlink() or child.name.startswith(".") or child.name in javascript.SKIP:
                continue
            if child.is_dir():
                todo.append(child)
            elif child.is_file() and javascript._own(child):
                return True
    return False


def _javascript(target):
    """(files, boxes, components, files not parsed) for a JS/TS file or folder."""
    unparsed = []
    chart = javascript.chart(target, skipped=unparsed)
    file_boxes = chart["children"][0].get("children", []) if target.is_dir() else chart["children"]
    imports = {}
    for edge in chart.get("edges", []):
        targets = imports.setdefault(edge["sources"][0], [])
        if edge["targets"][0] not in targets:
            targets.append(edge["targets"][0])
    files, boxes = _flatten(file_boxes, imports)
    found = javascript.find_components(target, skipped=list(unparsed), chart_data=chart)
    return files, boxes, found, [f"{name}: not parseable as JavaScript/TypeScript" for name in unparsed]


def _python(target):
    """(files, boxes, components, skipped folders) for a Python file or folder."""
    links = None
    if target.is_dir():
        links = built.python_dir_links(target)
        root, skipped = links[0], links[1]
        file_boxes = _file_boxes(root.get("children", []))
    else:
        file_boxes, skipped = [built.python_file(target.name, read_bytes(target))], []
    files, boxes = _flatten(file_boxes)
    return files, boxes, components.find(target, links), skipped


def build(target):
    """The overview payload for a Python or JavaScript/TypeScript file or folder, or a PlanError."""
    path = Path(target)
    if not path.exists():
        raise PlanError(f"{path.name}: no such file or folder")
    found = packages.find(path)
    payload = {
        "project": found["project"],
        "packages": found["packages"],
        "packages_unreadable": found.get("unreadable", []),
        "packages_read": found["read"],
        "packages_not_read": found["not_read"],
        "components": None,
        "js_components": None,
        "js_error": None,
        "files": [],
        "boxes": {},
        "not_read": [],
        "skipped": [],
    }
    if path.is_file():
        if path.suffix == ".py":
            if built._is_test_path(path):
                raise PlanError(f"{path.name}: test files are not charted.")
            has_py, has_js = True, False
        elif path.suffix in javascript.PARSERS:
            has_py, has_js = False, True
        else:
            raise PlanError(f"{path.name}: not a Python or JavaScript/TypeScript file")
    else:
        has_py, has_js = bool(built._py_files(path)[0]), _has_js(path)
    if has_py:
        files, boxes, comps, skipped = _python(path)
        payload.update(components=comps, files=files, boxes=boxes, skipped=sorted(skipped))
        payload["not_read"] += comps["files_not_read"]
    if has_js:
        try:
            files, boxes, comps, unparsed = _javascript(path)
        except PlanError as exc:
            if not has_py:
                raise
            # The Python half is still true; the JS half is named as failed, not left out.
            payload["js_error"] = str(exc)
        else:
            payload["js_components"] = comps
            payload["files"] += files
            payload["boxes"].update(boxes)
            payload["not_read"] += unparsed
    if not has_py and not has_js:
        raise PlanError(f"{path.name} has no Python or JavaScript/TypeScript files to chart.")
    return payload


def problems(payload):
    """One line per thing the overview could not read, for stderr."""
    lines = []
    if payload["skipped"]:
        lines.append(f"skipped {', '.join(payload['skipped'])}")
    lines += [f"could not read {item}" for item in payload["not_read"]]
    if payload["js_error"]:
        lines.append(f"JavaScript/TypeScript not charted: {payload['js_error']}")
    for label, key in (("Python", "components"), ("JavaScript/TypeScript", "js_components")):
        info = payload[key] or {}
        lines += [f"{label} entry point reaching no charted function: {e}" for e in info.get("entries_reaching_nothing", [])]
        lines += [f"{label} not proven: {e}" for e in info.get("not_proven", [])]
    lines += [f"could not read {name}" for name in payload["packages_unreadable"]]
    lines += [f"packages not read: {item}" for item in payload["packages_not_read"]]
    return lines


def main(argv=None):
    """Print the overview JSON to stdout and what was not read to stderr, or fail with exit 2."""
    parser = argparse.ArgumentParser(
        prog="python -m asbuilt.overview",
        description="Build a JSON project overview for a Python or JavaScript/TypeScript path.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=Path("."),
        type=Path,
        help="project directory or source file (default: current directory)",
    )
    args = parser.parse_args(argv)
    try:
        payload = build(args.path)
    except (PlanError, OSError) as exc:
        return output.fail("asbuilt.overview", exc)
    for line in problems(payload):
        print(f"asbuilt.overview: {line}", file=sys.stderr)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
