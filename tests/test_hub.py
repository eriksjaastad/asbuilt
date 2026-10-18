import threading
import unittest

from asbuilt.hub import Hub


class HubTest(unittest.TestCase):
    def setUp(self):
        self.n = 0
        self.hub = Hub(self.build)

    def build(self):
        self.n += 1
        return f"message {self.n}"

    def test_a_new_client_gets_the_current_message_at_once_then_waits_for_a_change(
        self,
    ):
        self.assertEqual(self.hub.newer_than(-1, 0), (0, "message 1"))
        self.assertEqual(self.hub.newer_than(0, 0.01), (0, None))

    def test_a_change_wakes_a_waiting_client_with_the_new_message(self):
        got = []
        waiter = threading.Thread(
            target=lambda: got.append(self.hub.newer_than(0, 30)), daemon=True
        )
        waiter.start()
        self.hub.refresh()
        waiter.join(5)
        self.assertEqual(got, [(1, "message 2")])

    def test_a_slow_client_skips_to_the_latest_message(self):
        for _ in range(3):
            self.hub.refresh()
        self.assertEqual(self.hub.newer_than(0, 0), (3, "message 4"))

    def test_overlapping_refreshes_land_in_the_order_they_started(self):
        gate, started = threading.Event(), threading.Event()

        def build():  # the first refresh (build 2) is held up; build 3 would finish first
            self.n += 1
            mine = self.n
            if mine == 2:
                started.set()
                gate.wait(5)
            return f"message {mine}"

        self.n = 0
        self.hub = Hub(build)
        first = threading.Thread(target=self.hub.refresh)
        first.start()
        started.wait(5)
        second = threading.Thread(target=self.hub.refresh)
        second.start()
        second.join(
            0.2
        )  # with the lock the second waits its turn; without it, it finishes first
        gate.set()
        first.join(5)
        second.join(5)
        self.assertEqual(self.hub.newer_than(-1, 0), (2, "message 3"))

    def test_a_message_of_more_than_one_line_is_refused_and_changes_nothing(self):
        for bad in ["a\nb", "a\rb", "a\r\nb"]:
            with self.assertRaises(ValueError):
                Hub(lambda: bad)
        hub = Hub(iter(["fine", "a\nb"]).__next__)
        with self.assertRaises(ValueError):
            hub.refresh()
        self.assertEqual(hub.newer_than(-1, 0), (0, "fine"))
