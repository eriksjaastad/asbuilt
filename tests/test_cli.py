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
import time
import unittest
from urllib.request import urlopen

from asbuilt.cli import NO_PLAN, main


def serve_visit_interrupt(**kwargs):
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
        code = main(["--port", "0"], **kwargs)
    return code, seen


def fail_to_start(port):
    with contextlib.redirect_stderr(io.StringIO()) as err:
        return main(["--port", port]), err.getvalue()


class CliTest(unittest.TestCase):
    def test_it_serves_the_page_and_a_visible_error_message_until_interrupted(self):
        code, seen = serve_visit_interrupt()
        self.assertEqual([code, seen[0]], [0, 200])
        self.assertEqual(seen[1], f"data: {NO_PLAN}\n".encode())
        self.assertIs(json.loads(seen[1][6:])["ok"], False)

    def test_a_given_build_function_supplies_the_message(self):
        message = json.dumps({"ok": True, "plan": {"id": "root"}})
        self.assertEqual(
            serve_visit_interrupt(build=lambda: message)[1][1],
            f"data: {message}\n".encode(),
        )

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
