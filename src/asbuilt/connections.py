"""The one place As-Built runs a model: the installed Claude Code CLI, so it is swapped here."""

import json
import os
import subprocess

TIMEOUT = 300
# Only these reach the CLI: no key, token or other provider (Bedrock, Vertex) can be chosen.
KEPT = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
STATUS = ("auth", "status", "--json")
OFF = ("--tools", "", "--no-session-persistence", "--strict-mcp-config", "--safe-mode")
FLAGS = ("-p", *OFF, "--settings", '{"disableAllHooks": true}')
NOT_SIGNED_IN = "claude is not signed in to claude.ai: run claude auth login."
TEXT = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}


class ConnectionFailed(Exception):
    """A model call that gave no reply, said in one line meant for the person."""


class ClaudeCLI:
    """The installed Claude Code CLI, run with only the kept environment and a timeout."""

    def __init__(self, command=("claude",), timeout=TIMEOUT):
        self.command, self.timeout = list(command), timeout

    def run(self, args, stdin):
        """The finished CLI run, or a ConnectionFailed when it cannot start or finish."""
        argv, t = [*self.command, *args], self.timeout
        env = {k: v for k, v in os.environ.items() if k in KEPT}
        try:
            return subprocess.run(argv, input=stdin, env=env, timeout=t, **TEXT)
        except FileNotFoundError:
            why = "was not found: install Claude Code"
        except subprocess.TimeoutExpired:
            why = f"gave no reply in {t} s"
        except OSError as exc:
            why = f"cannot be started: {exc.strerror or type(exc).__name__}"
        raise ConnectionFailed(f"claude {why}.")

    def check_signed_in(self):
        """A ConnectionFailed unless auth status says signed in to claude.ai, first party."""
        done = self.run(STATUS, "")
        if done.returncode not in (0, 1):
            raise stopped(done)
        try:
            status = json.loads(done.stdout)
        except (ValueError, RecursionError):
            status = None
        ok = isinstance(status, dict) and status.get("loggedIn") is True
        ok = ok and status.get("authMethod") == "claude.ai"
        ok = ok and status.get("apiProvider") == "firstParty"
        if done.returncode or not ok:
            raise ConnectionFailed(NOT_SIGNED_IN)

    def __call__(self, prompt):
        """The reply to `prompt`, sent on stdin with tools, MCP, hooks and plugins off."""
        self.check_signed_in()
        done = self.run(FLAGS, prompt)
        if done.returncode:
            raise stopped(done)
        if not done.stdout.strip():
            raise ConnectionFailed("claude replied with nothing.")
        return done.stdout


def ask(prompt, connection=None):
    """The reply to `prompt` from `connection`, else from the installed Claude CLI."""
    return (ClaudeCLI() if connection is None else connection)(prompt)


def stopped(done):
    """The failure for a run that exited non-zero: its last stderr line, else stdout's."""
    out = done.stderr.strip() or done.stdout.strip() or "no message"
    return ConnectionFailed(
        f"claude stopped, exit {done.returncode}: {out.splitlines()[-1][:200]}."
    )
