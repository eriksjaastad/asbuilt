"""Ordinary code for the plain-note checks. A docstring is not a note."""

import os
import subprocess

SECRET_ENV = ("TOKEN",)


def missing():
    return None


def ready():
    return "ok"


def mock():
    return '{"notes": []}'


def copy():
    subprocess.run(["rclone", "sync"])


def rclone(cmd):
    subprocess.run(cmd)


class Cache:
    def get(self):
        return None


def stream():
    yield 1
    return None


def fetch_mini_data():
    for _ in range(3):
        try:
            raise RuntimeError("nope")
        except RuntimeError:
            continue
    return None


def load_config():
    print("config was not found")
    return ""


def lookup(row):
    raise KeyError("config was not found")


def collect():
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    subprocess.run(["rclone", "sync"], env=env)


def send_email():
    import requests

    for _ in range(3):
        requests.post("https://example.com/send", data=b"hi")


class APIError(Exception):
    pass


def reject():
    raise APIError("validation", "the real message", 2)


def _built(action, exc):
    return RuntimeError(f"Failed to {action}: {exc}")


def remove(exc):
    raise _built("remove reminder", exc)


def flag_guard(force):
    if not force:
        raise RuntimeError("nope")
    return force


def always_fails_branches(force):
    import os

    key = os.environ.get("TOKEN")
    if not key:
        raise RuntimeError("missing")
    return force
