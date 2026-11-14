"""The one place As-Built runs a model, so it is swapped here.

ask() never calls out. Set ASBUILT_MODEL=1 and it calls OpenRouter instead, using
OPENROUTER_API_KEY from the environment. The key is read at call time and is not
written into a file or an error. Pass a connection to use something else. The Claude
CLI remains for a caller that names it; nothing uses it unless asked.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request

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


MODEL = "x-ai/grok-4.7"  # the strongest Grok model on OpenRouter
URL = "https://openrouter.ai/api/v1/chat/completions"
OPT_IN = "ASBUILT_MODEL"
KEY_NAME = "OPENROUTER_API_KEY"


class Mock:
    """A stand-in that never calls out. Its reply is no notes, so nothing is invented."""

    def __call__(self, prompt):
        return '{"notes": []}'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None  # a redirect must not carry the key to another host


def _open(req, timeout):
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)


class OpenRouter:
    """One chat completion. The key stays in the header of this call and nowhere else."""

    def __init__(self, opener=None, timeout=60, model=MODEL):
        self.opener, self.timeout, self.model = opener, timeout, model

    def __call__(self, prompt):
        key = os.environ.get(KEY_NAME) or ""
        if not key:
            raise ConnectionFailed(f"{KEY_NAME} is not set.")
        body = json.dumps({"model": self.model, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            URL, data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        )
        try:  # _open is looked up at call time, so a test can stand in for the network
            with (self.opener or _open)(req, self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise ConnectionFailed(f"OpenRouter refused the call, status {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ConnectionFailed("OpenRouter could not be reached.") from None
        try:
            text = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ConnectionFailed("OpenRouter replied with nothing it could read.") from None
        if not isinstance(text, str) or not text.strip() or key in text:
            raise ConnectionFailed("OpenRouter replied with nothing it could read.")
        return text


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
    """The reply to `prompt`. The default is the mock. ASBUILT_MODEL=1 uses OpenRouter."""
    if connection is not None:
        return connection(prompt)
    return (OpenRouter() if os.environ.get(OPT_IN) == "1" else Mock())(prompt)


def stopped(done):
    """The failure for a run that exited non-zero: its last stderr line, else stdout's."""
    out = done.stderr.strip() or done.stdout.strip() or "no message"
    return ConnectionFailed(
        f"claude stopped, exit {done.returncode}: {out.splitlines()[-1][:200]}."
    )
