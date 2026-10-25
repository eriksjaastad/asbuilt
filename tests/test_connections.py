import json
import os
import sys
import unittest
from unittest import mock

from asbuilt.connections import ClaudeCLI, ConnectionFailed, stopped


def py(code, timeout=30):  # stands in for claude: tests never run it
    return ClaudeCLI((sys.executable, "-c", f"import json, os, sys\n{code}"), timeout)


class ClaudeCLITest(unittest.TestCase):
    def test_args_and_stdin_reach_the_cli_with_only_the_kept_environment(self):
        said = "print(json.dumps([sys.argv[1:], sys.stdin.read(), list(os.environ)]))"
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "1", "LANG": "C"}):
            argv, stdin, env = json.loads(py(said).run(["a", ""], "é \ud800").stdout)
        self.assertEqual((argv, stdin), (["a", ""], "é ?"))
        self.assertEqual({"LANG", "CLAUDE_CODE_USE_BEDROCK"} & set(env), {"LANG"})
        self.assertEqual((ClaudeCLI().command, ClaudeCLI().timeout), (["claude"], 300))

    def test_a_cli_that_cannot_start_or_finish_is_one_line(self):
        for cli, said in [
            (ClaudeCLI(("/no/such/claude",)), "was not found: install Claude Code."),
            (ClaudeCLI((os.devnull,)), "cannot be started: Permission denied."),
            (py("import time; time.sleep(5)", 0.5), "gave no reply in 0.5 s."),
        ]:
            with self.subTest(said), self.assertRaises(ConnectionFailed) as caught:
                cli.run([], "")
            self.assertEqual(str(caught.exception), f"claude {said}")

    def test_a_failed_run_is_named_by_the_last_line_it_printed(self):
        for code, said in [
            ("print('out'); sys.stderr.write('one\\ntwo'); sys.exit(2)", "2: two"),
            ("print('Bad key'); sys.exit(3)", "3: Bad key"),
            ("print('x' * 300); sys.exit(4)", "4: " + "x" * 200),
            ("sys.exit(5)", "5: no message"),
        ]:
            with self.subTest(code):
                self.assertEqual(
                    str(stopped(py(code).run([], ""))), f"claude stopped, exit {said}."
                )
