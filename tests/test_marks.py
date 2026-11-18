import unittest
from pathlib import Path

from asbuilt.built import python_dir, python_file
from asbuilt.marks import LOOKS_FOR


def _badges(source, label):
    box = python_file("t.py", source.encode())
    return next(c["asbuilt"].get("badges", []) for c in box["children"] if c["labels"][0]["text"] == label)


class MarkTest(unittest.TestCase):
    def test_os_environ_get_is_env(self):
        self.assertEqual(_badges("import os\ndef read():\n    return os.environ.get('K')\n", "read()"), ["env"])

    def test_a_subscript_of_os_environ_is_env(self):
        self.assertEqual(_badges("import os\ndef read():\n    return os.environ['K']\n", "read()"), ["env"])

    def test_membership_in_os_environ_is_env(self):
        self.assertEqual(_badges("import os\ndef read():\n    return 'K' in os.environ\n", "read()"), ["env"])

    def test_environ_imported_from_os_is_env(self):
        self.assertEqual(_badges("from os import environ\ndef read():\n    return environ['K']\n", "read()"), ["env"])

    def test_os_getenv_is_env(self):
        self.assertEqual(_badges("import os\ndef read():\n    return os.getenv('K')\n", "read()"), ["env"])

    def test_getenv_imported_from_os_is_env(self):
        self.assertEqual(_badges("from os import getenv\ndef read():\n    return getenv('K')\n", "read()"), ["env"])

    def test_an_environ_parameter_is_not_env(self):
        source = "from os import environ\ndef app(environ, start_response):\n    return environ['PATH_INFO']\n"
        self.assertEqual(_badges(source, "app()"), [])

    def test_input_is_marked_input_and_a_local_def_is_not(self):
        self.assertEqual(_badges("def ask_user():\n    return input()\n", "ask_user()"), ["input"])
        source = "def ask_user():\n    def input():\n        return 'x'\n    return input()\n"
        self.assertEqual(_badges(source, "ask_user()"), [])

    def test_open_with_a_write_mode_is_writes_and_a_read_is_not(self):
        self.assertEqual(_badges("def save(path):\n    open(path, 'w')\n", "save()"), ["writes"])
        self.assertEqual(_badges("def load(path):\n    open(path)\n", "load()"), [])

    def test_open_mode_as_a_keyword_is_writes(self):
        self.assertEqual(_badges("def save(path):\n    open(path, mode='w')\n", "save()"), ["writes"])

    def test_a_plus_mode_is_writes(self):
        self.assertEqual(_badges("def save(path):\n    open(path, 'r+')\n", "save()"), ["writes"])

    def test_append_mode_is_writes(self):
        self.assertEqual(_badges("def save(path):\n    open(path, 'a')\n", "save()"), ["writes"])

    def test_exclusive_mode_is_writes(self):
        self.assertEqual(_badges("def save(path):\n    open(path, 'x')\n", "save()"), ["writes"])

    def test_an_imported_module_open_takes_the_mode_second(self):
        self.assertEqual(_badges("import gzip\ndef save(p):\n    gzip.open(p, 'wt')\n", "save()"), ["writes"])
        self.assertEqual(_badges("import gzip\ndef read():\n    gzip.open('app.log.gz')\n", "read()"), [])

    def test_a_file_name_passed_to_open_is_not_a_write_mode(self):
        source = (
            "import tarfile, webbrowser\nfrom PIL import Image\n"
            "def read(zf):\n    Image.open('logo.png')\n    tarfile.open('a.tar.gz')\n"
            "    webbrowser.open('https://example.test')\n    zf.open('a.txt')\n"
        )
        self.assertEqual(_badges(source, "read()"), [])

    def test_write_text_on_a_call_result_is_writes(self):
        self.assertEqual(_badges("def save(p):\n    Path(p).write_text('hi')\n", "save()"), ["writes"])

    def test_write_bytes_is_writes(self):
        self.assertEqual(_badges("def save(p):\n    p.write_bytes(b'hi')\n", "save()"), ["writes"])

    def test_a_method_open_with_a_write_mode_is_writes(self):
        self.assertEqual(_badges("def save(p):\n    p.open('w')\n", "save()"), ["writes"])

    def test_os_fdopen_with_a_write_mode_is_writes(self):
        self.assertEqual(_badges("import os\ndef save(fd):\n    os.fdopen(fd, 'wb')\n", "save()"), ["writes"])

    def test_a_parameter_named_open_is_not_writes(self):
        self.assertEqual(_badges("def save(open):\n    open('a', 'w')\n", "save()"), [])

    def test_requests_get_is_calls_out_and_a_caller_a_string_or_no_import_is_not(self):
        source = "import requests\ndef fetch():\n    requests.get('https://example.test')\ndef show():\n    return fetch()\ndef note():\n    return 'requests.get'\ndef bare():\n    requests.get('https://example.test')\n"
        # bare is a second file's idea; this file imported requests, so split the no-import case
        self.assertEqual(_badges(source, "fetch()"), ["calls out"])
        self.assertEqual(_badges(source, "show()"), [])
        self.assertEqual(_badges(source, "note()"), [])
        self.assertEqual(_badges("def bare():\n    requests.get('https://example.test')\n", "bare()"), [])

    def test_an_aliased_import_is_calls_out(self):
        self.assertEqual(_badges("import requests as r\ndef fetch():\n    r.get('https://example.test')\n", "fetch()"), ["calls out"])

    def test_a_name_imported_from_requests_is_calls_out(self):
        self.assertEqual(_badges("from requests import get\ndef fetch():\n    get('https://example.test')\n", "fetch()"), ["calls out"])

    def test_urlopen_imported_from_urllib_request_is_calls_out(self):
        self.assertEqual(_badges("from urllib.request import urlopen\ndef fetch():\n    urlopen('https://example.test')\n", "fetch()"), ["calls out"])

    def test_urllib_parse_is_not_calls_out(self):
        source = "from urllib.parse import quote, urlencode\ndef pack():\n    quote('a')\n    urlencode({})\n"
        self.assertEqual(_badges(source, "pack()"), [])

    def test_urllib_error_is_not_calls_out(self):
        self.assertEqual(_badges("from urllib.error import HTTPError\ndef fail():\n    HTTPError('u', 404, 'm', {}, None)\n", "fail()"), [])

    def test_httpx_is_calls_out(self):
        self.assertEqual(_badges("import httpx\ndef fetch():\n    httpx.get('https://example.test')\n", "fetch()"), ["calls out"])

    def test_subprocess_run_imported_is_calls_out(self):
        self.assertEqual(_badges("from subprocess import run\ndef go():\n    run(['echo'])\n", "go()"), ["calls out"])

    def test_subprocess_imported_under_another_name_is_calls_out(self):
        self.assertEqual(_badges("import subprocess as sp\ndef go():\n    sp.run(['echo'])\n", "go()"), ["calls out"])

    def test_os_system_is_calls_out(self):
        self.assertEqual(_badges("import os\ndef go():\n    os.system('echo')\n", "go()"), ["calls out"])

    def test_http_client_is_calls_out(self):
        self.assertEqual(_badges("import http.client\ndef fetch():\n    http.client.HTTPSConnection('example.test')\n", "fetch()"), ["calls out"])

    def test_subprocess_getoutput_is_not_calls_out(self):
        self.assertEqual(_badges("import subprocess\ndef go():\n    subprocess.getoutput('echo')\n", "go()"), [])

    def test_an_import_inside_the_box_counts(self):
        self.assertEqual(_badges("def fetch():\n    import requests\n    requests.get('https://example.test')\n", "fetch()"), ["calls out"])

    def test_a_class_body_name_does_not_hide_the_same_name_in_a_method(self):
        source = (
            "class Saver:\n    def open(self):\n        return 1\n    def save(self, path):\n        open(path, 'w')\n"
            "from subprocess import run\nclass Runner:\n    def run(self):\n        return 1\n    def go(self):\n        run(['echo'])\n"
        )
        self.assertIn("writes", _badges(source, "Saver"))
        self.assertIn("calls out", _badges(source, "Runner"))

    def test_a_client_built_once_at_the_top_is_that_module(self):
        source = (
            "from openai import OpenAI\nimport anthropic\nimport requests\n"
            "client = OpenAI()\nclaude = anthropic.Anthropic()\nsession = requests.Session()\n"
            "def ask():\n    client.chat.completions.create()\n"
            "def speak():\n    claude.messages.create()\n"
            "def fetch():\n    session.get('https://example.test')\n"
        )
        self.assertEqual(_badges(source, "ask()"), ["model"])
        self.assertEqual(_badges(source, "speak()"), ["model"])
        self.assertEqual(_badges(source, "fetch()"), ["calls out"])

    def test_a_client_assigned_again_is_not_that_module(self):
        source = "from openai import OpenAI\nclient = OpenAI()\nclient = None\ndef ask():\n    client.chat.completions.create()\n"
        self.assertEqual(_badges(source, "ask()"), [])

    def test_socket_socket_is_calls_out_and_gethostname_is_not(self):
        self.assertEqual(_badges("import socket\ndef go():\n    socket.socket()\n", "go()"), ["calls out"])
        self.assertEqual(_badges("import socket\ndef host():\n    socket.gethostname()\n", "host()"), [])

    def test_a_parameter_named_requests_or_socket_is_not_calls_out(self):
        self.assertEqual(_badges("import requests\ndef fetch(requests):\n    requests.get('https://example.test')\n", "fetch()"), [])
        self.assertEqual(_badges("import socket\ndef go(socket):\n    socket.socket()\n", "go()"), [])

    def test_a_local_named_requests_is_not_calls_out(self):
        source = "import requests\ndef fetch():\n    requests = None\n    return requests.get('https://example.test')\n"
        self.assertEqual(_badges(source, "fetch()"), [])

    def test_the_openai_sdk_is_model_and_not_calls_out(self):
        self.assertEqual(_badges("from openai import OpenAI\ndef chat():\n    return OpenAI()\n", "chat()"), ["model"])

    def test_the_anthropic_sdk_is_model(self):
        self.assertEqual(_badges("from anthropic import Anthropic\ndef chat():\n    return Anthropic()\n", "chat()"), ["model"])

    def test_the_repo_marks_its_note_calls_its_env_read_and_write_atomic(self):
        box, _skipped = python_dir(Path(__file__).resolve().parents[1])
        found = {}
        todo = [box]
        for node in todo:
            todo += node.get("children", [])
            label = (node.get("labels") or [{}])[0].get("text")
            badges = (node.get("asbuilt") or {}).get("badges")
            if label in ("OpenRouter", "ClaudeCLI", "ask()", "write_atomic()"):
                found[label] = badges or []
        self.assertIn("calls out", found["OpenRouter"])
        self.assertIn("env", found["OpenRouter"])
        self.assertIn("calls out", found["ClaudeCLI"])
        self.assertIn("env", found["ClaudeCLI"])
        self.assertIn("env", found["ask()"])
        self.assertNotIn("calls out", found["ask()"])
        self.assertIn("writes", found["write_atomic()"])


# One function body per listed write, since a write is a call shape, not a module path.
_WRITE_SAMPLES = {
    "open() with a write mode": "open('p', 'w')",
    "os.fdopen() with a write mode": "os.fdopen(3, 'w')",
    "a .open() method with a write mode": "p.open('w')",
    ".write_text()": "p.write_text('hi')",
    ".write_bytes()": "p.write_bytes(b'hi')",
}


def _calling(name):
    """A function whose body calls the dotted name, with its top module imported."""
    return f"import {name.split('.')[0]}\nimport os\ndef f(p):\n    return {name}()\n"


class LooksForTest(unittest.TestCase):
    """What the page says asBuilt looks for is exactly what marks.py marks."""

    def test_every_listed_name_is_marked(self):
        self.assertEqual(sorted(_WRITE_SAMPLES), sorted(LOOKS_FOR["writes"]))
        for kind, listed in LOOKS_FOR.items():
            for name in listed:
                with self.subTest(kind=kind, name=name):
                    if kind == "writes":
                        source = f"import os\ndef f(p):\n    return {_WRITE_SAMPLES[name]}\n"
                    elif kind == "input":
                        source = f"def f(p):\n    return {name}\n"
                    else:
                        source = _calling(name)
                    self.assertIn(kind, _badges(source, "f()"))

    def test_calls_that_are_not_checked_are_not_marked_and_not_listed(self):
        for name in ("subprocess.getoutput", "os.popen", "socket.gethostbyname", "shutil.copy", "urllib3.PoolManager"):
            with self.subTest(name=name):
                self.assertEqual(_badges(_calling(name), "f()"), [])
                listed = [item for items in LOOKS_FOR.values() for item in items]
                self.assertFalse(any(name == item or name.startswith(item + ".") for item in listed))
