import json
import os
import unittest
from pathlib import Path
from unittest import mock

from asbuilt.built import python_dir, python_file
from asbuilt.connections import OpenRouter, ask
from asbuilt.notes import annotate


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
        self.assertIn("os.environ.items()", whole["expert"])
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
        self.assertIn("sends your data", send["plain"])
        self.assertIn("urllib.request.build_opener", send["plain"])
        self.assertIn("A timeout is passed.", send["expert"])
        self.assertIn("not inside try", send["expert"])

        claude = found["ClaudeCLI"]
        mapping = next(note for note in claude if note["fact"]["kind"] == "env")
        command = next(note for note in claude if note["fact"]["kind"] == "calls out")
        self.assertNotIn("name", mapping["fact"])
        self.assertIn("os.environ.items()", mapping["expert"])
        self.assertNotIn("HOME", mapping["plain"] + mapping["expert"])
        self.assertEqual(command["fact"]["way"], "command")
        self.assertEqual(command["fact"]["timeout"], "passed")
        self.assertTrue(command["fact"]["caught"])
        self.assertIn("subprocess.run", command["plain"])
        self.assertIn("inside try", command["expert"])
        for note in claude:
            self.assertIn(note["fact"]["at"], note["plain"])
            self.assertIn(note["fact"]["at"], note["expert"])

    def test_a_model_sentence_is_kept_only_when_it_repeats_the_fact_and_the_mock_is_not_asked(self):
        source = "import os\nKEY = \"OPENROUTER_API_KEY\"\ndef read():\n    return os.environ.get(KEY)\n"
        plan = _plan(source)
        note = plan["children"][0]["children"][0]["asbuilt"]["security"][0]
        at, box = note["fact"]["at"], plan["children"][0]["children"][0]["id"]
        seen = []

        def reply(prompt):
            seen.append(prompt)
            if "FACTS:" in prompt:
                good = {"id": box, "at": at, "plain": f"Plain wrapper {at}.", "expert": f"Expert wrapper {at}."}
                return json.dumps({"notes": [good, {"id": box, "at": at, "plain": "reads 'OTHER'", "expert": f"nope {at}"}]})
            return '{"notes": []}'

        out = annotate(plan, "plan.json", reply)
        kept = out["children"][0]["children"][0]["asbuilt"]["security"][0]
        self.assertEqual(kept["plain"], f"Plain wrapper {at}.")
        self.assertEqual(kept["expert"], f"Expert wrapper {at}.")
        self.assertEqual(kept["fact"], note["fact"])
        self.assertNotIn("OTHER", kept["plain"] + kept["expert"])
        facts_prompt = next(prompt for prompt in seen if prompt.startswith("Write a plain"))
        self.assertIn(at, facts_prompt)
        self.assertNotIn("def read", facts_prompt)

        prompts = []

        def spy(prompt, connection=None):
            prompts.append(prompt)
            return ask(prompt, connection)

        with mock.patch.dict(os.environ, {"ASBUILT_MODEL": ""}), mock.patch.object(OpenRouter, "__call__", side_effect=AssertionError("called")):
            with mock.patch("asbuilt.notes.ask", side_effect=spy):
                same = annotate(plan, "plan.json")
        self.assertEqual(same["children"][0]["children"][0]["asbuilt"]["security"][0]["plain"], note["plain"])
        self.assertTrue(prompts)
        self.assertFalse(any(prompt.startswith("Write a plain") for prompt in prompts))
