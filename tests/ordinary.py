"""Ordinary code for the plain-note checks. A docstring is not a note."""

import os
import subprocess


def label(ok):
    if ok:
        return "just now"
    return "yesterday"


def gen():
    yield "a"
    return "a"


def agent_name():
    name = os.environ.get("AGENT_NAME")
    if not name:
        return None
    return name


def load():
    name = os.environ.get("AGENT_NAME")
    if not name:
        raise RuntimeError("no name")
    return name


def revert(path):
    try:
        path.unlink()
    except OSError as exc:
        print(f"refused: {exc}")
    return []


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
