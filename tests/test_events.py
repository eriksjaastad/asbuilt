import contextlib
import io
import time
from unittest import mock

from test_server import ServerCase


def event(resp):
    """The next message on the stream, as its lines."""
    lines = []
    while (line := resp.readline()) != b"\n":
        if not line:
            raise EOFError("the stream ended")
        lines.append(line.decode().rstrip("\n"))
    return lines


class EventsTest(ServerCase):
    def test_a_client_gets_the_current_message_then_each_change_and_keep_alives(self):
        resp = self.request("/events?x=1")
        self.assertEqual(resp.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertEqual(event(resp), ['data: {"n": 1}'])
        idle = time.monotonic()
        self.assertEqual(event(resp), [": keep-alive"])
        self.assertGreaterEqual(time.monotonic() - idle, 0.04)  # it waits, not spins
        self.n = 2
        self.hub.refresh()
        while (lines := event(resp)) == [": keep-alive"]:
            pass
        self.assertEqual(lines, ['data: {"n": 2}'])

    def test_a_client_that_goes_away_neither_breaks_the_server_nor_prints_an_error(
        self,
    ):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            resp = self.request("/events")
            event(resp)
            resp.close()
            time.sleep(0.5)
            self.assertEqual(self.request("/").status, 200)
        self.assertEqual(errors.getvalue(), "")

    def test_a_real_error_in_a_stream_is_still_printed(self):
        for error in [RuntimeError("boom"), OSError("disk full")]:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                with mock.patch.object(self.hub, "newer_than", side_effect=error):
                    self.request("/events").read()  # ends when the stream is dropped
            self.assertIn(f"{type(error).__name__}: {error}", errors.getvalue())
