import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from agentodoo import __version__
from agentodoo.cli import main


def run(argv, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(argv, **kwargs)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class CliBasicsTests(unittest.TestCase):
    def test_version_prints_package_version(self):
        code, out, err = run(["--version"])
        self.assertEqual(0, code)
        self.assertEqual(f"agentodoo {__version__}\n", out)
        self.assertEqual("", err)

    def test_no_command_shows_usage_and_fails(self):
        code, out, err = run([])
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("usage: agentodoo", err)


if __name__ == "__main__":
    unittest.main()
