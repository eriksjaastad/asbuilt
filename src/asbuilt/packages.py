"""Read declared Python and JavaScript packages from local project manifests.

Usage: python -m asbuilt.packages PATH

Each package is the name a manifest declares, with its version spec, its registry
link, whether it is a dev dependency, and `tags` naming an npm section other than
dependencies/devDependencies (optional, peer). A manifest that exists but cannot be
read is named in `unreadable`. A path that does not exist is an error, exit 2.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote

from asbuilt import output
from asbuilt.state import PlanError

MANIFEST_FILES = ("pyproject.toml", "requirements.txt", "package.json")
DEV_GROUPS = frozenset(
    {"dev", "test", "tests", "lint", "docs", "doc", "typing", "typecheck", "ci"}
)

_PEP508_RE = re.compile(
    r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\s*\[[^\]]*\])?"
    r"\s*(.*)$"
)
_PEP503_NORMALIZE_RE = re.compile(r"[-_.]+")
# A name is joined onto node_modules/ or matched in site-packages, so only a plain registry
# name is looked up there: `../x` or `/etc` must never reach the filesystem.
_PLAIN_NAME = {
    "pypi": re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"),
    "npm": re.compile(r"(?:@[a-z0-9~-][a-z0-9._~-]*/)?[a-z0-9~-][a-z0-9._~-]*", re.IGNORECASE),
}
NPM_SECTIONS = (  # (package.json field, dev, tag)
    ("dependencies", False, None),
    ("devDependencies", True, None),
    ("optionalDependencies", False, "optional"),
    ("peerDependencies", False, "peer"),
)
_SKIP_PREFIXES = ("-", "http://", "https://", "git+", "file:", "./", "../", "/")
_MAX_SUMMARY_LEN = 160

def _pypi_url(name: str) -> str:
    return f"https://pypi.org/project/{quote(name, safe='')}/"


def _npm_url(name: str) -> str:
    return f"https://www.npmjs.com/package/{quote(name, safe='@/')}"


def _norm_key(registry: str, name: str) -> tuple[str, str]:
    if registry == "pypi":
        return (registry, _PEP503_NORMALIZE_RE.sub("-", name).lower())
    return (registry, name.lower())


def _clean_summary(raw: str) -> str:
    single_line = " ".join(raw.split())
    if len(single_line) > _MAX_SUMMARY_LEN:
        return single_line[: _MAX_SUMMARY_LEN - 3].rstrip() + "..."
    return single_line


def _read_local_npm_summary(manifest_dir: Path, name: str) -> str:
    pkg_json_path = manifest_dir / "node_modules" / Path(name) / "package.json"
    if not pkg_json_path.is_file():
        return ""
    data = _read_json(pkg_json_path)
    if data is None:
        return ""
    desc = data.get("description")
    if isinstance(desc, str) and desc.strip():
        return _clean_summary(desc)
    return ""


def _read_local_pypi_summary(manifest_dir: Path, name: str) -> str:
    venv_lib = manifest_dir / ".venv" / "lib"
    if not venv_lib.is_dir():
        return ""
    norm_dash = _PEP503_NORMALIZE_RE.sub("-", name).lower()
    norm_under = norm_dash.replace("-", "_")
    prefixes = (f"{norm_under}-", f"{norm_dash}-")

    site_dirs = sorted(venv_lib.glob("python*/site-packages"))
    for site_dir in site_dirs:
        if not site_dir.is_dir():
            continue
        for dist_info in sorted(site_dir.glob("*.dist-info")):
            lower_name = dist_info.name.lower()
            if not lower_name.startswith(prefixes):
                continue
            rest = lower_name.split("-", 1)[1] if "-" in lower_name else ""
            if not rest or not rest[0].isdigit():
                continue
            meta_file = dist_info / "METADATA"
            if not meta_file.is_file():
                continue
            for line in _read_lines(meta_file) or []:
                if not line.strip():
                    break
                if line.lower().startswith("summary:"):
                    value = line.split(":", 1)[1].strip()
                    if value and value.lower() != "unknown":
                        return _clean_summary(value)
    return ""


def is_plain_name(registry: str, name: str) -> bool:
    """True when `name` is a plain package name for the registry, safe to look up on disk."""
    pattern = _PLAIN_NAME.get(registry)
    return bool(pattern and pattern.fullmatch(name))


def _resolve_summary(manifest_dir: Path, registry: str, name: str) -> str:
    """The package's own installed description, or "" when this project has none installed."""
    if not is_plain_name(registry, name):
        return ""
    if registry == "npm":
        return _read_local_npm_summary(manifest_dir, name)
    return _read_local_pypi_summary(manifest_dir, name)


def _parse_pep508(line: str) -> tuple[str, str] | None:
    """(name, spec) from a PEP 508 requirement, or None when it is not one (an option, URL or path)."""
    text = line.split(" #", 1)[0].strip() if not line.lstrip().startswith("#") else ""
    if not text or text.startswith(_SKIP_PREFIXES):
        return None
    text = text.split(";", 1)[0].strip()
    match = _PEP508_RE.match(text) if text else None
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _read_toml(path: Path) -> dict | None:
    parsed: object = None
    try:
        raw = path.read_text(encoding="utf-8-sig")  # a BOM is not part of the text
        parsed = tomllib.loads(raw)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        parsed = None
    return parsed if isinstance(parsed, dict) else None


def _read_json(path: Path) -> dict | None:
    parsed: object = None
    try:
        raw = path.read_text(encoding="utf-8-sig")  # a BOM is not part of the text
        parsed = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError):
        parsed = None
    return parsed if isinstance(parsed, dict) else None


def _read_lines(path: Path) -> list[str] | None:
    """The file's lines, or None when it cannot be read as UTF-8 text."""
    raw = None
    try:
        raw = path.read_text(encoding="utf-8-sig")  # a BOM is not part of the text
    except (OSError, UnicodeDecodeError):
        raw = None
    return None if raw is None else raw.splitlines()


def _locate_manifest_dir(folder: Path) -> Path | None:
    start = Path(folder).resolve()
    if not start.exists():
        return None
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if any((candidate / name).is_file() for name in MANIFEST_FILES):
            return candidate
        if (candidate / ".git").exists():
            return None
    return None


def _pypi(name: str, spec: str, dev: bool, tags=(), direct: bool = False) -> dict:
    """A PyPI entry; a direct reference (`name @ url`, a git or path source) gets no registry link."""
    direct = direct or spec.startswith("@")
    return {"name": name, "spec": spec, "registry": "pypi", "url": None if direct else _pypi_url(name), "dev": dev, "tags": list(tags)}


def _pep508_list(items, dev: bool, tags, where: str, entries: list, not_read: list) -> None:
    """Add each PEP 508 string of a list; anything else is named in not_read."""
    for item in items:
        parsed = _parse_pep508(item) if isinstance(item, str) else None
        if parsed:
            entries.append(_pypi(*parsed, dev, tags))
        elif not (isinstance(item, dict) and "include-group" in item):  # PEP 735: its group is read on its own
            not_read.append(f"pyproject.toml {where}: {item!r}")


def _poetry_table(deps, dev: bool, tags, where: str, entries: list, not_read: list) -> None:
    """Add each Poetry dependency ("^1.2" or {version = ...}; git, path and url sources get no link)."""
    for name, spec in deps.items():
        if name.lower() == "python":  # the interpreter version, not a package
            continue
        if isinstance(spec, str) and is_plain_name("pypi", name):
            entries.append(_pypi(name, spec, dev, tags))
        elif isinstance(spec, dict) and is_plain_name("pypi", name):
            version = spec.get("version") if isinstance(spec.get("version"), str) else ""
            direct = any(key in spec for key in ("git", "path", "url"))
            entries.append(_pypi(name, version, dev, tags, direct=direct))
        else:
            not_read.append(f"pyproject.toml {where}: {name} = {spec!r}")


def _parse_pyproject(data: dict, not_read: list) -> tuple[str | None, list[dict]]:
    """[project] dependencies and optional-dependencies, PEP 735 [dependency-groups], and Poetry's
    tables. A dependency table another tool keeps that is not read here is named in not_read."""
    entries: list[dict] = []
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    raw_name = project.get("name")
    project_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    if isinstance(project.get("dependencies"), list):
        _pep508_list(project["dependencies"], False, (), "[project] dependencies", entries, not_read)
    if isinstance(project.get("optional-dependencies"), dict):
        for group, items in project["optional-dependencies"].items():
            if isinstance(items, list):
                where = f"[project.optional-dependencies] {group}"
                _pep508_list(items, group.lower() in DEV_GROUPS, (f"extra {group}",), where, entries, not_read)
    if isinstance(data.get("dependency-groups"), dict):
        for group, items in data["dependency-groups"].items():
            if isinstance(items, list):
                _pep508_list(items, True, (f"group {group}",), f"[dependency-groups] {group}", entries, not_read)
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    if isinstance(poetry.get("dependencies"), dict):
        _poetry_table(poetry["dependencies"], False, ("poetry",), "[tool.poetry.dependencies]", entries, not_read)
    if isinstance(poetry.get("dev-dependencies"), dict):
        where = "[tool.poetry.dev-dependencies]"
        _poetry_table(poetry["dev-dependencies"], True, ("poetry dev",), where, entries, not_read)
    groups = poetry.get("group") if isinstance(poetry.get("group"), dict) else {}
    for group, table in groups.items():
        if isinstance(table, dict) and isinstance(table.get("dependencies"), dict):
            where = f"[tool.poetry.group.{group}.dependencies]"
            tags = (f"poetry group {group}",)
            _poetry_table(table["dependencies"], group.lower() in DEV_GROUPS, tags, where, entries, not_read)
    for other in ("uv", "pdm"):  # their dev-dependency tables are not read
        if isinstance(tool.get(other), dict) and "dev-dependencies" in tool[other]:
            not_read.append(f"pyproject.toml [tool.{other}.dev-dependencies] is not read")
    return project_name, entries


def _parse_requirements(lines: list[str], not_read: list) -> list[dict]:
    """Each requirement line; an include (-r), editable, option, URL or path line is named in not_read."""
    entries: list[dict] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parsed = _parse_pep508(line)
        if parsed:
            entries.append(_pypi(*parsed, False))
        else:
            not_read.append(f"requirements.txt: {text}")
    return entries


def _npm_link(name: str, spec: str) -> str | None:
    """The npm page for a registry dependency: an alias (npm:real@range) links to its real name;
    a git, URL, file:, link:, workspace: or GitHub shorthand source has no registry page."""
    if spec.startswith("npm:"):
        rest = spec[4:]
        at = rest.rfind("@")
        name = rest[:at] if at > 0 else rest
    elif ":" in spec or "/" in spec:
        return None
    return _npm_url(name) if is_plain_name("npm", name) else None


def _parse_package_json(data: dict, not_read: list) -> tuple[str | None, list[dict]]:
    raw_name = data.get("name")
    project_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    entries: list[dict] = []
    for field, is_dev, tag in NPM_SECTIONS:
        deps = data.get(field)
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            if not name.strip() or not isinstance(spec, str):
                not_read.append(f"package.json {field}: {name!r}: {spec!r}")
                continue
            clean_name, clean_spec = name.strip(), spec.strip()
            entries.append(
                {
                    "name": clean_name,
                    "spec": clean_spec,
                    "registry": "npm",
                    "url": _npm_link(clean_name, clean_spec),
                    "dev": is_dev,
                    "tags": [tag] if tag else [],
                }
            )
    if "workspaces" in data:
        not_read.append("package.json workspaces: each workspace's own package.json is not read")
    return project_name, entries


def _dedupe_and_sort(entries: list[dict]) -> list[dict]:
    seen: dict[tuple[str, str], dict] = {}
    for entry in entries:
        key = _norm_key(entry["registry"], entry["name"])
        existing = seen.get(key)
        if existing is None:
            seen[key] = {"tags": [], **entry}
        else:
            if existing["dev"] and not entry["dev"]:
                existing["dev"] = False
            if not existing["spec"] and entry["spec"]:
                existing["spec"] = entry["spec"]
            existing["tags"] = sorted(set(existing.get("tags", [])) | set(entry.get("tags", [])))
    return sorted(
        seen.values(),
        key=lambda pkg: (pkg["dev"], pkg["registry"], pkg["name"].lower()),
    )


def find(folder: Path) -> dict:
    """The packages the manifests of `folder`'s project declare (its nearest folder with one).
    `read` names the manifests read; `unreadable` those that exist but could not be read;
    `not_read` each declaration or table present that is not read, so the list never looks
    complete when it is not."""
    if not Path(folder).exists():
        raise PlanError(f"{Path(folder).name}: no such file or folder")
    target = Path(folder).resolve()
    manifest_dir = _locate_manifest_dir(target)
    if manifest_dir is None:
        fallback = target.parent.name if target.is_file() else target.name
        return {"project": fallback, "packages": [], "read": [], "not_read": []}

    project_name: str | None = None
    raw_entries: list[dict] = []
    read, unreadable, not_read = [], [], []
    readers = {
        "pyproject.toml": (_read_toml, _parse_pyproject),
        "requirements.txt": (_read_lines, lambda lines, nr: (None, _parse_requirements(lines, nr))),
        "package.json": (_read_json, _parse_package_json),
    }
    for name, (reader, parse) in readers.items():
        path = manifest_dir / name
        if not path.is_file():
            continue
        data = reader(path)
        if data is None:  # exists but cannot be read or parsed: named, never shown as no packages
            unreadable.append(name)
            continue
        read.append(name)
        found_name, entries = parse(data, not_read)
        project_name = project_name or found_name
        raw_entries.extend(entries)

    packages = _dedupe_and_sort(raw_entries)
    for pkg in packages:
        pkg["summary"] = _resolve_summary(manifest_dir, pkg["registry"], pkg["name"])

    result = {
        "project": project_name or manifest_dir.name,
        "packages": packages,
        "read": read,
        "not_read": not_read,
    }
    if unreadable:
        result["unreadable"] = sorted(unreadable)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m asbuilt.packages",
        description="List declared Python and JavaScript packages for a project.",
    )
    parser.add_argument("path", nargs="?", default=Path("."), type=Path, help="project directory or file")
    args = parser.parse_args(argv)
    try:
        found = find(args.path)
    except PlanError as exc:
        return output.fail("asbuilt.packages", exc)
    print(json.dumps(found, indent=2, ensure_ascii=False))
    for name in found.get("unreadable", []):
        print(f"asbuilt.packages: could not read {name}", file=sys.stderr)
    for item in found["not_read"]:
        print(f"asbuilt.packages: not read: {item}", file=sys.stderr)
    return 1 if found.get("unreadable") else 0


if __name__ == "__main__":
    sys.exit(main())
