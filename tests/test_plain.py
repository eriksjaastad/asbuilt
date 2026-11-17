import ast
import json
import unittest
from pathlib import Path

from asbuilt.built import python_file

_CONNECTIONS = Path("src/asbuilt/connections.py")
_ORDINARY = Path("tests/ordinary.py")


def _lines(source, label):
    box = python_file("t.py", source.encode() if isinstance(source, str) else source)
    return next(child["asbuilt"].get("plain") or [] for child in box["children"] if child["labels"][0]["text"] == label)


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
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Return):
            returns.append(_classify(child.value))
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            state["yield"] = True
        if isinstance(child, ast.Raise):
            state["raise"] = True
        _scan(child, returns, state)


def _analyze(node):
    returns, state = [], {"yield": False, "raise": False}
    bodies = node.body if isinstance(node, ast.ClassDef) else [node]
    bodies = [item for item in bodies if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))] or [node]
    if not isinstance(node, ast.ClassDef):
        bodies = [node]
    for body in bodies:
        _scan(body, returns, state)
    return returns, state


class PlainTest(unittest.TestCase):
    def test_each_box_in_connections_says_what_it_does_in_everyday_words(self):
        box = python_file("connections.py", _CONNECTIONS.read_bytes())
        got = {child["labels"][0]["text"]: child["asbuilt"].get("plain") for child in box["children"]}
        self.assertEqual(got["ConnectionFailed"], ["ConnectionFailed is an exception."])
        self.assertEqual(got["Mock"], ["Mock returns a fixed text."])
        self.assertEqual(got["_NoRedirect"], ["_NoRedirect returns nothing."])
        self.assertEqual(got["_open()"], ["_open returns whatever the network request returns."])
        self.assertEqual(
            got["OpenRouter"],
            [
                "OpenRouter sends what it was given to openrouter.ai over the internet, using a key read from OPENROUTER_API_KEY.",
                "By default OpenRouter waits up to 60 seconds.",
                'If the key is missing, OpenRouter stops with an error, and it stops with an error that says "OpenRouter could not be reached." or "OpenRouter replied with nothing it could read.".',
            ],
        )
        self.assertEqual(
            got["ClaudeCLI"],
            [
                "ClaudeCLI runs the command claude and keeps only a fixed set of environment names.",
                "By default ClaudeCLI waits up to 300 seconds.",
                'ClaudeCLI stops with an error that says "claude is not signed in to claude.ai: run claude auth login." or "claude replied with nothing.".',
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
        got = {child["labels"][0]["text"]: child["asbuilt"].get("plain") or [] for child in box["children"]}
        self.assertEqual(got["label()"], [])
        self.assertEqual(got["gen()"], [])
        self.assertEqual(got["agent_name()"], [])
        self.assertEqual(
            got["load()"],
            ['If the environment value AGENT_NAME is missing, load stops with an error, and it stops with an error that says "no name".'],
        )
        self.assertEqual(got["revert()"], [])
        self.assertEqual(got["missing()"], ["missing returns nothing."])
        self.assertEqual(got["ready()"], ["ready returns the text ok."])
        self.assertEqual(got["mock()"], ["mock returns a fixed text."])
        self.assertEqual(got["copy()"], ["copy runs the command rclone."])
        self.assertEqual(got["rclone()"], [])

    def test_a_return_claim_needs_every_return_to_agree(self):
        lines = _lines('def label(ok):\n    if ok:\n        return "just now"\n    return "yesterday"\n', "label()")
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
            ['If the environment value AGENT_NAME is missing, load stops with an error, and it stops with an error that says "no name".'],
        )

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
        self.assertEqual(_lines(source, "post()"), ["post sends what it was given to 10.1.2.3."])

    def test_an_unspecified_address_is_not_over_the_internet(self):
        source = 'import requests\ndef post(payload):\n    requests.post("http://0.0.0.0:8000/x", json=payload)\n'
        self.assertEqual(_lines(source, "post()"), ["post sends what it was given to 0.0.0.0."])

    def test_a_timeout_parameter_default_is_by_default(self):
        source = 'import requests\ndef fetch(timeout=15):\n    requests.get("https://example.com/x", timeout=timeout)\n'
        self.assertEqual(_lines(source, "fetch()"), ["By default fetch waits up to 15 seconds."])

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
        notes = next(child["asbuilt"]["security"] for child in box["children"] if child["labels"][0]["text"] == "OpenRouter")
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
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
            for child in box["children"]:
                label = child["labels"][0]["text"]
                name = label[:-2] if label.endswith("()") else label
                lines = (child.get("asbuilt") or {}).get("plain") or []
                returns, state = _analyze(defined[name])
                text = " ".join(lines)
                for line in lines:
                    self.assertFalse(line.startswith("It "), line)
                if "returns nothing" in text:
                    self.assertTrue(returns and all(item[0] == "none" for item in returns) and not state["yield"], label)
                for line in lines:
                    if "returns the text " not in line:
                        continue
                    claimed = line.split("returns the text ", 1)[1].removesuffix(".")
                    self.assertTrue(returns and all(item == ("text", claimed) for item in returns) and not state["yield"], label)
                if "returns a fixed text" in text:
                    self.assertTrue(returns and all(item[0] == "json" for item in returns) and not state["yield"], label)
                if any(line.endswith("returns an error.") for line in lines):
                    self.assertTrue(returns and all(item[0] == "error" for item in returns) and not state["yield"], label)
                if "returns whatever the network request returns" in text:
                    self.assertTrue(returns and all(item[0] == "call" for item in returns) and not state["yield"], label)
                if "stops with an error" in text:
                    self.assertTrue(state["raise"], label)
