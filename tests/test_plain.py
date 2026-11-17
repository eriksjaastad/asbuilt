import re
import unittest
from pathlib import Path

import tree_sitter
import tree_sitter_python

from asbuilt.built import _definitions, _scope, python_file
from asbuilt.plain import for_box
from asbuilt.security import constants
from asbuilt.security import for_box as security_notes

_CONNECTIONS = Path("src/asbuilt/connections.py")
_PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))


def _tree(source):
    root = _PARSER.parse(source if isinstance(source, bytes) else source.encode()).root_node
    return root, _scope(list(root.named_children), None, False), constants(list(root.named_children))


def _node(source, name):
    root, scope, words = _tree(source)
    node = next(node for ident, _, _, _, node in _definitions(root) if ident == name)
    return for_box(node, scope, words)


def _sentences(text):
    return [part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part]


def _exception_names(source):
    return set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Failed))\b", source))


class PlainTest(unittest.TestCase):
    def test_each_box_in_connections_says_what_it_does_in_everyday_words(self):
        source = _CONNECTIONS.read_bytes()
        box = python_file("connections.py", source)
        got = {child["labels"][0]["text"]: child["asbuilt"].get("plain") for child in box["children"]}
        self.assertEqual(got["ConnectionFailed"], ["ConnectionFailed is an exception."])
        self.assertEqual(got["Mock"], ['Mock returns the text {"notes": []}.'])
        self.assertEqual(got["_NoRedirect"], ["_NoRedirect returns nothing."])
        self.assertEqual(got["_open()"], ["_open returns whatever the network request returns."])
        self.assertEqual(
            got["OpenRouter"],
            [
                "OpenRouter sends a prompt to openrouter.ai over the internet, using a key read from OPENROUTER_API_KEY.",
                "By default it waits up to 60 seconds.",
                "If the key is missing, the call is refused, the site can't be reached, or the reply has no readable text, it stops with an error; otherwise it returns the text from the reply.",
            ],
        )
        self.assertEqual(
            got["ClaudeCLI"],
            [
                "ClaudeCLI runs the command claude, keeps only a fixed set of environment names, and returns the command's output.",
                "By default it waits up to 300 seconds.",
                "If the command cannot start, it does not finish, the command fails, it is not signed in, or it returns no output, it stops with an error.",
            ],
        )
        self.assertEqual(
            got["ask()"],
            [
                "If ask is given something to call, it returns whatever that returns and does not read ASBUILT_MODEL.",
                "Otherwise it calls ClaudeCLI when ASBUILT_MODEL is claude, OpenRouter when it is 1, and Mock otherwise.",
            ],
        )
        self.assertEqual(
            got["stopped()"],
            ["stopped returns an error made from the command's exit code and the last line of its output."],
        )

    def test_a_docstring_is_not_copied_into_the_note(self):
        lines = _node('def wipe():\n    """This deletes every file on disk."""\n    return 1\n', "wipe")
        self.assertEqual(lines, [])

    def test_a_private_host_is_not_described_as_over_the_internet(self):
        lines = _node(
            'import requests\ndef post(payload):\n    requests.post("http://0.0.0.0:8000/x", json=payload)\n',
            "post",
        )
        self.assertEqual(lines, ["post sends what it was given to 0.0.0.0."])

    def test_security_sentences_stay_the_parser_text(self):
        source = _CONNECTIONS.read_bytes()
        box = python_file("connections.py", source)
        root, scope, words = _tree(source)
        by_label = {child["labels"][0]["text"]: child["asbuilt"] for child in box["children"]}
        for ident, is_class, _, _, node in _definitions(root):
            info = by_label[ident if is_class else f"{ident}()"]
            self.assertEqual(info.get("security") or [], security_notes(node, scope, words), ident)
            for note in info.get("security") or []:
                self.assertIn("expert", note)

    def test_a_plain_note_is_at_most_three_sentences_and_names_no_exception_class(self):
        source = _CONNECTIONS.read_text()
        root, scope, words = _tree(source)
        names = _exception_names(source)
        for ident, _, _, _, node in _definitions(root):
            lines = for_box(node, scope, words)
            text = " ".join(lines)
            self.assertLessEqual(len(lines), 3, ident)
            self.assertLessEqual(len(_sentences(text)), 3, text)
            for name in names:
                if name == ident:
                    continue
                self.assertNotIn(name, text, ident)
        fetch = (
            "import urllib.request\nimport urllib.error\n"
            "class ConnectionFailed(Exception):\n    pass\n"
            "def fetch(prompt):\n"
            "    try:\n        urllib.request.urlopen('https://example.com/v1', timeout=5)\n"
            "    except urllib.error.HTTPError:\n        raise ConnectionFailed('the call was refused')\n"
            "    except urllib.error.URLError:\n        raise ConnectionFailed('the site could not be reached')\n"
            "    except TimeoutError:\n        raise ConnectionFailed('the site could not be reached')\n"
            "    except OSError:\n        raise ConnectionFailed('the site could not be reached')\n"
            "    except JSONDecodeError:\n        raise ConnectionFailed('nothing it could read')\n"
            "    return prompt\n"
        )
        lines = _node(fetch, "fetch")
        text = " ".join(lines)
        self.assertIn("refused", text)
        self.assertLessEqual(len(lines), 3)
        self.assertLessEqual(len(_sentences(text)), 3)
        for name in _exception_names(fetch):
            self.assertNotIn(name, text, name)
