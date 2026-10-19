import io
import math
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from asbuilt import plan
from asbuilt.plan import read_bytes
from asbuilt.state import PlanError


class ReadBytesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = self.dir / "plan.json"

    def refused(self, path=None):
        with self.assertRaises(PlanError) as caught:
            read_bytes(path or self.path)
        return str(caught.exception)

    def test_the_bytes_of_a_file_come_back_exactly(self):
        self.path.write_bytes(b'{"id": "r"}\xff\x00')
        self.assertEqual(read_bytes(self.path), b'{"id": "r"}\xff\x00')

    def test_a_pipe_is_refused_without_waiting_for_it(self):
        pipe = self.dir / "pipe.json"
        os.mkfifo(pipe)
        self.addCleanup(
            lambda: os.close(os.open(pipe, os.O_RDWR))
        )  # frees a reader stuck on the pipe
        said = []
        reader = threading.Thread(
            target=lambda: said.append(self.refused(pipe)), daemon=True
        )
        reader.start()
        reader.join(5)
        self.assertEqual(said, ["pipe.json is not a regular file."])

    def test_a_file_that_cannot_be_used_is_named_with_the_reason(self):
        (self.dir / "file.txt").write_bytes(b"")
        for path, why in (
            (self.dir / "file.txt" / "plan.json", "cannot be read: Not a directory."),
            (self.dir, "cannot be read: Is a directory."),
        ):
            self.assertEqual(self.refused(path), f"{path.name} {why}")

    def test_a_missing_file_is_named_with_the_full_path_it_was_looked_for_at(self):
        nowhere = Path(
            "no-such-folder", "plan.json"
        )  # relative, as typed or the default
        self.assertEqual(
            self.refused(nowhere),
            f"plan.json was not found: {Path.cwd() / nowhere}. "
            "Save a plan there, or start asbuilt with its path.",
        )

    def test_the_size_limit_is_exact(self):
        self.path.write_bytes(b" " * plan.MAX_BYTES)
        self.assertEqual(len(read_bytes(self.path)), plan.MAX_BYTES)
        self.path.write_bytes(b" " * (plan.MAX_BYTES + 1))
        self.assertEqual(self.refused(), f"plan.json is over {plan.MAX_BYTES} bytes.")

    def test_it_reads_at_most_one_byte_past_the_limit(self):
        self.path.write_bytes(b" " * (plan.MAX_BYTES + 100))
        sizes = []

        class Spy(io.BufferedReader):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)

        def spy(path, mode, opener):
            return Spy(io.FileIO(path, "r", opener=opener))

        with mock.patch.object(plan, "open", spy, create=True):
            self.refused()
        self.assertEqual(sizes, [plan.MAX_BYTES + 1])


class ParseTest(unittest.TestCase):
    def refused(self, raw):
        with self.assertRaises(PlanError) as caught:
            plan.parse(raw, "plan.json")
        return str(caught.exception)

    def test_json_parses_plainly_and_numbers_are_left_for_the_message_step(self):
        self.assertEqual(
            plan.parse(b'{"id": "r", "k": [1, 2.5, "caf\xc3\xa9"]}', "p"),
            {"id": "r", "k": [1, 2.5, "caf\u00e9"]},
        )
        nan, huge = plan.parse(b"[NaN, 1e400]", "p")
        self.assertTrue(math.isnan(nan) and math.isinf(huge))

    def test_text_that_is_not_json_names_the_file_and_where(self):
        self.assertRegex(
            self.refused(b'{"a": }'),
            r"^plan\.json is not valid JSON: .*line 1 column 7",
        )
        self.assertRegex(
            self.refused(b"\xef\xbb\xbf{}"), r"^plan\.json is not valid JSON: "
        )

    def test_bytes_that_are_not_utf8_are_refused(self):
        self.assertEqual(self.refused(b'{"a": "\xff"}'), "plan.json is not UTF-8 text.")

    def test_a_repeated_key_is_refused_wherever_it_is(self):
        for raw in (b'{"id": 1, "id": 2}', b'{"children": [{"x": 1, "x": 2}]}'):
            self.assertRegex(
                self.refused(raw),
                r"^plan\.json repeats the key '(id|x)' in one object\.$",
            )

    def test_a_number_with_too_many_digits_and_a_deep_nest_are_refused(self):
        old = sys.get_int_max_str_digits()
        self.addCleanup(sys.set_int_max_str_digits, old)
        sys.set_int_max_str_digits(640)
        self.assertEqual(
            self.refused(b"[" + b"1" * 700 + b"]"),
            "plan.json has a whole number with too many digits.",
        )
        self.assertEqual(self.refused(b"[" * 200000), "plan.json is nested too deeply.")

    def test_a_snapshot_is_parsed_or_its_reason_raised(self):
        self.assertEqual(plan.parse_snapshot(b'{"id": "r"}', "p"), {"id": "r"})
        with self.assertRaisesRegex(PlanError, "^plan.json was not found.$"):
            plan.parse_snapshot("plan.json was not found.", "plan.json")
