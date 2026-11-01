import functools
import io
import os
import tempfile
import unittest.mock
from pathlib import Path

from asbuilt.output import fail, plan_bytes, write_atomic
from asbuilt.state import PlanError


class OutputTest(unittest.TestCase):
    def why(self, call, *args):  # the PlanError's reason
        with self.assertRaises(PlanError) as caught:
            call(*args)
        return str(caught.exception).partition(" cannot be written: ")[2]

    def test_plan_bytes_are_the_file_asbuilt_reads_up_to_its_limit(self):
        n = 5_000_000 - 14  # the longest "k" text whose file is exactly MAX_BYTES
        self.assertEqual(plan_bytes({"id": "é"}, "O"), b'{\n  "id": "\xc3\xa9"\n}\n')
        self.assertEqual(len(plan_bytes({"k": "a" * n}, "OUT")), 5_000_000)
        self.assertIn("over 5000000", self.why(plan_bytes, {"k": "a" * n + "a"}, "O"))
        deep = functools.reduce(lambda inner, _: [inner], range(100000), [])
        for bad in ["\ud800", float("nan"), float("-inf"), 10**5000, deep]:
            self.assertIn("not Unicode, NaN,", self.why(plan_bytes, [bad], "OUT"))

    def test_write_atomic_writes_whole_through_a_link_or_not_at_all(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out, link, plain = self.dir / "OUT", self.dir / "link", self.dir / "plain"
        self.addCleanup(os.umask, os.umask(0o027))  # not the usual 022, and kept
        link.symlink_to("OUT")
        plain.touch()
        write_atomic(link, b"new")
        write_atomic(out, b"again")
        got = (out.read_bytes(), out.stat().st_mode, link.is_symlink())
        self.assertEqual(got, (b"again", plain.stat().st_mode, True))
        # a failed write leaves the file as it was and no temporary file
        for call in ["os.fsync", "os.replace"]:
            with unittest.mock.patch(call, side_effect=OSError(28, "why")):
                self.assertEqual(self.why(write_atomic, out, b"new"), "why.")
        got = (out.read_bytes(), sorted(os.listdir(self.dir)))
        self.assertEqual(got, (b"again", ["OUT", "link", "plain"]))
        self.assertIn("No such file", self.why(write_atomic, self.dir / "no/OUT", b""))
        fails = {"side_effect": OSError(13, "no")}
        with (
            unittest.mock.patch("os.replace", **fails),
            unittest.mock.patch("os.unlink", **fails),
        ):
            self.assertIn("could not be removed: no.", self.why(write_atomic, out, b""))

    def test_fail_prints_the_whole_message_as_one_line_and_gives_2(self):
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = fail("x", PlanError("plan\ncopy.json: a\r\nb\u2028c"))
        self.assertEqual((code, err.getvalue()), (2, "x: plan copy.json: a b c\n"))
