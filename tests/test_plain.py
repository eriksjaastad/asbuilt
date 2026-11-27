import ast
import json
import unittest
from pathlib import Path

from asbuilt.built import python_file

_CONNECTIONS = Path("src/asbuilt/connections.py")
_ORDINARY = Path("tests/ordinary.py")


def _lines(source, label):
    box = python_file("t.py", source.encode() if isinstance(source, str) else source)
    return next(
        child["asbuilt"].get("plain") or []
        for child in box["children"]
        if child["labels"][0]["text"] == label
    )


def _classify(value):
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return ("none", None)
    if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
        try:
            parsed = json.loads(value.value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return ("json", None)
        return ("text", value.value)
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "Exception" or str(name).endswith(("Error", "Exception", "Failed")):
            return ("error", None)
        return ("call", None)
    return ("other", None)


def _scan(node, returns, state):
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        if isinstance(child, ast.Return):
            returns.append(_classify(child.value))
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            state["yield"] = True
        if isinstance(child, ast.Raise):
            state["raise"] = True
        _scan(child, returns, state)


def _escaping_raise(fn):
    def walk(node, handled):
        if (
            isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            )
            and node is not fn
        ):
            return False
        if isinstance(node, ast.Raise):
            return not handled
        if isinstance(node, ast.Try):
            if any(walk(stmt, handled or bool(node.handlers)) for stmt in node.body):
                return True
            return any(
                walk(stmt, handled)
                for stmt in (*node.handlers, *node.orelse, *node.finalbody)
            )
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if walk(child, handled):
                return True
        return False

    return walk(fn, False)


def _analyze(node):
    returns, state = [], {"yield": False, "raise": False}
    bodies = node.body if isinstance(node, ast.ClassDef) else [node]
    bodies = [
        item
        for item in bodies
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ] or [node]
    if not isinstance(node, ast.ClassDef):
        bodies = [node]
    for body in bodies:
        _scan(body, returns, state)
    return returns, state


class PlainTest(unittest.TestCase):
    def test_each_box_in_connections_says_what_it_does_in_everyday_words(self):
        box = python_file("connections.py", _CONNECTIONS.read_bytes())
        got = {
            child["labels"][0]["text"]: child["asbuilt"].get("plain")
            for child in box["children"]
        }
        self.assertEqual(got["ConnectionFailed"], ["ConnectionFailed is an exception."])
        self.assertEqual(got["Mock"], None)
        self.assertEqual(got["_NoRedirect"], None)
        self.assertEqual(
            got["_open()"], ["_open returns whatever the network request returns."]
        )
        self.assertEqual(
            got["OpenRouter"],
            [
                "OpenRouter sends data to openrouter.ai over the internet, using a key read from OPENROUTER_API_KEY.",
                'If the key is missing, OpenRouter stops with an error that says "OPENROUTER_API_KEY is not set.", among others.',
            ],
        )
        self.assertEqual(
            got["ClaudeCLI"],
            [
                "ClaudeCLI runs the command claude and keeps only a fixed set of environment names.",
                'ClaudeCLI can stop with an error that says "claude is not signed in to claude.ai: run claude auth login." or "claude replied with nothing.", among others.',
            ],
        )
        self.assertEqual(
            got["ask()"],
            [
                "If ask is given something to call, it returns whatever that returns and does not read ASBUILT_MODEL.",
                "Otherwise it calls ClaudeCLI when ASBUILT_MODEL is claude, OpenRouter when it is 1, and Mock otherwise.",
            ],
        )
        self.assertEqual(got["stopped()"], ["stopped returns an error."])

    def test_ordinary_code_does_not_inherit_one_file_phrasing(self):
        box = python_file("ordinary.py", _ORDINARY.read_bytes())
        got = {
            child["labels"][0]["text"]: child["asbuilt"].get("plain") or []
            for child in box["children"]
        }
        self.assertEqual(got["missing()"], ["missing returns nothing."])
        self.assertEqual(got["ready()"], ["ready returns the text ok."])
        self.assertEqual(got["mock()"], ["mock returns a fixed text."])
        self.assertEqual(got["copy()"], ["copy runs the command rclone."])
        self.assertEqual(got["rclone()"], [])
        self.assertEqual(got["Cache"], [])
        self.assertEqual(got["stream()"], [])
        self.assertEqual(got["fetch_mini_data()"], ["fetch_mini_data returns nothing."])
        self.assertEqual(got["load_config()"], [])
        self.assertEqual(
            got["lookup()"],
            ['lookup stops with an error that says "config was not found".'],
        )
        self.assertEqual(got["collect()"], ["collect runs the command rclone."])
        self.assertEqual(
            got["send_email()"],
            ["send_email sends data to example.com over the internet."],
        )
        self.assertEqual(got["reject()"], ["reject stops with an error."])
        self.assertNotIn("validation", " ".join(got["reject()"]))
        self.assertEqual(got["remove()"], ["remove stops with an error."])
        self.assertNotIn("remove reminder", " ".join(got["remove()"]))
        self.assertEqual(
            got["flag_guard()"], ['flag_guard can stop with an error that says "nope".']
        )
        self.assertNotIn("force", " ".join(got["flag_guard()"]))
        self.assertEqual(
            got["always_fails_branches()"],
            [
                'If the environment value TOKEN is missing, always_fails_branches stops with an error that says "missing".'
            ],
        )

    def test_a_return_claim_needs_every_return_to_agree(self):
        lines = _lines(
            'def label(ok):\n    if ok:\n        return "just now"\n    return "yesterday"\n',
            "label()",
        )
        self.assertEqual(lines, [])

    def test_a_generator_does_not_claim_a_return(self):
        lines = _lines('def gen():\n    yield "a"\n    return "a"\n', "gen()")
        self.assertEqual(lines, [])

    def test_an_env_guard_that_returns_does_not_stop_with_an_error(self):
        source = 'import os\ndef agent_name():\n    name = os.environ.get("AGENT_NAME")\n    if not name:\n        return None\n    return name\n'
        self.assertEqual(_lines(source, "agent_name()"), [])

    def test_an_env_guard_that_raises_names_the_environment_value(self):
        source = 'import os\ndef load():\n    name = os.environ.get("AGENT_NAME")\n    if not name:\n        raise RuntimeError("no name")\n    return name\n'
        self.assertEqual(
            _lines(source, "load()"),
            [
                'If the environment value AGENT_NAME is missing, load stops with an error that says "no name".'
            ],
        )

    def test_a_raise_on_one_path_can_stop(self):
        source = 'def check(name):\n    if not name:\n        raise ValueError("empty")\n    return name\n'
        lines = _lines(source, "check()")
        self.assertEqual(lines, ['check can stop with an error that says "empty".'])
        self.assertNotIn("name", lines[0])
        self.assertNotIn("is empty", lines[0])

    def test_an_env_guard_that_does_not_raise_is_not_why_it_stops(self):
        source = (
            "import os\n"
            "def load(ready):\n"
            '    name = os.environ.get("AGENT_NAME")\n'
            "    if not name:\n"
            "        return None\n"
            "    if not ready:\n"
            '        raise RuntimeError("nope")\n'
            "    return name\n"
        )
        text = " ".join(_lines(source, "load()"))
        self.assertNotIn("AGENT_NAME", text)
        self.assertNotIn("ready", text)
        self.assertIn("can stop", text)
        self.assertNotIn("stops", text)

    def test_a_url_on_another_call_is_not_where_data_is_sent(self):
        source = 'import pandas as pd\ndef load(rows):\n    pd.DataFrame(data=rows)\n    pd.read_csv("https://example.com/data.csv")\n'
        self.assertEqual(_lines(source, "load()"), [])

    def test_a_browser_host_is_not_borrowed_by_the_send(self):
        source = (
            "import webbrowser\nimport requests\n"
            "def post(api, body):\n"
            '    webbrowser.open("https://github.com/x")\n'
            "    requests.post(api, json=body)\n"
        )
        text = " ".join(_lines(source, "post()"))
        self.assertNotIn("github.com", text)

    def test_an_env_branch_that_sometimes_returns_can_stop(self):
        source = (
            "import os\n"
            "def load(extra):\n"
            '    name = os.environ.get("AGENT_NAME")\n'
            "    if not name:\n"
            "        if extra:\n"
            '            raise RuntimeError("no name")\n'
            "        return None\n"
            "    return name\n"
        )
        text = " ".join(_lines(source, "load()"))
        self.assertIn("can stop", text)
        self.assertNotIn("stops", text)

    def test_the_host_comes_from_the_first_fact_that_sends(self):
        first = (
            "import requests\n"
            "def post():\n"
            '    requests.post("https://one.example/a", data=b"1")\n'
            '    requests.post("https://two.example/b", data=b"2")\n'
        )
        text = " ".join(_lines(first, "post()"))
        self.assertIn("one.example", text)
        self.assertNotIn("two.example", text)
        later = (
            "import requests\n"
            "def post(url, body):\n"
            "    requests.post(url, data=body)\n"
            '    requests.post("https://example.com/v1", data=b"x")\n'
        )
        self.assertEqual(
            _lines(later, "post()"),
            ["post sends data to example.com over the internet."],
        )

    def test_at_most_two_messages_are_quoted(self):
        source = (
            "def fail():\n"
            '    raise ValueError("one")\n'
            '    raise ValueError("two")\n'
            '    raise ValueError("three")\n'
        )
        lines = _lines(source, "fail()")
        self.assertEqual(
            lines,
            ['fail stops with an error that says "one" or "two", among others.'],
        )
        self.assertEqual(" ".join(lines).count(", among others"), 1)
        self.assertNotIn("three", lines[0])

    def test_any_other_escaping_raise_is_among_others(self):
        bare = (
            "def send_email():\n"
            '    raise ValueError("missing password")\n'
            "    try:\n"
            "        send()\n"
            "    except OSError:\n"
            "        raise\n"
        )
        text = " ".join(_lines(bare, "send_email()"))
        self.assertIn('"missing password"', text)
        self.assertIn(", among others", text)
        mirror = (
            "def mirror():\n"
            "    try:\n"
            "        work()\n"
            "    except Exception:\n"
            "        raise\n"
            '    raise RuntimeError("copy failed")\n'
        )
        text = " ".join(_lines(mirror, "mirror()"))
        self.assertIn('"copy failed"', text)
        self.assertIn(", among others", text)
        pull = (
            "def pull(name):\n"
            '    raise RuntimeError("mailbox missing")\n'
            "    raise RuntimeError(\n"
            '        f"could not pull {name} "\n'
            '        "from the server"\n'
            "    )\n"
        )
        text = " ".join(_lines(pull, "pull()"))
        self.assertIn('"mailbox missing"', text)
        self.assertNotIn("could not pull", text)
        self.assertIn(", among others", text)
        only = 'def fail():\n    raise RuntimeError("only this")\n'
        self.assertNotIn("among others", " ".join(_lines(only, "fail()")))

    def test_a_quoted_message_keeps_its_final_period(self):
        source = 'def fail():\n    raise RuntimeError("no name.")\n'
        self.assertEqual(
            _lines(source, "fail()"),
            ['fail stops with an error that says "no name."'],
        )

    def test_command_output_needs_every_return_to_be_stdout(self):
        mixed = (
            "import subprocess\n"
            "def run_git(args):\n"
            "    result = subprocess.run(['git', *args], capture_output=True)\n"
            "    if result.returncode in (3, 4):\n"
            "        return None\n"
            "    return result.stdout\n"
        )
        self.assertEqual(_lines(mixed, "run_git()"), ["run_git runs the command git."])
        only = (
            "import subprocess\n"
            "def run_git(args):\n"
            "    result = subprocess.run(['git', *args], capture_output=True)\n"
            "    return result.stdout\n"
        )
        self.assertEqual(
            _lines(only, "run_git()"),
            ["run_git runs the command git and returns the command's output."],
        )
        none = (
            "import subprocess\n"
            "def output_or_none(args):\n"
            "    result = subprocess.run(['rclone', *args], capture_output=True)\n"
            "    if result.returncode in (3, 4):\n"
            "        return None\n"
            "    return result.stdout\n"
        )
        self.assertEqual(
            _lines(none, "output_or_none()"),
            ["output_or_none runs the command rclone."],
        )
        empty = (
            "import subprocess\n"
            "def output_or_empty(args):\n"
            "    result = subprocess.run(['rclone', *args], capture_output=True)\n"
            "    if not result.stdout:\n"
            '        return ""\n'
            "    return result.stdout\n"
        )
        self.assertEqual(
            _lines(empty, "output_or_empty()"),
            ["output_or_empty runs the command rclone."],
        )

    def test_two_commands_are_not_named(self):
        source = (
            "import subprocess\n"
            "def _cleanup_temp_worktree():\n"
            '    subprocess.run(["git", "worktree", "remove", "tmp"])\n'
            '    subprocess.run(["rm", "-rf", "tmp"])\n'
        )
        self.assertEqual(_lines(source, "_cleanup_temp_worktree()"), [])

    def test_the_env_name_is_only_the_guarded_value(self):
        source = (
            "import os\n"
            "def human_address():\n"
            '    other = os.environ.get("OTHER_NAME")\n'
            '    value = os.environ.get("AGENT_CHAT_HUMAN", "")\n'
            "    if not value:\n"
            '        raise ValueError("missing")\n'
            "    return other\n"
        )
        self.assertEqual(
            _lines(source, "human_address()"),
            [
                'If the environment value AGENT_CHAT_HUMAN is missing, human_address stops with an error that says "missing".'
            ],
        )
        loose = (
            "import os\n"
            "def _get_jwt_secret():\n"
            '    secret = os.environ.get("JWT_SECRET")\n'
            '    other = os.environ.get("OTHER_NAME")\n'
            "    if not check(secret):\n"
            '        raise RuntimeError("bad secret")\n'
            "    return other\n"
        )
        text = " ".join(_lines(loose, "_get_jwt_secret()"))
        self.assertNotIn("JWT_SECRET", text)
        self.assertNotIn("OTHER_NAME", text)
        self.assertNotIn("is missing", text)

    def test_an_fstring_of_a_module_constant_is_the_message(self):
        source = (
            'KEY = "OPENROUTER_API_KEY"\n'
            "import os\n"
            "class ConnectionFailed(Exception):\n"
            "    pass\n"
            "def call():\n"
            "    key = os.environ.get(KEY)\n"
            "    if not key:\n"
            '        raise ConnectionFailed(f"{KEY} is not set.")\n'
            "    return key\n"
        )
        self.assertEqual(
            _lines(source, "call()"),
            [
                'If the environment value OPENROUTER_API_KEY is missing, call stops with an error that says "OPENROUTER_API_KEY is not set."'
            ],
        )

    def test_click_usage_error_quotes_its_first_argument(self):
        source = (
            "import click\n"
            "def config_show(effective):\n"
            "    if not effective:\n"
            '        raise click.UsageError("only --effective is currently supported", "extra")\n'
            "    return None\n"
        )
        text = " ".join(_lines(source, "config_show()"))
        self.assertIn('"only --effective is currently supported"', text)
        self.assertNotIn("If effective", text)
        self.assertNotIn("extra", text)

    def test_a_local_class_named_usage_error_is_not_clicks(self):
        source = (
            "class UsageError(Exception):\n"
            "    def __init__(self, code):\n"
            '        super().__init__("fixed failure")\n'
            "def fail():\n"
            '    raise UsageError("024")\n'
        )
        text = " ".join(_lines(source, "fail()"))
        self.assertNotIn("024", text)
        self.assertNotIn("says", text)

    def test_an_fstring_conversion_is_not_quoted(self):
        source = (
            'PATTERN = "regex"\n'
            "def run_simulation():\n"
            '    raise RuntimeError(f"{PATTERN!r} got regex")\n'
        )
        text = " ".join(_lines(source, "run_simulation()"))
        self.assertNotIn("got regex", text)
        self.assertNotIn("says", text)

    def test_the_same_command_twice_keeps_its_name(self):
        source = (
            "import subprocess\n"
            "def reload():\n"
            '    subprocess.run(["git", "status"])\n'
            '    subprocess.run(["git", "diff"])\n'
        )
        self.assertEqual(_lines(source, "reload()"), ["reload runs the command git."])

    def test_a_second_condition_is_not_tacked_on(self):
        source = (
            "import os\n"
            "def _get_jwt_secret():\n"
            '    name = os.environ.get("APP_NAME")\n'
            '    secret = os.environ.get("JWT_SECRET")\n'
            "    if not name:\n"
            '        raise RuntimeError("no name")\n'
            "    if not secret:\n"
            '        raise RuntimeError("no secret")\n'
            "    return secret\n"
        )
        text = " ".join(_lines(source, "_get_jwt_secret()"))
        self.assertNotIn("and if", text)
        self.assertNotIn("JWT_SECRET", text)
        self.assertIn("APP_NAME", text)
        self.assertIn("no name", text)
        self.assertIn(", among others", text)

    def test_a_flag_is_not_called_empty(self):
        source = (
            "def toggle(enabled):\n"
            "    if not enabled:\n"
            '        raise RuntimeError("turned off")\n'
            "    return enabled\n"
        )
        text = " ".join(_lines(source, "toggle()"))
        self.assertEqual(text, 'toggle can stop with an error that says "turned off".')
        self.assertNotIn("enabled", text)
        self.assertNotIn("is empty", text)

    def test_a_custom_init_that_builds_another_message_is_not_quoted(self):
        source = (
            "class CheckoutsMigrationPendingError(ValueError):\n"
            "    def __init__(self, migration='023'):\n"
            '        super().__init__(f"project checkouts need migration {migration}")\n'
            "def checkout():\n"
            '    raise CheckoutsMigrationPendingError("024")\n'
        )
        text = " ".join(_lines(source, "checkout()"))
        self.assertNotIn("024", text)
        self.assertNotIn("says", text)
        self.assertIn("stops", text)

    def test_a_builtin_error_is_quoted_without_a_hand_written_list(self):
        source = 'def fail():\n    raise ConnectionRefusedError("the port refused the call")\n'
        self.assertEqual(
            _lines(source, "fail()"),
            ['fail stops with an error that says "the port refused the call".'],
        )
        broken = 'def fail():\n    raise BrokenPipeError("the pipe closed")\n'
        self.assertIn('"the pipe closed"', " ".join(_lines(broken, "fail()")))
        decode = (
            'def fail():\n    raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")\n'
        )
        self.assertNotIn("utf-8", " ".join(_lines(decode, "fail()")))

    def test_adjacent_string_literals_are_one_message(self):
        source = (
            "def fail():\n"
            "    raise RuntimeError(\n"
            '        "table not found. "\n'
            '        "The hook must have fired."\n'
            "    )\n"
        )
        self.assertEqual(
            _lines(source, "fail()"),
            [
                'fail stops with an error that says "table not found. The hook must have fired."'
            ],
        )

    def test_a_repeated_message_is_quoted_once(self):
        source = (
            'def fail():\n    raise ValueError("same")\n    raise ValueError("same")\n'
        )
        self.assertEqual(
            _lines(source, "fail()"),
            ['fail stops with an error that says "same".'],
        )

    def test_a_category_is_not_the_message_when_init_passes_the_second_argument(self):
        source = (
            "class APIError(Exception):\n"
            "    def __init__(self, category, message, code):\n"
            "        super().__init__(message)\n"
            "def fail():\n"
            '    raise APIError("validation", "the real message", 2)\n'
        )
        text = " ".join(_lines(source, "fail()"))
        self.assertIn('"the real message"', text)
        self.assertNotIn("validation", text)

    def test_a_keyword_message_is_quoted_when_init_passes_it_on(self):
        source = (
            "class APIError(Exception):\n"
            "    def __init__(self, message):\n"
            "        super().__init__(message)\n"
            "def fail():\n"
            '    raise APIError(message="the real message")\n'
        )
        self.assertEqual(
            _lines(source, "fail()"),
            ['fail stops with an error that says "the real message".'],
        )

    def test_an_argument_init_does_not_pass_on_is_not_quoted(self):
        source = (
            "class APIError(Exception):\n"
            "    def __init__(self, category):\n"
            '        super().__init__("fixed failure")\n'
            "def fail():\n"
            '    raise APIError("validation")\n'
        )
        text = " ".join(_lines(source, "fail()"))
        self.assertNotIn("validation", text)
        self.assertNotIn("fixed failure", text)
        self.assertNotIn("says", text)

    def test_a_category_argument_is_not_quoted_as_the_message(self):
        source = (
            "class APIError(Exception):\n    pass\n"
            "def reject():\n"
            '    raise APIError("validation", "the real message", 2)\n'
        )
        text = " ".join(_lines(source, "reject()"))
        self.assertNotIn("validation", text)
        self.assertNotIn("says", text)

    def test_a_helper_argument_is_not_quoted_as_the_message(self):
        source = (
            "def _built(action, exc):\n"
            '    return RuntimeError(f"Failed to {action}: {exc}")\n'
            "def remove(exc):\n"
            '    raise _built("remove reminder", exc)\n'
        )
        text = " ".join(_lines(source, "remove()"))
        self.assertNotIn("remove reminder", text)
        self.assertNotIn("says", text)

    def test_a_message_that_says_refused_is_not_an_error_unless_it_raises(self):
        source = 'def revert(path):\n    try:\n        path.unlink()\n    except OSError as exc:\n        print(f"refused: {exc}")\n    return []\n'
        self.assertEqual(_lines(source, "revert()"), [])

    def test_a_docstring_is_not_copied_into_the_note(self):
        source = (
            "import os\nimport requests\n"
            "def send():\n"
            '    """This deletes every file."""\n'
            "    f\"Bearer {os.environ.get('OPENROUTER_API_KEY')}\"\n"
            '    requests.post("https://example.com/v1", data=b"x")\n'
        )
        lines = _lines(source, "send()")
        self.assertEqual(lines, ["send sends data to example.com over the internet."])

    def test_a_private_address_is_not_over_the_internet(self):
        source = 'import requests\ndef post(payload):\n    requests.post("http://10.1.2.3/x", json=payload)\n'
        self.assertEqual(_lines(source, "post()"), ["post sends data to 10.1.2.3."])

    def test_an_unspecified_address_is_not_over_the_internet(self):
        source = 'import requests\ndef post(payload):\n    requests.post("http://0.0.0.0:8000/x", json=payload)\n'
        self.assertEqual(_lines(source, "post()"), ["post sends data to 0.0.0.0."])

    def test_a_debug_fstring_is_not_quoted(self):
        source = (
            'NAME = "OPENROUTER_API_KEY"\n'
            "def fail():\n"
            '    raise RuntimeError(f"{NAME=}")\n'
        )
        text = " ".join(_lines(source, "fail()"))
        self.assertNotIn("says", text)
        self.assertNotIn("OPENROUTER_API_KEY", text)

    def test_a_note_stays_within_three_sentences(self):
        source = (
            "import os\nimport requests\n"
            "def fetch(payload):\n"
            '    if not os.environ.get("TOKEN"):\n'
            '        raise RuntimeError("missing token")\n'
            '    requests.post("https://example.com/v1", json=payload)\n'
            '    return "DONE"\n'
        )
        self.assertEqual(
            _lines(source, "fetch()"),
            [
                "fetch sends data to example.com over the internet.",
                'If the environment value TOKEN is missing, fetch stops with an error that says "missing token".',
                "fetch returns the text DONE.",
            ],
        )

    def test_a_raised_message_is_quoted_and_its_class_is_not_named(self):
        source = (
            "import urllib.request\nimport urllib.error\n"
            "class ConnectionFailed(Exception):\n    pass\n"
            "def fetch():\n"
            "    try:\n        urllib.request.urlopen('https://example.com/v1')\n"
            "    except urllib.error.HTTPError:\n"
            "        raise ConnectionFailed('the call was refused')\n"
        )
        text = " ".join(_lines(source, "fetch()"))
        self.assertIn('"the call was refused"', text)
        self.assertNotIn("HTTPError", text)
        self.assertNotIn("ConnectionFailed", text)
        self.assertLessEqual(len(_lines(source, "fetch()")), 3)

    def test_security_sentences_stay_the_parser_text(self):
        box = python_file("connections.py", _CONNECTIONS.read_bytes())
        notes = next(
            child["asbuilt"]["security"]
            for child in box["children"]
            if child["labels"][0]["text"] == "OpenRouter"
        )
        self.assertEqual(
            [(note["plain"], note["expert"]) for note in notes],
            [
                (
                    "This reads OPENROUTER_API_KEY from the environment: os.environ.get(KEY_NAME).",
                    "os.environ.get(KEY_NAME) reads OPENROUTER_API_KEY from the environment.",
                ),
                (
                    "This sends data in a network request: urllib.request.Request(...).",
                    "urllib.request.Request(...). This call is not inside try.",
                ),
            ],
        )

    def test_return_and_error_claims_match_the_code(self):
        for path in (_CONNECTIONS, _ORDINARY):
            source = path.read_text()
            tree = ast.parse(source)
            box = python_file(path.name, source.encode())
            defined = {
                node.name: node
                for node in tree.body
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            }
            for child in box["children"]:
                label = child["labels"][0]["text"]
                name = label[:-2] if label.endswith("()") else label
                lines = (child.get("asbuilt") or {}).get("plain") or []
                returns, state = _analyze(defined[name])
                text = " ".join(lines)
                for line in lines:
                    self.assertFalse(line.startswith("It "), line)
                if isinstance(defined[name], ast.ClassDef):
                    for phrase in (
                        "returns nothing",
                        "returns the text ",
                        "returns a fixed text",
                        "returns an error.",
                        "returns whatever the network",
                    ):
                        self.assertNotIn(phrase, text, label)
                    if "with an error" in text:
                        methods = [
                            item
                            for item in defined[name].body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        ]
                        self.assertTrue(
                            any(_escaping_raise(method) for method in methods), label
                        )
                    self.assertLessEqual(
                        " ".join(lines).count(", among others"), 1, label
                    )
                    continue
                if "returns nothing" in text:
                    self.assertTrue(
                        returns
                        and all(item[0] == "none" for item in returns)
                        and not state["yield"],
                        label,
                    )
                for line in lines:
                    if "returns the text " not in line:
                        continue
                    claimed = line.split("returns the text ", 1)[1].removesuffix(".")
                    self.assertTrue(
                        returns
                        and all(item == ("text", claimed) for item in returns)
                        and not state["yield"],
                        label,
                    )
                if "returns a fixed text" in text:
                    self.assertTrue(
                        returns
                        and all(item[0] == "json" for item in returns)
                        and not state["yield"],
                        label,
                    )
                if any(line.endswith("returns an error.") for line in lines):
                    self.assertTrue(
                        returns
                        and all(item[0] == "error" for item in returns)
                        and not state["yield"],
                        label,
                    )
                if "returns whatever the network request returns" in text:
                    self.assertTrue(
                        returns
                        and all(item[0] == "call" for item in returns)
                        and not state["yield"],
                        label,
                    )
                if "with an error" in text:
                    self.assertTrue(_escaping_raise(defined[name]), label)
                self.assertLessEqual(" ".join(lines).count(", among others"), 1, label)

    def test_non_decimal_integer_default_parameters_do_not_crash(self):
        source = (
            "def write_secret(path, mode=0o644, flags: int = 0x10, mask=0b101):\n"
            "    return mode\n"
        )
        box = python_file("t.py", source.encode())
        self.assertEqual(box["children"][0]["labels"][0]["text"], "write_secret()")

    def test_cyclic_class_inheritance_does_not_recurse_infinitely(self):
        self_cycle = (
            "class LoopError(LoopError):\n"
            "    pass\n"
            "def fail():\n"
            '    raise LoopError("boom")\n'
        )
        self.assertIn("stops with an error", " ".join(_lines(self_cycle, "fail()")))
        mutual_cycle = (
            "class A(B):\n"
            "    pass\n"
            "class B(A):\n"
            "    pass\n"
            "def fail():\n"
            '    raise A("boom")\n'
        )
        self.assertIn("stops with an error", " ".join(_lines(mutual_cycle, "fail()")))
