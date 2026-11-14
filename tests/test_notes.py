import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asbuilt.connections import ConnectionFailed
from asbuilt.notes import annotate, main
from asbuilt.state import PlanError

SENTENCE = "This function calls one other function and returns whatever that returns."


def chart():
    child = lambda i, text, **extra: {"id": i, "labels": [{"text": f"{i}()"}], "asbuilt": {"text": text, **extra}}
    return {"id": "asbuilt:built", "children": [{"id": "t.py", "labels": [{"text": "t.py"}], "children": [
        child("f", "def f():\n    return g()\n", proven=SENTENCE), child("h", "def h():\n    return 1\n"),
    ]}]}


def ids(plan):
    found, todo = [], [plan]
    while todo:
        box = todo.pop()
        todo.extend(box.get("children") or [])
        found.append(box["id"])
    return sorted(found)


class NotesTest(unittest.TestCase):
    def test_a_valid_note_lands_on_that_box_and_the_mock_adds_none(self):
        plan = chart()
        reply = '```json\n{"notes": [{"id": "f", "label": "tricky part", "text": " the name hides the call "}]}\n```'
        out = annotate(plan, "plan.json", lambda prompt: reply)
        self.assertEqual(ids(out), ids(plan))
        noted, other = [c["asbuilt"] for c in out["children"][0]["children"]]
        self.assertEqual(noted["model"], [{"label": "tricky part", "text": "the name hides the call"}])
        self.assertEqual(noted["proven"], SENTENCE)
        self.assertNotIn("model", other)
        with mock.patch.dict(os.environ, {"ASBUILT_MODEL": ""}):
            self.assertNotIn("model", annotate(plan, "plan.json")["children"][0]["children"][0]["asbuilt"])
        before = json.dumps(plan)
        bad = (
            "not json",
            '{"notes": [{"id": "no-such", "label": "tricky part", "text": "x"}]}',
            '{"notes": [{"id": "f", "label": "guess", "text": "x"}]}',
            '{"notes": [{"id": "f", "label": "common pattern", "text": "two\\nlines"}]}',
        )
        for reply in bad:
            with self.assertRaises(PlanError):
                annotate(plan, "plan.json", lambda prompt, reply=reply: reply)
        self.assertEqual(json.dumps(plan), before)

    def test_a_bad_reply_or_a_failed_call_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp, "plan.json"), Path(tmp, "out.json")
            src.write_text(json.dumps(chart()))
            quiet = mock.patch.dict(os.environ, {"ASBUILT_MODEL": ""})
            with quiet:
                self.assertEqual(main([str(src), str(out)]), 0)
            kept = out.read_text()
            written = json.loads(kept)
            self.assertNotIn("model", written["children"][0]["children"][0]["asbuilt"])
            self.assertEqual(ids(written), ids(chart()))
            with quiet, mock.patch("asbuilt.notes.ask", return_value="not json"), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(src), str(out)]), 2)
            self.assertEqual(err.getvalue().count("\n"), 1)
            self.assertTrue(err.getvalue().startswith("asbuilt.notes: "))
            failed = ConnectionFailed("OpenRouter could not be reached.")
            with quiet, mock.patch("asbuilt.notes.ask", side_effect=failed), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([str(src), str(out)]), 2)
            self.assertIn("could not be reached", err.getvalue())
            self.assertEqual(out.read_text(), kept)
