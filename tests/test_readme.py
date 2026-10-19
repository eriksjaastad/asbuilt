import contextlib
import io
import json
import re
import unittest
from pathlib import Path

from asbuilt import cli, plan

README = (Path(__file__).parent.parent / "README.md").read_text()


class ReadmeTest(unittest.TestCase):
    def test_the_defaults_it_states_are_the_commands_defaults(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["--help"])
        for default in re.findall(r"default (\S+)", out.getvalue()):
            self.assertIn(default, README)

    def test_the_example_plan_is_one_the_command_reads(self):
        example = re.search(r"```json\n(.*?)```", README, re.S).group(1)
        self.assertEqual(plan.parse(example.encode(), "example"), json.loads(example))
