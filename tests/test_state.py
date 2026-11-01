import contextlib
import io
import json
import unittest

from asbuilt.state import FALLBACK, NOT_SENDABLE, PlanError, build


def parsed(source):
    message = build(source)
    assert "\n" not in message and message.isascii()
    return json.loads(message)


class Meta(type):
    @property
    def __name__(cls):
        raise RuntimeError("the class name fails")


class Hostile(Exception, metaclass=Meta):
    def __str__(self):
        raise RuntimeError("the message fails")


def raising(error):
    def source():
        raise error

    return source


class StateTest(unittest.TestCase):
    def test_a_plan_becomes_an_ok_message_with_the_plan_unchanged(self):
        plan = {"id": "root", "children": [{"id": "a", "label": "caf\u00e9"}]}
        self.assertEqual(parsed(lambda: plan), {"ok": True, "plan": plan})

    def test_a_plan_error_shows_its_own_text(self):
        self.assertEqual(
            parsed(raising(PlanError("plan.json is missing\nsecond line"))),
            {"ok": False, "error": "plan.json is missing\nsecond line"},
        )

    def test_any_other_failure_gets_the_fixed_sentence_and_none_of_its_text(self):
        for error in (
            RuntimeError("secret"),
            KeyError("secret"),
            Hostile(),
            PlanError(Hostile()),
        ):
            with self.subTest(error=repr(error.args)):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(
                        parsed(raising(error)), {"ok": False, "error": FALLBACK}
                    )
                self.assertIn("Traceback", err.getvalue())

    def test_numbers_json_cannot_hold_are_refused_in_one_place(self):
        for number in (float("nan"), float("inf"), float("-inf"), 1e400):
            self.assertEqual(
                parsed(lambda number=number: {"id": "r", "width": number}),
                {"ok": False, "error": NOT_SENDABLE},
            )

    def test_a_plan_that_is_not_json_data_gets_the_message_not_a_crash(self):
        self.assertEqual(
            parsed(lambda: {"id": object()}), {"ok": False, "error": NOT_SENDABLE}
        )
