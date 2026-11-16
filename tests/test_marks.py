import unittest
from pathlib import Path

from asbuilt.built import python_dir, python_file


def _badges(source):
    box = python_file("t.py", source.encode())
    return {child["labels"][0]["text"]: child["asbuilt"].get("badges", []) for child in box["children"]}


class MarkTest(unittest.TestCase):
    def test_an_environment_read_is_marked_env(self):
        marks = _badges("import os\ndef read():\n    return os.environ.get('KEY')\n")
        self.assertEqual(marks["read()"], ["env"])

    def test_input_is_marked_input(self):
        marks = _badges("def ask_user():\n    return input()\n")
        self.assertEqual(marks["ask_user()"], ["input"])

    def test_opening_a_file_to_write_is_marked_writes_and_reading_one_is_not(self):
        marks = _badges("def save(path):\n    open(path, 'w')\ndef load(path):\n    open(path)\n")
        self.assertEqual(marks["save()"], ["writes"])
        self.assertEqual(marks["load()"], [])

    def test_a_call_out_is_marked_on_that_box_and_not_on_a_box_that_only_calls_it(self):
        source = "import requests\ndef fetch():\n    requests.get('https://example.test')\ndef show():\n    return fetch()\ndef note():\n    return 'requests.get'\n"
        marks = _badges(source)
        self.assertEqual(marks["fetch()"], ["calls out"])
        self.assertEqual(marks["show()"], [])
        self.assertEqual(marks["note()"], [])

    def test_the_repo_marks_its_note_calls_and_its_environment_reads(self):
        box, _skipped = python_dir(Path(__file__).resolve().parents[1])
        found = {}
        todo = [box]
        for node in todo:
            todo += node.get("children", [])
            label = (node.get("labels") or [{}])[0].get("text")
            badges = (node.get("asbuilt") or {}).get("badges")
            if badges:
                found[label] = badges
        self.assertEqual(found["OpenRouter"], ["env", "calls out"])
        self.assertEqual(found["ClaudeCLI"], ["env", "calls out"])
        self.assertEqual(found["ask()"], ["env"])
