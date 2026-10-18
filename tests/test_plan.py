import io
import os
import shutil
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
            (self.path, "was not found."),
            (self.dir / "file.txt" / "plan.json", "cannot be read: Not a directory."),
            (self.dir, "cannot be read: Is a directory."),
        ):
            self.assertEqual(self.refused(path), f"{path.name} {why}")

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
