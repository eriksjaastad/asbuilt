import threading


class Hub:
    """The latest message (one line from `build`, which reports problems in it, not by raising); clients wait for a newer one."""

    def __init__(self, build):
        self._build = build
        self._changed = threading.Condition()
        self._version, self._message = 0, self._built()

    def _built(self):
        message = self._build()
        if "\n" in message or "\r" in message:
            raise ValueError("a state message must be a single line")
        return message

    def refresh(self):
        """Build again and wake every waiting client. Built under the lock, so overlapping refreshes land in order."""
        with self._changed:
            self._version, self._message = self._version + 1, self._built()
            self._changed.notify_all()

    def newer_than(self, version, timeout):
        """Wait up to `timeout` s for a message newer than `version`. Return (version, message) or (version, None)."""
        with self._changed:
            self._changed.wait_for(lambda: self._version != version, timeout)
            if self._version == version:
                return version, None
            return self._version, self._message
