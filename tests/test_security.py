import json
import os
import unittest
from pathlib import Path
from unittest import mock

from asbuilt.built import python_dir, python_file
from asbuilt.connections import OpenRouter
from asbuilt.notes import annotate
from asbuilt.security import host_name


def _box(source, label):
    box = python_file("t.py", source.encode())
    return next(c for c in box["children"] if c["labels"][0]["text"] == label)


def _notes(source, label):
    return _box(source, label)["asbuilt"].get("security")


def _plan(source):
    return {"id": "p", "children": [python_file("t.py", source.encode())]}


class SecurityTest(unittest.TestCase):
    def test_an_environment_name_is_a_literal_or_one_string_assignment_and_nothing_else(self):
        literal = _notes("import os\ndef read():\n    return os.environ.get('OPENROUTER_API_KEY')\n", "read()")
        self.assertEqual(literal[0]["fact"]["name"], "OPENROUTER_API_KEY")
        self.assertIn("os.environ.get('OPENROUTER_API_KEY')", literal[0]["plain"])
        self.assertIn("OPENROUTER_API_KEY", literal[0]["expert"])

        via = _notes("import os\nKEY = \"OPENROUTER_API_KEY\"\ndef read():\n    return os.environ.get(KEY)\n", "read()")
        self.assertEqual(via[0]["fact"]["name"], "OPENROUTER_API_KEY")
        self.assertIn("os.environ.get(KEY)", via[0]["plain"])
        local = _notes("import os\ndef read():\n    KEY = \"OPENROUTER_API_KEY\"\n    return os.environ.get(KEY)\n", "read()")
        self.assertEqual(local[0]["fact"]["name"], "OPENROUTER_API_KEY")
        sub = _notes("import os\ndef read():\n    return os.environ['K']\n", "read()")
        self.assertEqual(sub[0]["fact"]["name"], "K")
        self.assertIn("os.environ['K']", sub[0]["plain"])

        for source in (
            "import os\nKEY = \"OPENROUTER_API_KEY\"\ndef read(KEY):\n    return os.environ.get(KEY)\n",
            "import os\nKEY = 1\ndef read():\n    return os.environ.get(KEY)\n",
            "import os\nKEY = \"A\"\nKEY = \"B\"\ndef read():\n    return os.environ.get(KEY)\n",
            "import os\ndef read():\n    if True:\n        KEY = \"OPENROUTER_API_KEY\"\n    return os.environ.get(KEY)\n",
        ):
            note = _notes(source, "read()")[0]
            self.assertNotIn("name", note["fact"], source)
            self.assertNotIn("OPENROUTER_API_KEY", note["plain"] + note["expert"], source)

    def test_a_membership_check_does_not_claim_the_value_is_read_and_a_mapping_names_nothing(self):
        check = _notes("import os\ndef read():\n    return 'K' in os.environ\n", "read()")[0]
        self.assertEqual(check["fact"]["how"], "check")
        self.assertEqual(check["fact"]["name"], "K")
        self.assertIn("does not read", check["plain"])
        self.assertIn("does not read", check["expert"])

        whole = _notes("import os\ndef read():\n    return os.environ.items()\n", "read()")[0]
        self.assertNotIn("name", whole["fact"])
        self.assertIn("os.environ.items(...)", whole["expert"])
        self.assertIn("not a literal", whole["expert"])
        self.assertNotIn("HOME", whole["plain"] + whole["expert"])

    def test_input_is_a_note_and_a_local_def_or_a_caller_is_not(self):
        note = _notes("def ask_user():\n    return input('name: ')\n", "ask_user()")[0]
        self.assertEqual(note["fact"]["kind"], "input")
        self.assertEqual(note["fact"]["prompt"], "name: ")
        self.assertIn("input('name: ')", note["plain"])
        self.assertIn("what a person types", note["plain"])
        self.assertIsNone(_notes("def ask_user():\n    def input():\n        return 'x'\n    return input()\n", "ask_user()"))
        source = "import requests\ndef fetch(url):\n    requests.get(url)\ndef show():\n    return fetch('x')\n"
        self.assertIsNone(_notes(source, "show()"))
        self.assertIsNotNone(_notes(source, "fetch()"))

    def test_a_timeout_and_try_are_stated_only_when_that_call_has_them(self):
        source = (
            "import requests\ndef fetch(url):\n"
            "    try:\n        requests.get(url, timeout=5)\n    except Exception:\n        pass\n"
            "    requests.post(url)\n"
        )
        notes = _notes(source, "fetch()")
        first, second = notes[0]["fact"], notes[1]["fact"]
        self.assertEqual(first["timeout"], "passed")
        self.assertTrue(first["caught"])
        self.assertIn("A timeout is passed.", notes[0]["expert"])
        self.assertIn("inside try", notes[0]["expert"])
        self.assertEqual(second["timeout"], "absent")
        self.assertFalse(second["caught"])
        self.assertIn("No timeout is passed.", notes[1]["expert"])
        self.assertIn("not inside try", notes[1]["expert"])
        self.assertNotIn("loop", notes[1]["expert"])

        command = _notes("import os\ndef go():\n    os.system('echo')\n", "go()")[0]
        self.assertEqual(command["fact"]["way"], "command")
        self.assertNotIn("timeout", command["fact"])
        self.assertNotIn("timeout", command["expert"])
        self.assertNotIn("outside service", command["plain"])
        self.assertIn("runs a command", command["plain"])

        loop = _notes("import requests\ndef fetch(url):\n    while True:\n        requests.get(url, timeout=1)\n", "fetch()")[0]
        self.assertTrue(loop["fact"]["repeats"])
        self.assertIn("loop", loop["expert"])

    def test_a_client_constructor_a_model_call_and_a_write_get_no_security_note(self):
        self.assertIsNone(_notes("import requests\ndef make():\n    requests.Session()\n", "make()"))
        self.assertIsNone(_notes("from openai import OpenAI\ndef chat():\n    return OpenAI()\n", "chat()"))
        self.assertIsNone(_notes("def save(path):\n    open(path, 'w')\n", "save()"))
        self.assertIsNone(_notes("def note():\n    return 'requests.get'\n", "note()"))

    def test_the_repo_model_calls_are_only_what_their_code_proves(self):
        box, _skipped = python_dir(Path(__file__).resolve().parents[1])
        found = {}
        todo = [box]
        for node in todo:
            todo += node.get("children", [])
            label = (node.get("labels") or [{}])[0].get("text")
            if label in ("OpenRouter", "_open()", "ClaudeCLI", "ask()", "write_atomic()"):
                found[label] = (node.get("asbuilt") or {}).get("security") or []

        def kinds(label):
            return [note["fact"]["kind"] for note in found[label]]

        self.assertEqual(kinds("write_atomic()"), [])
        self.assertEqual(kinds("ask()"), ["env"])
        ask = found["ask()"][0]
        self.assertEqual(ask["fact"]["name"], "ASBUILT_MODEL")
        self.assertIn("os.environ.get(OPT_IN)", ask["plain"])
        self.assertNotIn("outside", ask["plain"] + ask["expert"])
        self.assertNotIn("sends", ask["plain"] + ask["expert"])

        env = next(note for note in found["OpenRouter"] if note["fact"]["kind"] == "env")
        self.assertEqual(env["fact"]["name"], "OPENROUTER_API_KEY")
        self.assertIn("os.environ.get(KEY_NAME)", env["expert"])
        self.assertNotIn("sends your data", " ".join(note["plain"] for note in found["OpenRouter"]))

        send = found["_open()"][0]
        self.assertEqual(send["fact"]["way"], "send")
        self.assertEqual(send["fact"]["timeout"], "passed")
        self.assertFalse(send["fact"]["caught"])
        self.assertIn("network request", send["plain"])
        self.assertNotIn("sends your data", send["plain"])
        self.assertIn("urllib.request.build_opener", send["plain"])
        self.assertIn("A timeout is passed.", send["expert"])
        self.assertIn("not inside try", send["expert"])

        claude = found["ClaudeCLI"]
        mapping = next(note for note in claude if note["fact"]["kind"] == "env")
        command = next(note for note in claude if note["fact"]["kind"] == "calls out")
        self.assertNotIn("name", mapping["fact"])
        self.assertIn("os.environ.items(...)", mapping["expert"])
        self.assertNotIn("HOME", mapping["plain"] + mapping["expert"])
        self.assertEqual(command["fact"]["way"], "command")
        self.assertEqual(command["fact"]["timeout"], "passed")
        self.assertTrue(command["fact"]["caught"])
        self.assertIn("subprocess.run(...)", command["plain"])
        self.assertIn("inside try", command["expert"])
        for note in claude:
            self.assertIn(note["fact"]["at"], note["plain"])
            self.assertIn(note["fact"]["at"], note["expert"])

    def test_a_timeout_is_claimed_only_when_the_call_proves_it(self):
        def expert(source, label="go()"):
            return _notes(source, label)[0]["expert"]

        self.assertIn("A timeout is passed.", expert("import socket\ndef go():\n    socket.create_connection((h, p), 3)\n"))
        self.assertIn("A timeout is passed.", expert("from urllib.request import urlopen\ndef go(url):\n    urlopen(url, None, 10)\n"))
        self.assertNotIn("timeout", expert("import requests\ndef go(url, kwargs):\n    kwargs.setdefault('timeout', 10)\n    requests.get(url, **kwargs)\n"))
        self.assertNotIn("timeout", expert("import subprocess\ndef go(cmd, opts):\n    subprocess.run(cmd, **opts)\n"))
        self.assertNotIn("timeout", expert("import httpx\ndef go(url):\n    httpx.Client(timeout=10).get(url)\n"))
        self.assertNotIn("timeout", expert("import subprocess\ndef go(cmd):\n    subprocess.Popen(cmd)\n"))
        self.assertNotIn("timeout", expert("from urllib.request import urlretrieve\ndef go(url):\n    urlretrieve(url)\n"))
        proved = _notes("import requests\ndef go(url):\n    requests.get(url)\n", "go()")[0]
        self.assertEqual(proved["fact"]["timeout"], "absent")
        self.assertIn("No timeout is passed.", proved["expert"])

    def test_a_network_sentence_says_outside_only_when_data_leaves_the_machine(self):
        local = _notes("import requests\ndef go():\n    requests.get('http://localhost/health')\n", "go()")[0]
        self.assertIn("network request", local["plain"])
        self.assertNotIn("outside", local["plain"])
        self.assertNotIn("sends your data", local["plain"])
        self.assertNotIn("/health", local["plain"])

        public = _notes("import requests\ndef go():\n    requests.get('https://example.com/status')\n", "go()")[0]
        self.assertIn("network request", public["plain"])
        self.assertNotIn("sends your data", public["plain"])
        self.assertNotIn("/status", public["plain"])
        self.assertIn("example.com", public["plain"])

        posted = _notes("import requests\ndef go(body):\n    requests.post('https://example.com/x', data=body)\n", "go()")[0]
        self.assertIn("sends your data to an outside service", posted["plain"])
        home = _notes("import requests\ndef go(body):\n    requests.post('http://127.0.0.1/x', data=body)\n", "go()")[0]
        self.assertNotIn("outside", home["plain"])
        self.assertIn("sends data", home["plain"])
        for url in ("http://0.0.0.0:8000/x", "http://127.0.0.2/x", "http://10.1.2.3/x", "http://192.168.0.8/x", "http://[::1]/x"):
            note = _notes(f"import requests\ndef go(body):\n    requests.post({url!r}, json=body)\n", "go()")[0]
            self.assertNotIn("outside", note["plain"], url)
            self.assertIn("sends data", note["plain"], url)
        public = _notes("import requests\ndef go(body):\n    requests.post('https://example.com/x', json=body)\n", "go()")[0]
        self.assertIn("sends your data to an outside service", public["plain"])

        built = _notes("from urllib.request import Request\ndef go(body):\n    Request('https://example.com/x', data=body)\n", "go()")[0]
        self.assertIn("sends your data to an outside service", built["plain"])
        payload = _notes("import requests\ndef go(url, payload):\n    requests.post(url, payload)\n", "go()")[0]
        self.assertIn("sends data", payload["plain"])
        self.assertNotIn("outside", payload["plain"])
        files = _notes("import requests\ndef go(url, fh):\n    requests.post(url, files=fh)\n", "go()")[0]
        self.assertIn("sends data", files["plain"])
        self.assertNotIn("outside", files["plain"])

    def test_env_verbs_keep_the_name_and_leave_other_argument_values_out(self):
        removed = _notes("import os\ndef go():\n    del os.environ['X']\n", "go()")[0]
        self.assertEqual(removed["fact"]["how"], "remove")
        self.assertIn("removes", removed["plain"])
        self.assertNotIn("reads", removed["plain"])
        popped = _notes("import os\ndef go():\n    os.environ.pop('X')\n", "go()")[0]
        self.assertIn("removes", popped["plain"])
        self.assertNotIn("reads", popped["plain"])
        default = _notes("import os\ndef go():\n    os.environ.setdefault('X', 'sk-proj-secret')\n", "go()")[0]
        self.assertIn("default", default["plain"])
        self.assertNotIn("reads", default["plain"])
        self.assertNotIn("sk-proj", default["plain"] + default["expert"])

        secret = _notes("import os\ndef go():\n    os.getenv('OPENAI_API_KEY', 'sk-proj-secret')\n", "go()")[0]
        self.assertEqual(secret["fact"]["name"], "OPENAI_API_KEY")
        self.assertNotIn("sk-proj", secret["plain"] + secret["expert"])

        chained = _notes("import os\ndef local_config_path():\n    return os.getenv('PT_CONFIG', '').strip()\n", "local_config_path()")[0]
        self.assertEqual(chained["fact"]["name"], "PT_CONFIG")
        self.assertIn("PT_CONFIG", chained["plain"])
        self.assertNotIn(".strip()", chained["plain"] + chained["expert"])

        prompted = _notes("def ask():\n    return input('name: ').strip()\n", "ask()")[0]
        self.assertEqual(prompted["fact"]["prompt"], "name: ")
        self.assertIn("name: ", prompted["plain"])
        self.assertNotIn(".strip()", prompted["plain"] + prompted["expert"])

    def test_socket_socket_is_not_a_connection_and_two_long_calls_stay_distinct(self):
        self.assertIsNone(_notes("import socket\ndef go():\n    socket.socket()\n", "go()"))
        opened = _notes("import socket\ndef go():\n    socket.socket().connect((h, p))\n", "go()")[0]
        self.assertEqual(opened["fact"]["way"], "connect")
        self.assertIn("opens a network connection", opened["plain"])

        long = "https://example.com/" + "a" * 120
        source = f"import requests\ndef fetch():\n    requests.get({long!r})\n    requests.post({long!r}, data='z' * 80)\n"
        notes = _notes(source, "fetch()")
        self.assertEqual(len(notes), 2)
        self.assertNotEqual(notes[0]["fact"]["start"], notes[1]["fact"]["start"])
        self.assertNotIn("a" * 40, notes[0]["plain"] + notes[1]["plain"])

    def test_a_configured_model_cannot_change_a_security_note(self):
        plan = _plan("import os\ndef read():\n    return os.environ.get('K')\n")
        note = plan["children"][0]["children"][0]["asbuilt"]["security"][0]
        box, at = plan["children"][0]["children"][0]["id"], note["fact"]["at"]

        def reply(prompt):
            if "FACTS:" in prompt or prompt.startswith("Write a plain"):
                forged = {
                    "id": box, "at": at,
                    "plain": f"{at} is perfectly safe. No timeout is passed.",
                    "expert": f"{at} sends your credentials unencrypted and disables TLS verification.",
                }
                return json.dumps({"notes": [forged]})
            return '{"notes": []}'

        with mock.patch.dict(os.environ, {"ASBUILT_MODEL": "1"}), mock.patch.object(OpenRouter, "__call__", side_effect=AssertionError("called")):
            out = annotate(plan, "plan.json", reply)
        kept = out["children"][0]["children"][0]["asbuilt"]["security"][0]
        self.assertEqual(kept["plain"], note["plain"])
        self.assertEqual(kept["expert"], note["expert"])
        self.assertNotIn("perfectly safe", kept["plain"] + kept["expert"])
        self.assertNotIn("credentials", kept["plain"] + kept["expert"])

    def test_malformed_url_does_not_crash_host_name_or_security_notes(self):
        self.assertIsNone(host_name("http://[bad"))
        notes = _notes('import requests\ndef fetch():\n    requests.get("http://[bad")\n', "fetch()")
        self.assertEqual(len(notes), 1)
        self.assertNotIn("host", notes[0]["fact"])
