import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from asbuilt.packages import find, main
from asbuilt.state import PlanError


class PackagesTest(unittest.TestCase):
    def test_pyproject_dependencies_optional_and_dependency_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "demo-app"
dependencies = [
    "uvicorn[standard]>=0.29.0",
    "requests>=2.31,<3; python_version >= '3.11'",
    "flask",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff==0.5.0"]
docs = ["mkdocs>=1.6"]
postgres = ["psycopg[binary]>=3.1"]

[dependency-groups]
lint = [{include-group = "dev"}, "mypy>=1.10"]
""",
                encoding="utf-8",
            )
            result = find(root)
            self.assertEqual(result["project"], "demo-app")
            by_name = {pkg["name"]: pkg for pkg in result["packages"]}
            self.assertEqual(
                set(by_name),
                {"flask", "mkdocs", "mypy", "psycopg", "pytest", "requests", "ruff", "uvicorn"},
            )
            self.assertEqual(
                by_name["uvicorn"],
                {
                    "name": "uvicorn",
                    "spec": ">=0.29.0",
                    "registry": "pypi",
                    "url": "https://pypi.org/project/uvicorn/",
                    "dev": False,
                    "tags": [],
                    "summary": "",
                },
            )
            self.assertEqual(by_name["requests"]["spec"], ">=2.31,<3")
            self.assertFalse(by_name["requests"]["dev"])
            self.assertEqual(by_name["flask"]["spec"], "")
            self.assertFalse(by_name["psycopg"]["dev"])
            self.assertTrue(by_name["pytest"]["dev"])
            self.assertTrue(by_name["ruff"]["dev"])
            self.assertTrue(by_name["mkdocs"]["dev"])
            self.assertTrue(by_name["mypy"]["dev"])

    def test_requirements_txt_ignores_comments_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "my-script")
            root.mkdir()
            (root / "requirements.txt").write_text(
                """
# Top comment
-r base.txt
--index-url https://pypi.org/simple
-e ./local-pkg
https://example.com/pkg-1.0.whl

httpx>=0.27  # inline comment
pydantic[email]==2.8.0 ; python_version >= "3.10"
httpx
""",
                encoding="utf-8",
            )
            result = find(root)
            self.assertEqual(result["project"], "my-script")
            self.assertEqual(
                result["packages"],
                [
                    {
                        "name": "httpx",
                        "spec": ">=0.27",
                        "registry": "pypi",
                        "url": "https://pypi.org/project/httpx/",
                        "dev": False,
                        "tags": [],
                        "summary": "",
                    },
                    {
                        "name": "pydantic",
                        "spec": "==2.8.0",
                        "registry": "pypi",
                        "url": "https://pypi.org/project/pydantic/",
                        "dev": False,
                        "tags": [],
                        "summary": "",
                    },
                ],
            )

    def test_package_json_including_scoped_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@acme/web",
                        "dependencies": {
                            "react": "^18.3.0",
                            "@tanstack/react-query": "^5.0.0",
                        },
                        "devDependencies": {
                            "@types/node": "^22.0.0",
                            "vitest": "^2.0.0",
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = find(root)
            self.assertEqual(result["project"], "@acme/web")
            self.assertEqual(
                result["packages"],
                [
                    {
                        "name": "@tanstack/react-query",
                        "spec": "^5.0.0",
                        "registry": "npm",
                        "url": "https://www.npmjs.com/package/@tanstack/react-query",
                        "dev": False,
                        "tags": [],
                        "summary": "",
                    },
                    {
                        "name": "react",
                        "spec": "^18.3.0",
                        "registry": "npm",
                        "url": "https://www.npmjs.com/package/react",
                        "dev": False,
                        "tags": [],
                        "summary": "",
                    },
                    {
                        "name": "@types/node",
                        "spec": "^22.0.0",
                        "registry": "npm",
                        "url": "https://www.npmjs.com/package/@types/node",
                        "dev": True,
                        "tags": [],
                        "summary": "",
                    },
                    {
                        "name": "vitest",
                        "spec": "^2.0.0",
                        "registry": "npm",
                        "url": "https://www.npmjs.com/package/vitest",
                        "dev": True,
                        "tags": [],
                        "summary": "",
                    },
                ],
            )

    def test_mixed_python_and_js_repo_dedupes_and_orders_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "fullstack"
dependencies = ["FastAPI>=0.111"]

[project.optional-dependencies]
dev = ["fastapi>=0.111", "pytest>=8"]
""",
                encoding="utf-8",
            )
            (root / "requirements.txt").write_text("fastapi\nclick>=8.1\n", encoding="utf-8")
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "fullstack-ui",
                        "dependencies": {"zod": "^3.23.0"},
                        "devDependencies": {"zod": "^3.23.0", "eslint": "^9.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            result = find(root)
            self.assertEqual(result["project"], "fullstack")
            self.assertEqual(
                [(p["registry"], p["name"], p["dev"]) for p in result["packages"]],
                [
                    ("npm", "zod", False),
                    ("pypi", "click", False),
                    ("pypi", "FastAPI", False),
                    ("npm", "eslint", True),
                    ("pypi", "pytest", True),
                ],
            )

    def test_relative_paths_nested_file_and_git_root_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp)
            (outer / "pyproject.toml").write_text('[project]\nname = "outer"\ndependencies = ["outerpkg"]\n', encoding="utf-8")
            inner = outer / "inner-repo"
            (inner / ".git").mkdir(parents=True)
            nested = inner / "src" / "app"
            nested.mkdir(parents=True)
            target_file = nested / "main.py"
            target_file.write_text("print('hi')\n", encoding="utf-8")

            # Inside inner-repo (no manifests, has .git), must NOT wander up to outer.
            stopped = find(target_file)
            self.assertEqual(stopped["packages"], [])

            # Now add a manifest inside inner-repo and query via relative path from nested dir.
            (inner / "requirements.txt").write_text("rich>=13.0\n", encoding="utf-8")
            prev = Path.cwd()
            try:
                os.chdir(nested)
                rel_result = find(Path("main.py"))
            finally:
                os.chdir(prev)
            self.assertEqual(rel_result["project"], "inner-repo")
            self.assertEqual([p["name"] for p in rel_result["packages"]], ["rich"])

    def test_missing_manifests_and_nonexistent_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp, "empty-proj")
            (empty / ".git").mkdir(parents=True)
            self.assertEqual(find(empty), {"project": "empty-proj", "packages": [], "read": [], "not_read": []})
            missing = Path(tmp, "does-not-exist")
            with self.assertRaisesRegex(PlanError, "does-not-exist: no such file or folder"):
                find(missing)
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(missing)]), 2)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("no such file or folder", err.getvalue())

    def test_optional_and_peer_npm_dependencies_are_listed_and_tagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {"react": "^18"},
                        "peerDependencies": {"react": "^18", "react-dom": "^18"},
                        "optionalDependencies": {"fsevents": "^2"},
                    }
                ),
                encoding="utf-8",
            )
            by_name = {p["name"]: p for p in find(root)["packages"]}
            self.assertEqual(set(by_name), {"react", "react-dom", "fsevents"})
            self.assertEqual(by_name["fsevents"]["tags"], ["optional"])
            self.assertEqual(by_name["react-dom"]["tags"], ["peer"])
            self.assertEqual(by_name["react"]["tags"], ["peer"])
            self.assertFalse(any(p["dev"] for p in by_name.values()))

    def test_poetry_and_dependency_groups_are_read_and_the_rest_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                """
[tool.poetry.dependencies]
python = "^3.12"
requests = "^2.31"
local-lib = {path = "../lib"}
httpx = {version = "^0.27", extras = ["http2"]}

[tool.poetry.group.dev.dependencies]
pytest = "^8"

[dependency-groups]
lint = ["ruff>=0.5", {include-group = "test"}]
test = ["coverage"]

[tool.uv]
dev-dependencies = ["mypy"]
""",
                encoding="utf-8",
            )
            (root / "requirements.txt").write_text(
                "\ufeffflask>=3\n-r requirements/base.txt\n-e git+https://x.example/p.git#egg=p\nweird @ https://x.example/w.whl\n",
                encoding="utf-8",
            )
            result = find(root)
            by_name = {p["name"]: p for p in result["packages"]}
            self.assertEqual(result["read"], ["pyproject.toml", "requirements.txt"])
            self.assertNotIn("python", by_name)
            self.assertEqual(by_name["requests"]["tags"], ["poetry"])
            self.assertEqual(by_name["httpx"]["spec"], "^0.27")
            self.assertIsNone(by_name["local-lib"]["url"])
            self.assertTrue(by_name["pytest"]["dev"])
            self.assertEqual(by_name["pytest"]["tags"], ["poetry group dev"])
            self.assertEqual(by_name["ruff"]["tags"], ["group lint"])
            self.assertIn("coverage", by_name)
            self.assertIn("flask", by_name)  # a BOM does not hide the first line
            self.assertIsNone(by_name["weird"]["url"])  # a direct reference has no PyPI page
            self.assertEqual(
                result["not_read"],
                [
                    "pyproject.toml [tool.uv.dev-dependencies] is not read",
                    "requirements.txt: -r requirements/base.txt",
                    "requirements.txt: -e git+https://x.example/p.git#egg=p",
                ],
            )

    def test_non_registry_npm_dependencies_get_no_link_and_an_alias_links_its_real_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "react": "^18",
                            "react-v17": "npm:react@^17",
                            "scoped-alias": "npm:@scope/pkg@^1",
                            "local": "file:../local",
                            "ws-pkg": "workspace:*",
                            "gitdep": "git+https://x.example/r.git",
                            "gh": "user/repo",
                            "tarball": "https://x.example/t.tgz",
                        },
                        "workspaces": ["packages/*"],
                    }
                ),
                encoding="utf-8",
            )
            result = find(root)
            urls = {p["name"]: p["url"] for p in result["packages"]}
            self.assertEqual(urls["react"], "https://www.npmjs.com/package/react")
            self.assertEqual(urls["react-v17"], "https://www.npmjs.com/package/react")
            self.assertEqual(urls["scoped-alias"], "https://www.npmjs.com/package/@scope/pkg")
            for name in ("local", "ws-pkg", "gitdep", "gh", "tarball"):
                self.assertIsNone(urls[name], name)
            self.assertEqual(result["read"], ["package.json"])
            self.assertEqual(result["not_read"], ["package.json workspaces: each workspace's own package.json is not read"])

    def test_a_name_that_is_not_a_plain_package_name_never_reads_outside_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp, "secret")
            outside.mkdir()
            (outside / "package.json").write_text(json.dumps({"description": "OUTSIDE"}), encoding="utf-8")
            root = Path(tmp, "proj")
            (root / "node_modules").mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"../../secret": "1", "/abs": "1", "ok-pkg": "1"}}),
                encoding="utf-8",
            )
            by_name = {p["name"]: p for p in find(root)["packages"]}
            self.assertEqual(by_name["../../secret"]["summary"], "")
            self.assertIsNone(by_name["../../secret"]["url"])
            self.assertIsNone(by_name["/abs"]["url"])
            self.assertEqual(by_name["ok-pkg"]["url"], "https://www.npmjs.com/package/ok-pkg")

    def test_malformed_and_unreadable_manifests_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "broken")
            root.mkdir()
            (root / "pyproject.toml").write_text("not = [valid toml", encoding="utf-8")
            (root / "package.json").write_text("{not valid json", encoding="utf-8")
            (root / "requirements.txt").write_bytes(b"\xff\xfe\x00invalid-utf8")
            result = find(root)
            self.assertEqual(
                result,
                {
                    "project": "broken",
                    "packages": [],
                    "read": [],
                    "not_read": [],
                    "unreadable": ["package.json", "pyproject.toml", "requirements.txt"],
                },
            )
            # A non-object JSON root is unreadable too; a good manifest beside it still counts.
            (root / "package.json").write_text('["not", "an", "object"]', encoding="utf-8")
            (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
            result = find(root)
            self.assertEqual([p["name"] for p in result["packages"]], ["requests"])
            self.assertEqual(result["unreadable"], ["package.json", "pyproject.toml"])
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(root)]), 1)
            self.assertIn("could not read package.json", err.getvalue())

    def test_cli_prints_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"name": "cli-test", "dependencies": {"lodash": "^4.17.21"}}),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main([str(root)])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["project"], "cli-test")
            self.assertEqual(len(payload["packages"]), 1)
            self.assertEqual(payload["packages"][0]["name"], "lodash")
            self.assertEqual(payload["packages"][0]["summary"], "")

    def test_summary_only_from_local_metadata_else_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "summary-demo"
dependencies = [
    "tree-sitter>=0.23",
    "custom_local_pkg>=1.0",
    "totally-unknown-pypi-pkg>=0.1",
]
""",
                encoding="utf-8",
            )
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "summary-demo",
                        "dependencies": {
                            "elkjs": "^0.9.3",
                            "@scope/local-js": "^1.0.0",
                            "totally-unknown-npm-pkg": "^0.0.1",
                        },
                    }
                ),
                encoding="utf-8",
            )

            # Local node_modules/<pkg>/package.json description (including multi-line and >160 chars)
            nm_dir = root / "node_modules" / "@scope" / "local-js"
            nm_dir.mkdir(parents=True)
            long_desc = "Local JS package   with\nmultiple   spaces and lines that " + ("x" * 140)
            (nm_dir / "package.json").write_text(
                json.dumps({"name": "@scope/local-js", "description": long_desc}),
                encoding="utf-8",
            )

            # Local .venv/lib/python3.13/site-packages/<norm_name>-*.dist-info/METADATA
            dist_info = (
                root
                / ".venv"
                / "lib"
                / "python3.13"
                / "site-packages"
                / "custom_local_pkg-1.0.0.dist-info"
            )
            dist_info.mkdir(parents=True)
            (dist_info / "METADATA").write_text(
                "Metadata-Version: 2.1\n"
                "Name: custom-local-pkg\n"
                "Version: 1.0.0\n"
                "Summary:   Local Python wheel summary from dist-info METADATA.  \n"
                "\n"
                "Body text after headers.\n",
                encoding="utf-8",
            )

            result = find(root)
            by_name = {pkg["name"]: pkg for pkg in result["packages"]}

            # 1. Local node_modules description trimmed, single-line, capped at 160 chars
            local_js_summary = by_name["@scope/local-js"]["summary"]
            self.assertTrue(local_js_summary.startswith("Local JS package with multiple spaces and lines"))
            self.assertLessEqual(len(local_js_summary), 160)
            self.assertTrue(local_js_summary.endswith("..."))

            # 2. Local .venv dist-info METADATA Summary header
            self.assertEqual(
                by_name["custom_local_pkg"]["summary"],
                "Local Python wheel summary from dist-info METADATA.",
            )

            # 3. A well-known package with no metadata installed in this project has no summary:
            # asBuilt shows only what it reads.
            self.assertEqual(by_name["tree-sitter"]["summary"], "")
            self.assertEqual(by_name["elkjs"]["summary"], "")

            # 4. Unknown packages return empty string (never guessing)
            self.assertEqual(by_name["totally-unknown-pypi-pkg"]["summary"], "")
            self.assertEqual(by_name["totally-unknown-npm-pkg"]["summary"], "")


if __name__ == "__main__":
    unittest.main()
