import contextlib
import http.client
import io
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError
from urllib.request import urlopen

from asbuilt import cli
from asbuilt.overview import build, main
from asbuilt.server import OverviewServer
from asbuilt.state import PlanError

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ASBUILT = REPO_ROOT / "src" / "asbuilt"

BOX_KEYS = {
    "id",
    "file",
    "name",
    "lines",
    "plain",
    "proven",
    "badges",
    "security",
    "text",
    "rest",
    "calls",
}


def write(root, files):
    """Write {relative path: text} under root, making folders."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def ids_resolve(test, info, boxes):
    """Every member, root, shared member and no-path id in a component list is a charted box."""
    for item in info["components"]:
        for root_id in item["roots"]:
            test.assertIn(root_id, boxes)
        for member in item["members"]:
            test.assertIn(member["id"], boxes)
    for shared_box in info["shared"]:
        for member in shared_box["members"]:
            test.assertIn(member["id"], boxes)
    for member in info["no_path_found"]:
        test.assertIn(member["id"], boxes)


SERVER_PY = 'import os\n\ndef serve():\n    return os.environ.get("PORT", "8000")\n\nif __name__ == "__main__":\n    serve()\n'
API_TS = 'export async function fetchStatus() {\n  return fetch("/status");\n}\n'
MAIN_TS = 'import { fetchStatus } from "./api";\n\nexport function initApp() {\n  return fetchStatus();\n}\n'


class OverviewTest(unittest.TestCase):
    def test_asbuilt_repo_overview(self):
        result = build(SRC_ASBUILT)
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)
        self.assertEqual(result["project"], "asbuilt")
        self.assertTrue(any(p["registry"] == "pypi" and p["url"].startswith("https://pypi.org/") for p in result["packages"]))
        self.assertEqual(result["packages_unreadable"], [])
        self.assertIsNone(result["js_error"])
        comp = result["components"]
        self.assertTrue(comp["groups"])
        self.assertTrue(comp["components"])
        boxes = result["boxes"]
        for box_id, box in boxes.items():
            self.assertEqual(box["id"], box_id)
            self.assertEqual(set(box), BOX_KEYS)
            self.assertEqual(len(box["lines"]), 2)
            for callee in box["calls"]:
                self.assertIn(callee, boxes)
        ids_resolve(self, comp, boxes)
        ids_resolve(self, result["js_components"], boxes)
        file_ids = {f["id"] for f in result["files"]}
        self.assertTrue({"built.py", "components.py", "page/overview.js"} <= file_ids)

    def test_asbuilt_repo_root_charts_its_code_and_never_its_tests(self):
        res = build(REPO_ROOT)
        self.assertEqual(res["project"], "asbuilt")
        file_ids = {f["id"] for f in res["files"]}
        expected = {p.relative_to(REPO_ROOT).as_posix() for p in SRC_ASBUILT.glob("*.py")}
        expected |= {p.relative_to(REPO_ROOT).as_posix() for p in (SRC_ASBUILT / "page").glob("*.js")}
        self.assertTrue(expected <= file_ids, expected - file_ids)
        for fid in file_ids:
            self.assertFalse(fid.startswith("tests/"), fid)
            self.assertNotIn("vendor/", fid)
        self.assertTrue({"commands", "scripts"} <= {g["kind"] for g in res["components"]["groups"]})
        ids_resolve(self, res["components"], res["boxes"])
        ways = [w for c in res["js_components"]["components"] if c["kind"] == "web pages" for w in c["ways_in"]]
        self.assertTrue(any("src/asbuilt/page/overview.js" in w for w in ways))
        names = {p["name"] for p in res["packages"]}
        self.assertTrue({"tree-sitter", "elkjs", "roughjs"} <= names)

    def test_mixed_project_charts_both_languages_from_one_python_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "mixed-app")
            write(root, {
                "pyproject.toml": '[project]\nname = "mixed-app"\ndependencies = ["flask>=3.0"]\n',
                "package.json": json.dumps({"name": "mixed-app-ui", "dependencies": {"d3": "^7.0.0"}}),
                "server.py": SERVER_PY,
                "frontend/api.ts": API_TS,
                "frontend/main.ts": MAIN_TS,
                "frontend/broken.js": "function oops( {\n",
            })
            result = build(root)
            self.assertEqual(result["project"], "mixed-app")
            self.assertIsNone(result["js_error"])
            self.assertEqual([c["name"] for c in result["components"]["components"]], ["server"])
            self.assertIsInstance(result["js_components"], dict)
            js_ids = {m["id"] for c in result["js_components"]["components"] for m in c["members"]}
            js_ids |= {m["id"] for m in result["js_components"]["no_path_found"]}
            self.assertEqual(js_ids, {"frontend/api.ts:fetchStatus:1", "frontend/main.ts:initApp:3"})
            self.assertEqual(result["not_read"], ["frontend/broken.js: not parseable as JavaScript/TypeScript"])
            by_file = {f["id"]: f for f in result["files"]}
            self.assertEqual(by_file["frontend/main.ts"]["imports"], ["frontend/api.ts"])
            boxes = result["boxes"]
            self.assertIn("calls out", boxes["frontend/api.ts:fetchStatus:1"]["badges"])
            self.assertEqual(boxes["frontend/main.ts:initApp:3"]["calls"], ["frontend/api.ts:fetchStatus:1"])
            ids_resolve(self, result["components"], boxes)
            ids_resolve(self, result["js_components"], boxes)

    def test_a_javascript_reader_failure_beside_python_is_named_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "mixed")
            write(root, {"server.py": SERVER_PY, "web/broken.js": "function oops( {\n"})
            result = build(root)
            self.assertEqual([c["name"] for c in result["components"]["components"]], ["server"])
            self.assertIsNone(result["js_components"])
            self.assertIn("no parseable JavaScript/TypeScript files", result["js_error"])
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(main([str(root)]), 0)
            self.assertIn("JavaScript/TypeScript not charted", err.getvalue())

    def test_python_project_names_unparsed_files_and_skipped_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "demo")
            write(root, {
                "pyproject.toml": '[project]\nname = "demo-py"\ndependencies = ["click>=8.1"]\n\n[project.scripts]\ndemo = "cli:run"\n',
                "tests/test_ignore.py": "def test_x():\n    pass\n",
                "test_top.py": "def test_y():\n    pass\n",
                "sub/__init__.py": "",
                "sub/util.py": "def shared_helper():\n    return 1\n\ndef orphan_fn():\n    return 2\n",
                "cli.py": "from sub.util import shared_helper\n\ndef run():\n    return shared_helper()\n",
                "script.py": 'from sub.util import shared_helper\n\ndef main():\n    return shared_helper()\n\nif __name__ == "__main__":\n    main()\n',
                "broken.py": "def oops(:\n",
            })
            result = build(root)
            self.assertEqual(result["project"], "demo-py")
            self.assertEqual([p["name"] for p in result["packages"]], ["click"])
            self.assertEqual(result["skipped"], ["tests"])
            self.assertEqual(len(result["not_read"]), 1)
            self.assertTrue(result["not_read"][0].startswith("broken.py: "), result["not_read"])
            comp = result["components"]
            self.assertEqual({g["kind"] for g in comp["groups"]}, {"commands", "scripts"})
            self.assertEqual(len(comp["shared"]), 1)
            self.assertEqual([m["name"] for m in comp["no_path_found"]], ["orphan_fn()"])
            ids_resolve(self, comp, result["boxes"])
            file_ids = {f["id"] for f in result["files"]}
            self.assertNotIn("test_top.py", file_ids)
            self.assertNotIn("tests/test_ignore.py", file_ids)
            self.assertFalse(any(b.startswith("test_top.py") for b in result["boxes"]))
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(main([str(root)]), 0)
            self.assertIn("could not read broken.py", err.getvalue())

    def test_single_python_file_and_a_test_file_named_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "solo")
            write(root, {
                "app.py": 'def helper():\n    return 42\n\ndef entry():\n    return helper()\n\nif __name__ == "__main__":\n    entry()\n',
                "test_app.py": "def test_entry():\n    pass\n",
            })
            result = build(root / "app.py")
            self.assertEqual([f["id"] for f in result["files"]], ["app.py"])
            self.assertEqual(len(result["boxes"]), 2)
            with self.assertRaisesRegex(PlanError, "test files are not charted"):
                build(root / "test_app.py")

    def test_javascript_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "web-app")
            write(root, {
                "package.json": json.dumps({"name": "@acme/web-app", "bin": {"web": "./src/app.tsx"}, "dependencies": {"react": "^18.3.0"}}),
                "src/util.ts": "export function formatName(name: string): string {\n  return name.trim();\n}\n",
                "src/app.tsx": 'import { formatName } from "./util";\n\nexport function greet(who: string) {\n  return formatName(who);\n}\n',
                "src/broken.js": "function bad( {\n",
            })
            result = build(root)
            self.assertEqual(result["project"], "@acme/web-app")
            self.assertIsNone(result["components"])
            self.assertIn("no_path_found", result["js_components"])
            self.assertEqual(result["not_read"], ["src/broken.js: not parseable as JavaScript/TypeScript"])
            self.assertEqual([(p["name"], p["registry"]) for p in result["packages"]], [("react", "npm")])
            self.assertEqual({f["id"] for f in result["files"]}, {"src/app.tsx", "src/util.ts"})
            self.assertEqual(result["boxes"]["src/app.tsx:greet:3"]["calls"], ["src/util.ts:formatName:1"])
            ids_resolve(self, result["js_components"], result["boxes"])

    def test_single_typescript_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ts_file = Path(tmp, "index.ts")
            ts_file.write_text("function leaf(): number { return 1; }\nexport function top(): number { return leaf(); }\n", encoding="utf-8")
            result = build(ts_file)
            self.assertIsNone(result["components"])
            self.assertIsInstance(result["js_components"], dict)
            self.assertEqual(len(result["boxes"]), 2)

    def test_a_javascript_failure_with_no_python_fails_the_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "js")
            write(root, {"bad.js": "function (\n"})
            with self.assertRaisesRegex(PlanError, "no parseable"):
                build(root)

    def test_an_unreadable_manifest_is_in_the_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "proj")
            write(root, {"package.json": "{not json", "app.py": "def run():\n    return 1\n"})
            result = build(root)
            self.assertEqual(result["packages_unreadable"], ["package.json"])
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(main([str(root)]), 0)
            self.assertIn("could not read package.json", err.getvalue())

    def test_an_unreadable_or_mistyped_pyproject_is_named_and_the_rest_still_charts(self):
        for name, text, unreadable in (
            ("bad-toml", "[project\n", ["pyproject.toml"]),
            ("bad-utf8", b"\xff\xfe[project]\n", ["pyproject.toml"]),
            ("bad-scripts", '[project]\nname = "x"\n[project.scripts]\napp = 1\n', []),
        ):
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp, name)
                write(root, {"server.py": SERVER_PY, "web/api.ts": API_TS})
                if isinstance(text, bytes):
                    (root / "pyproject.toml").write_bytes(text)
                else:
                    (root / "pyproject.toml").write_text(text, encoding="utf-8")
                result = build(root)
                self.assertEqual(result["packages_unreadable"], unreadable)
                self.assertEqual([c["name"] for c in result["components"]["components"]], ["server"])
                self.assertIsNotNone(result["js_components"])
                self.assertTrue(
                    any("console scripts are not looked for" in e for e in result["components"]["not_proven"]),
                    result["components"]["not_proven"],
                )

    def test_entry_points_the_reader_could_not_follow_reach_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "setup-only")
            write(root, {"run.py": 'import os\n\nif __name__ == "__main__":\n    os.getcwd()\n', "lib.py": "def f():\n    return 1\n"})
            result = build(root)
            self.assertTrue(result["components"]["entries_reaching_nothing"])
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(main([str(root)]), 0)
            self.assertIn("Python entry point reaching no charted function", err.getvalue())

    def test_the_payload_names_the_manifests_read_and_what_they_hold_that_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "proj")
            write(root, {"requirements.txt": "flask\n-r base.txt\n", "app.py": "def run():\n    return 1\n"})
            result = build(root)
            self.assertEqual(result["packages_read"], ["requirements.txt"])
            self.assertEqual(result["packages_not_read"], ["requirements.txt: -r base.txt"])

    def test_missing_unsupported_and_empty_paths_raise_plan_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlanError):
                build(Path(tmp, "nope"))
            txt = Path(tmp, "readme.txt")
            txt.write_text("hello", encoding="utf-8")
            with self.assertRaises(PlanError):
                build(txt)
            with self.assertRaisesRegex(PlanError, "no Python or JavaScript/TypeScript files"):
                build(Path(tmp))

    def test_cli_failure_returns_exit_code_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main([str(Path(tmp, "missing-dir"))])
            self.assertEqual(code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("asbuilt.overview:", stderr.getvalue())


class OverviewServerTest(unittest.TestCase):
    def test_overview_server_serves_html_js_and_json_with_host_guard(self):
        payload = {
            "project": "demo",
            "packages": [],
            "components": None,
            "files": [],
            "boxes": {},
            "skipped": [],
        }
        try:
            server = OverviewServer(0, payload)
        except PermissionError:
            self.skipTest("sandbox blocks loopback sockets")
        thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        self.assertEqual(server.server_address[0], "127.0.0.1")

        def fetch(path, host=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=5)
            self.addCleanup(conn.close)
            try:
                conn.request("GET", path, headers={"Host": host} if host else {})
            except PermissionError:
                self.skipTest("sandbox blocks loopback sockets")
            return conn.getresponse()

        html_resp = fetch("/")
        self.assertEqual(html_resp.status, 200)
        self.assertEqual(html_resp.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(html_resp.getheader("Cache-Control"), "no-store")
        html_body = html_resp.read().decode("utf-8")
        self.assertIn('/overview.js', html_body)

        js_resp = fetch("/overview.js")
        self.assertEqual(js_resp.status, 200)
        self.assertEqual(js_resp.getheader("Content-Type"), "text/javascript; charset=utf-8")
        self.assertEqual(js_resp.getheader("Cache-Control"), "no-store")
        self.assertIn("renderOverview", js_resp.read().decode("utf-8"))

        json_resp = fetch("/overview.json")
        self.assertEqual(json_resp.status, 200)
        self.assertEqual(json_resp.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(json_resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(json.loads(json_resp.read().decode("utf-8")), payload)

        for missing in ("/events", "/index.html", "/nope"):
            resp = fetch(missing)
            self.assertEqual(resp.status, 404, missing)
            self.assertEqual(resp.getheader("Cache-Control"), "no-store")

        bad_host = fetch("/", host=f"evil.example:{server.server_port}")
        self.assertEqual(bad_host.status, 403)

    def test_cli_directory_mode_serves_overview_page_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "proj")
            root.mkdir()
            (root / "pyproject.toml").write_text(
                '[project]\nname = "proj"\n', encoding="utf-8"
            )
            (root / "app.py").write_text(
                'def run():\n    return 1\n\nif __name__ == "__main__":\n    run()\n',
                encoding="utf-8",
            )

            out, seen, servers = io.StringIO(), {}, []

            class CaptureOverview(OverviewServer):
                def __init__(self, *args):
                    super().__init__(*args)
                    servers.append(self)

            def visit():
                try:
                    deadline = time.monotonic() + 5
                    while not (
                        found := re.search(r"http://127\.0\.0\.1:\d+/", out.getvalue())
                    ):
                        assert time.monotonic() < deadline, "no address printed"
                        time.sleep(0.01)
                    base = found[0]
                    with urlopen(base, timeout=5) as page:
                        seen["html_status"] = page.status
                        seen["html"] = page.read().decode("utf-8")
                    with urlopen(base + "overview.json", timeout=5) as data_resp:
                        seen["json_status"] = data_resp.status
                        seen["payload"] = json.loads(data_resp.read().decode("utf-8"))
                except (PermissionError, URLError) as exc:
                    if isinstance(exc, PermissionError) or isinstance(
                        getattr(exc, "reason", None), PermissionError
                    ):
                        seen["sandbox_blocked"] = True
                    else:
                        seen["error"] = repr(exc)
                except Exception as exc:
                    seen["error"] = repr(exc)
                finally:
                    if servers:
                        servers[0].shutdown()

            threading.Thread(target=visit, daemon=True).start()
            try:
                with (
                    contextlib.redirect_stdout(out),
                    mock.patch.object(cli, "OverviewServer", CaptureOverview),
                ):
                    code = cli.main([str(root), "--port", "0"])
            except PermissionError:
                self.skipTest("sandbox blocks loopback sockets")

            if seen.get("sandbox_blocked"):
                self.skipTest("sandbox blocks loopback sockets")

            self.assertEqual(code, 0)
            self.assertNotIn("error", seen)
            self.assertEqual(seen["html_status"], 200)
            self.assertIn("/overview.js", seen["html"])
            self.assertEqual(seen["json_status"], 200)
            self.assertEqual(seen["payload"]["project"], "proj")
            self.assertEqual(seen["payload"]["components"]["components"][0]["name"], "app")

    def test_cli_empty_directory_returns_exit_code_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp, "empty")
            empty.mkdir()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = cli.main([str(empty), "--port", "0"])
            self.assertEqual(code, 2)
            self.assertIn("asbuilt:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
