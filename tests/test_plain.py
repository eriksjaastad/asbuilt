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
                "OpenRouter sends data, using a key read from OPENROUTER_API_KEY.",
                "By default OpenRouter waits up to 60 seconds.",
                "If the key is missing, OpenRouter stops with an error.",
            ],
        )
        self.assertEqual(
            got["ClaudeCLI"],
            [
                "ClaudeCLI runs the command claude, keeps only a fixed set of environment names, and returns the command's output.",
                "By default ClaudeCLI waits up to 300 seconds.",
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
        self.assertEqual(got["wait()"], [])
        self.assertEqual(
            got["send_email()"],
            [
                "send_email sends data to example.com over the internet.",
                "send_email waits up to 15 seconds.",
            ],
        )
        self.assertEqual(got["reject()"], ["reject stops with an error."])
        self.assertNotIn("validation", " ".join(got["reject()"]))
        self.assertEqual(got["remove()"], ["remove stops with an error."])
        self.assertNotIn("remove reminder", " ".join(got["remove()"]))
        self.assertEqual(got["retry()"], ["Each try, retry waits up to 15 seconds."])

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

    def test_an_smtp_timeout_is_seconds(self):
        source = 'import smtplib\ndef send_email():\n    smtplib.SMTP("smtp.example.com", timeout=30)\n'
        self.assertEqual(
            _lines(source, "send_email()"), ["send_email waits up to 30 seconds."]
        )

    def test_a_timeout_passed_to_a_helper_is_claimed_only_when_the_helper_shows_it(
        self,
    ):
        forwarded = (
            "def _gh_json(url, timeout):\n"
            "    import requests\n"
            "    requests.get(url, timeout=timeout)\n"
            "def fetch():\n"
            '    _gh_json("https://example.com", timeout=30)\n'
        )
        self.assertEqual(
            _lines(forwarded, "fetch()"), ["fetch waits up to 30 seconds."]
        )
        hidden = (
            "def _search_week(url, timeout):\n"
            "    return url\n"
            "def fetch():\n"
            "    _search_week('https://example.com', timeout=30)\n"
        )
        self.assertEqual(_lines(hidden, "fetch()"), [])

    def test_httpx_client_get_timeout_is_seconds(self):
        source = (
            "import httpx\ndef fetch(url):\n    httpx.Client().get(url, timeout=30)\n"
        )
        self.assertEqual(_lines(source, "fetch()"), ["fetch waits up to 30 seconds."])

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

    def test_a_quoted_message_keeps_its_final_period(self):
        source = 'def fail():\n    raise RuntimeError("no name.")\n'
        self.assertEqual(
            _lines(source, "fail()"),
            ['fail stops with an error that says "no name."'],
        )

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

    def test_a_timeout_parameter_default_is_by_default(self):
        source = 'import requests\ndef fetch(timeout=15):\n    requests.get("https://example.com/x", timeout=timeout)\n'
        self.assertEqual(
            _lines(source, "fetch()"), ["By default fetch waits up to 15 seconds."]
        )

    def test_a_literal_timeout_names_the_box(self):
        source = 'import requests\ndef send():\n    requests.get("https://example.com/x", timeout=15)\n'
        self.assertEqual(_lines(source, "send()"), ["send waits up to 15 seconds."])

    def test_a_note_stays_within_three_sentences(self):
        source = (
            "import os\nimport requests\n"
            "def fetch(payload, timeout=15):\n"
            '    if not os.environ.get("TOKEN"):\n'
            '        raise RuntimeError("missing token")\n'
            '    requests.post("https://example.com/v1", json=payload, timeout=timeout)\n'
            '    return "DONE"\n'
        )
        lines = _lines(source, "fetch()")
        self.assertEqual(len(lines), 3)
        self.assertNotIn("DONE", " ".join(lines))

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
