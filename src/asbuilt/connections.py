"""The one place As-Built runs a model (D-017): the installed Claude Code CLI, so it is swapped here."""

import os
import subprocess

TIMEOUT = 300
# Only these reach the CLI: no key, token or other provider (Bedrock, Vertex) can be chosen.
KEPT = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
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


def stopped(done):
    """The failure for a run that exited non-zero: its last stderr line, else stdout's."""
    out = done.stderr.strip() or done.stdout.strip() or "no message"
    return ConnectionFailed(
        f"claude stopped, exit {done.returncode}: {out.splitlines()[-1][:200]}."
    )
