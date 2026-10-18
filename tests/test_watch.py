import tempfile
import threading
import unittest
from pathlib import Path

from asbuilt.watch import snapshot, watch


class Script:  # stands in for threading.Event: each wait() applies the next step to the file
    def __init__(self, path, steps):
        self.path, self.steps = path, list(steps)

    def wait(self, tick):
        assert tick == 0.1, "the watch must wait the tick it was given"
        if not self.steps:
            return True
        step = self.steps.pop(0)
        if step is not None:
            self.path.write_bytes(step)
        elif self.path.exists():  # moved away: gone from the plan's name
            self.path.rename(self.path.with_name("moved"))
        return False


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "plan.json"
        self.path.write_bytes(b"A")
        self.calls = []

    def run_steps(self, *steps, on_change=None):
        watch(
            self.path,
            on_change or (lambda: self.calls.append(snapshot(self.path))),
            Script(self.path, steps),
        )
        return self.calls

    def test_a_change_is_reported_once_when_it_settles_not_while_half_written(self):
        self.assertEqual(self.run_steps(b"{", b"{ ", b"{ }", b"{ }"), [b"{ }"])

    def test_the_first_look_the_same_bytes_and_an_undone_change_are_not_reported(self):
        for steps in ((b"A", b"A", b"A"), (b"B", b"A", b"A")):
            self.assertEqual(self.run_steps(*steps), [])

    def test_a_deleted_file_is_a_change_and_so_is_its_return(self):
        self.assertEqual(
            self.run_steps(None, None, b"A", b"A"), ["plan.json was not found.", b"A"]
        )

    def test_an_error_from_the_callback_ends_the_watch_and_reaches_the_caller(self):
        def fail():
            self.calls.append("called")
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            self.run_steps(b"B", b"B", b"C", b"C", on_change=fail)
        self.assertEqual(self.calls, ["called"])

    def test_a_real_save_is_reported_to_a_real_thread_until_stopped(self):
        changed, looked, stop = threading.Event(), threading.Event(), threading.Event()
        stop.wait = lambda tick: looked.set() or threading.Event.wait(
            stop, tick
        )  # set after the first look
        thread = threading.Thread(
            target=watch, args=(self.path, changed.set, stop, 0.01), daemon=True
        )
        thread.start()
        self.assertTrue(looked.wait(10))
        self.path.write_bytes(b"B")
        self.assertTrue(changed.wait(10))
        stop.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
