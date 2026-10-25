import json
import os
import sys
import unittest
from unittest import mock

from asbuilt.connections import ClaudeCLI, ConnectionFailed, ask, stopped

NO = "^claude is not signed in to claude.ai: run claude auth login[.]$"
IN = (
    '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", "x": 1}'
)
SIGNED = f"if sys.argv[1] == 'auth': sys.exit(print({IN!r}))\n"
FLAGS = '-p --tools  --no-session-persistence --strict-mcp-config --safe-mode --settings {"disableAllHooks": true}'


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

    def test_only_an_exact_claude_ai_sign_in_is_taken(self):
        asked = "assert sys.argv[1:] == ['auth', 'status', '--json'] and not sys.stdin.read()\n"
        self.assertIsNone(py(f"{asked}print({IN!r})").check_signed_in())
        bad = [
            ("true", "1"),
            ("true", '"true"'),
            ("claude.ai", "key"),
            ("firstParty", "x"),
        ]
        outs = [repr(IN.replace(*b)) for b in bad] + ["'[]'", "'no'", "'[' * 300000"]
        for code in [f"print({IN!r}); sys.exit(1)", *(f"print({o})" for o in outs)]:
            with self.subTest(code[:40]), self.assertRaisesRegex(ConnectionFailed, NO):
                py(asked + code).check_signed_in()
        with self.assertRaisesRegex(ConnectionFailed, "^claude stopped, exit 2: x[.]$"):
            py("sys.stderr.write('x'); sys.exit(2)").check_signed_in()

    def test_a_prompt_goes_on_stdin_with_everything_off_once_signed_in(self):
        said = "print(json.dumps([' '.join(sys.argv[1:]), sys.stdin.read()]))"
        out = py(SIGNED + said)("draw")
        self.assertEqual((json.loads(out), out[-1]), ([FLAGS, "draw"], "\n"))
        for code, why in [
            ("sys.exit(print('x') or 3)", "stopped, exit 3: x"),
            ("print()", "replied with nothing"),
        ]:
            with self.subTest(why), self.assertRaises(ConnectionFailed) as caught:
                py(SIGNED + code)("draw")
            self.assertEqual(str(caught.exception), f"claude {why}.")
        with self.assertRaisesRegex(ConnectionFailed, NO):
            py(said)("draw")

    def test_ask_uses_the_connection_handed_in_else_the_claude_cli(self):
        self.assertEqual(ask("p", lambda p: p + "!"), "p!")
        with mock.patch.object(ClaudeCLI, "__call__", lambda cli, p: (cli.command, p)):
            self.assertEqual(ask("p"), (["claude"], "p"))
