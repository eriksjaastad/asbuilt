import _thread
import contextlib
import importlib.metadata
import io
import json
import re
import socket
import subprocess
import sys
import threading
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

from asbuilt.cli import main


def serve_visit_interrupt(*argv):
    out, seen = io.StringIO(), []

    def visit():
        try:
            deadline = time.monotonic() + 5
            while not (found := re.search(r"http://127\.0\.0\.1:\d+/", out.getvalue())):
                assert time.monotonic() < deadline, "no address printed"
                time.sleep(0.01)
            with urlopen(found[0]) as page, urlopen(found[0] + "events") as events:
                seen.extend([page.status, events.readline()])
        except Exception as exc:  # shown by the assertions
            seen.append(repr(exc))
        finally:
            _thread.interrupt_main()

    threading.Thread(target=visit, daemon=True).start()
    with contextlib.redirect_stdout(out):
        code = main([*argv, "--port", "0"])
    return code, seen


def fail_to_start(port):
    with contextlib.redirect_stderr(io.StringIO()) as err:
        return main(["--port", port]), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.plan = Path(self.enterContext(tempfile.TemporaryDirectory())) / "plan.json"

    def test_it_serves_the_page_and_the_plan_in_the_file_until_interrupted(self):
        self.plan.write_text('{"id": "root", "children": [{"id": "a"}]}')
        code, seen = serve_visit_interrupt(str(self.plan))
        self.assertEqual([code, seen[0]], [0, 200])
        plan = {"id": "root", "children": [{"id": "a"}]}
        self.assertEqual(json.loads(seen[1][6:]), {"ok": True, "plan": plan})

    def test_a_missing_or_bad_plan_still_serves_the_page_with_the_reason(self):
        for text, why in ((None, "was not found."), ("{", "is not valid JSON: ")):
            if text:
                self.plan.write_text(text)
            code, seen = serve_visit_interrupt(str(self.plan))
            message = json.loads(seen[1][6:])
            self.assertEqual([code, seen[0], message["ok"]], [0, 200, False])
            self.assertTrue(message["error"].startswith(f"plan.json {why}"))

    def test_the_default_plan_is_asbuilt_plan_json(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertRaises(SystemExit, main, ["--help"])
        self.assertIn("default asbuilt/plan.json", out.getvalue().replace("\n", " "))

    def test_a_taken_port_ends_with_a_message_that_names_it_and_exit_code_2(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
            code, text = fail_to_start(str(port))
        self.assertEqual(code, 2)
        self.assertRegex(text, rf"on 127\.0\.0\.1:{port}: .*in use")

    def test_a_port_out_of_range_ends_the_same_way(self):
        code, text = fail_to_start("65536")
        self.assertEqual(code, 2)
        self.assertIn("cannot start on 127.0.0.1:65536: ", text)

    def test_the_default_port_is_8765(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertRaises(SystemExit, main, ["--help"])
        self.assertIn("default 8765", out.getvalue())


class EntryTest(unittest.TestCase):
    def test_the_asbuilt_command_runs_main(self):
        [entry] = importlib.metadata.entry_points(
            group="console_scripts", name="asbuilt"
        )
        self.assertIs(entry.load(), main)

    def test_python_dash_m_runs_main_and_passes_its_exit_code_on(self):
        run = subprocess.run(
            [sys.executable, "-m", "asbuilt", "--port", "65536"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn("cannot start on 127.0.0.1:65536", run.stderr)
