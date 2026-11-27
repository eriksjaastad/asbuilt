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
    """Return (package_name, version_spec) from a PEP 508 line, or None if ignored."""
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith(_SKIP_PREFIXES):
        return None
    text = text.split(";", 1)[0].strip()
    if not text:
        return None
    match = _PEP508_RE.match(text)
    if not match:
        return None
    name, spec = match.group(1), match.group(2).strip()
    if spec.startswith("://"):
        return None
    return name, spec


def _read_toml(path: Path) -> dict | None:
    parsed: object = None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = tomllib.loads(raw)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        parsed = None
    return parsed if isinstance(parsed, dict) else None


def _read_json(path: Path) -> dict | None:
    parsed: object = None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError):
        parsed = None
    return parsed if isinstance(parsed, dict) else None


def _read_lines(path: Path) -> list[str] | None:
    """The file's lines, or None when it cannot be read as UTF-8 text."""
    raw = None
    try:
        raw = path.read_text(encoding="utf-8")
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


def _parse_pyproject(path: Path) -> tuple[str | None, list[dict]]:
    data = _read_toml(path)
    if data is None:
        return None, []
    project = data.get("project")
    project_name: str | None = None
    entries: list[dict] = []

    if isinstance(project, dict):
        raw_name = project.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            project_name = raw_name.strip()

        deps = project.get("dependencies")
        if isinstance(deps, list):
            for item in deps:
                if isinstance(item, str):
                    parsed = _parse_pep508(item)
                    if parsed:
                        name, spec = parsed
                        entries.append(
                            {
                                "name": name,
                                "spec": spec,
                                "registry": "pypi",
                                "url": _pypi_url(name),
                                "dev": False,
                            }
                        )

        opt_deps = project.get("optional-dependencies")
        if isinstance(opt_deps, dict):
            for group, items in opt_deps.items():
                if not isinstance(items, list):
                    continue
                is_dev = isinstance(group, str) and group.lower() in DEV_GROUPS
                for item in items:
                    if isinstance(item, str):
                        parsed = _parse_pep508(item)
                        if parsed:
                            name, spec = parsed
                            entries.append(
                                {
                                    "name": name,
                                    "spec": spec,
                                    "registry": "pypi",
                                    "url": _pypi_url(name),
                                    "dev": is_dev,
                                }
                            )

    dep_groups = data.get("dependency-groups")
    if isinstance(dep_groups, dict):
        for items in dep_groups.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    parsed = _parse_pep508(item)
                    if parsed:
                        name, spec = parsed
                        entries.append(
                            {
                                "name": name,
                                "spec": spec,
                                "registry": "pypi",
                                "url": _pypi_url(name),
                                "dev": True,
                            }
                        )

    return project_name, entries


def _parse_requirements(path: Path) -> list[dict]:
    entries: list[dict] = []
    for line in _read_lines(path) or []:
        parsed = _parse_pep508(line)
        if parsed:
            name, spec = parsed
            entries.append(
                {
                    "name": name,
                    "spec": spec,
                    "registry": "pypi",
                    "url": _pypi_url(name),
                    "dev": False,
                }
            )
    return entries


def _parse_package_json(path: Path) -> tuple[str | None, list[dict]]:
    data = _read_json(path)
    if data is None:
        return None, []
    raw_name = data.get("name")
    project_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    entries: list[dict] = []

    for field, is_dev, tag in NPM_SECTIONS:
        deps = data.get(field)
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            if not isinstance(name, str) or not name.strip():
                continue
            clean_name = name.strip()
            clean_spec = spec.strip() if isinstance(spec, str) else ""
            entries.append(
                {
                    "name": clean_name,
                    "spec": clean_spec,
                    "registry": "npm",
                    "url": _npm_url(clean_name) if is_plain_name("npm", clean_name) else None,
                    "dev": is_dev,
                    "tags": [tag] if tag else [],
                }
            )
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
    """Find declared packages in `folder` (or its enclosing project root)."""
    if not Path(folder).exists():
        raise PlanError(f"{Path(folder).name}: no such file or folder")
    target = Path(folder).resolve()
    manifest_dir = _locate_manifest_dir(target)
    if manifest_dir is None:
        fallback = target.parent.name if target.is_file() else target.name
        return {"project": fallback, "packages": []}

    project_name: str | None = None
    raw_entries: list[dict] = []
    # A manifest that exists but cannot be read or parsed is named, never shown as
    # a project with no packages.
    readers = {"pyproject.toml": _read_toml, "requirements.txt": _read_lines, "package.json": _read_json}
    unreadable = sorted(
        name for name, read in readers.items()
        if (manifest_dir / name).is_file() and read(manifest_dir / name) is None
    )

    pyproject_path = manifest_dir / "pyproject.toml"
    if pyproject_path.is_file() and "pyproject.toml" not in unreadable:
        py_name, py_entries = _parse_pyproject(pyproject_path)
        if py_name:
            project_name = py_name
        raw_entries.extend(py_entries)

    requirements_path = manifest_dir / "requirements.txt"
    if requirements_path.is_file() and "requirements.txt" not in unreadable:
        raw_entries.extend(_parse_requirements(requirements_path))

    package_json_path = manifest_dir / "package.json"
    if package_json_path.is_file() and "package.json" not in unreadable:
        js_name, js_entries = _parse_package_json(package_json_path)
        if not project_name and js_name:
            project_name = js_name
        raw_entries.extend(js_entries)

    packages = _dedupe_and_sort(raw_entries)
    for pkg in packages:
        pkg["summary"] = _resolve_summary(manifest_dir, pkg["registry"], pkg["name"])

    result = {
        "project": project_name or manifest_dir.name,
        "packages": packages,
    }
    if unreadable:
        result["unreadable"] = unreadable
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
    return 1 if found.get("unreadable") else 0


if __name__ == "__main__":
    sys.exit(main())
