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
from unittest import mock
from urllib.request import urlopen

from asbuilt import cli, plan
from asbuilt.cli import main

ONE = '{"id": "root", "children": [{"id": "one"}]}'
TWO = '{"id": "root", "children": [{"id": "two"}]}'


def data(events):
    return next(
        line for line in iter(events.readline, b"") if line.startswith(b"data:")
    )


def serve_visit_interrupt(*argv, then=None, until=b""):
    out, seen, servers = io.StringIO(), [], []

    class Capture(cli.Server):
        def __init__(self, *args):
            super().__init__(*args)
            servers.append(self)

    def visit():
        try:
            deadline = time.monotonic() + 5
            while not (found := re.search(r"http://127\.0\.0\.1:\d+/", out.getvalue())):
                assert time.monotonic() < deadline, "no address printed"
                time.sleep(0.01)
            with (
                urlopen(found[0]) as page,
                urlopen(found[0] + "events", timeout=10) as events,
            ):
                seen.extend([page.status, data(events)])
                then and then()
                while until not in seen[-1]:
                    seen.append(data(events))
        except Exception as exc:  # shown by the assertions
            seen.append(repr(exc))
        finally:
            servers[0].shutdown()  # ends serve_forever, no signal needed

    threading.Thread(target=visit, daemon=True).start()
    with contextlib.redirect_stdout(out), mock.patch.object(cli, "Server", Capture):
        code = main([*argv, "--port", "0"])
    return code, seen


def fail_to_start(port):
    with contextlib.redirect_stderr(io.StringIO()) as err:
        return main(["--port", port]), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.plan = Path(self.enterContext(tempfile.TemporaryDirectory())) / "plan.json"

    def test_a_missing_or_bad_plan_still_serves_the_page_with_the_reason(self):
        for text, why in ((None, "was not found."), ("{", "is not valid JSON: ")):
            if text:
                self.plan.write_text(text)
            code, seen = serve_visit_interrupt(str(self.plan))
            message = json.loads(seen[1][6:])
            self.assertEqual([code, seen[0], message["ok"]], [0, 200, False])
            self.assertTrue(message["error"].startswith(f"plan.json {why}"))

    def test_a_save_redraws_the_page(self):
        self.plan.write_text(ONE)
        code, seen = serve_visit_interrupt(
            str(self.plan), then=lambda: self.plan.write_text(TWO), until=b'"two"'
        )
        self.assertEqual([code, seen[0]], [0, 200])
        self.assertEqual(json.loads(seen[1][6:])["plan"], json.loads(ONE))
        self.assertEqual(json.loads(seen[-1][6:])["plan"], json.loads(TWO))

    def test_a_save_made_before_the_watch_looks_is_not_lost(self):
        self.plan.write_text(ONE)
        stops, real_watch, saved = [], cli.watch, self.plan

        class SavedWhileStarting(
            cli.Hub
        ):  # built after the first look, before the watch
            def __init__(self, build):
                super().__init__(build)
                saved.write_text(TWO)

        def watch(path, on_change, stop, shown):
            stops.append(stop)
            real_watch(path, on_change, stop, shown)

        redraws = mock.patch.object(
            cli.Hub, "refresh", autospec=True, side_effect=cli.Hub.refresh
        )
        with (
            mock.patch.object(cli, "Hub", SavedWhileStarting),
            mock.patch.object(cli, "watch", watch),
            redraws as refresh,
        ):
            _, seen = serve_visit_interrupt(str(self.plan), until=b'"two"')
        refresh.assert_called_once()  # reported once, not at every look
        self.assertEqual(json.loads(seen[1][6:])["plan"], json.loads(ONE))
        self.assertEqual(json.loads(seen[-1][6:])["plan"], json.loads(TWO))
        self.assertTrue(stops[0].is_set())

    def test_the_page_shows_the_snapshot_the_watch_settled_on_not_a_reread(self):
        self.plan.write_text(ONE)

        def settled(path, on_change, stop, shown):
            on_change(TWO.encode())  # the file itself still holds ONE

        with mock.patch.object(cli, "watch", settled):
            _, seen = serve_visit_interrupt(str(self.plan), until=b'"two"')
        self.assertEqual(json.loads(seen[-1][6:])["plan"], json.loads(TWO))

    def test_ctrl_c_ends_the_command_cleanly_and_closes_the_socket(self):
        closing = mock.patch.object(
            cli.Server,
            "server_close",
            autospec=True,
            side_effect=cli.Server.server_close,
        )
        with (
            mock.patch.object(
                cli.Server, "serve_forever", side_effect=KeyboardInterrupt
            ),
            closing as close,
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--port", "0"]), 0)
        close.assert_called_once()

    def test_a_watch_that_fails_ends_the_command_with_the_reason(self):
        codes = []
        runner = threading.Thread(
            target=lambda: codes.append(main(["--port", "0"])), daemon=True
        )
        failing = mock.patch.object(cli, "watch", side_effect=RuntimeError("boom"))
        with (
            failing,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            runner.start()
            runner.join(10)  # a command that never ends fails here instead of hanging
        self.assertEqual(codes, [1])
        self.assertIn("watch of asbuilt/plan.json failed", err.getvalue())
        self.assertIn("RuntimeError: boom", err.getvalue())

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

    def test_port_80_is_refused_before_it_binds_with_a_reason_and_exit_code_2(self):
        # Its own process: a bind that wrongly succeeded times out instead of hanging the suite.
        run = subprocess.run(
            [sys.executable, "-m", "asbuilt", "--port", "80"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn(
            "cannot start on 127.0.0.1:80: browsers leave port 80 out", run.stderr
        )

    def test_the_help_gives_the_defaults_and_an_example_plan_it_reads(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertRaises(SystemExit, main, ["--help"])
        text = out.getvalue().replace("\n", " ")
        self.assertIn("default asbuilt/plan.json", text)
        self.assertIn("default 8765", text)
        example = cli.USAGE[cli.USAGE.index("{") :].encode()
        self.assertIn(cli.USAGE.strip(), out.getvalue())  # verbatim, example included
        self.assertEqual(plan.parse(example, "example")["edges"][0]["id"], "e1")


class EntryTest(unittest.TestCase):
    def test_the_asbuilt_command_runs_main(self):
        [entry] = importlib.metadata.entry_points(
            group="console_scripts", name="asbuilt"
        )
        self.assertIs(entry.load(), main)

    def test_the_address_shows_at_once_when_the_output_is_a_pipe(self):
        command = [sys.executable, "-m", "asbuilt", "--port", "0"]
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        ) as run:
            first = []
            reader = threading.Thread(
                target=lambda: first.append(run.stdout.readline())
            )
            reader.start()
            reader.join(10)
            run.kill()  # it serves until stopped
            reader.join()
        self.assertRegex(first[0], r"^asbuilt: drawing .+ at http://127\.0\.0\.1:\d+/$")

    def test_python_dash_m_runs_main_and_passes_its_exit_code_on(self):
        run = subprocess.run(
            [sys.executable, "-m", "asbuilt", "--port", "65536"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn("cannot start on 127.0.0.1:65536", run.stderr)
