"""Check process isolation, continuation, and unittest exception semantics."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from run_test_packages import discover_packages, exit_description


class PackageRunnerTests(unittest.TestCase):
    def test_packages_continue_after_assertions_and_process_abort(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "tests"
            tests.mkdir()
            (tests / "__init__.py").touch()
            sources = {
                "A_Tests": '''import unittest, os
from pathlib import Path
class ExpectedExceptions(unittest.TestCase):
    def test_expected_exception_passes(self):
        Path("first-pid.txt").write_text(str(os.getpid()))
        print("EXPECTED OUTPUT HIDDEN")
        with self.assertRaisesRegex(ValueError, "specific reason"):
            raise ValueError("specific reason")
    def test_wrong_exception_is_error(self):
        print("ACTUAL OUTPUT RETAINED")
        with self.assertRaises(ValueError):
            raise TypeError("wrong exception")
    def test_missing_exception_fails(self):
        with self.assertRaises(ValueError):
            pass
    @unittest.expectedFailure
    def test_known_bug(self):
        self.fail("known bug")
    @unittest.expectedFailure
    def test_unexpected_success(self):
        pass
''',
                "B_Tests": '''import os, unittest
class AbortedProcess(unittest.TestCase):
    def test_abort(self):
        os._exit(9)
''',
                "C_Tests": '''import faulthandler, os, unittest
from pathlib import Path
class AfterAbort(unittest.TestCase):
    def test_still_runs(self):
        self.assertTrue(faulthandler.is_enabled())
        self.assertNotEqual(int(Path("first-pid.txt").read_text()), os.getpid())
        Path("after-abort.txt").write_text(str(os.getpid()))
''',
                "D_Tests": '''import unittest
class UnavailableFeature(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise unittest.SkipTest("optional feature unavailable")
    def test_optional_feature(self):
        pass
''',
            }
            for package, source in sources.items():
                folder = tests / package
                folder.mkdir()
                (folder / "__init__.py").touch()
                (folder / "test_case.py").write_text(source, encoding="utf-8")
            self.assertEqual(discover_packages(root), list(sources))
            runner = Path(__file__).resolve().parents[2] / "run_test_packages.py"
            process = subprocess.run(
                [sys.executable, "-X", "faulthandler", str(runner), "--root", str(root)],
                text=True, encoding="utf-8", capture_output=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = process.stdout + process.stderr
            self.assertEqual(process.returncode, 1, output)
            self.assertIn("A_Tests: FAIL | tests=5 passed=1 failures=1 errors=1", output)
            self.assertIn("known-bug expected failures=1 unexpected successes=1", output)
            self.assertIn("B_Tests: ABORTED", output)
            self.assertIn("C_Tests: PASS", output)
            self.assertIn("D_Tests: PASS | tests=0 passed=0 failures=0 errors=0 skipped=1", output)
            self.assertIn("OVERALL: FAILED", output)
            self.assertNotIn("EXPECTED OUTPUT HIDDEN", output)
            self.assertIn("ACTUAL OUTPUT RETAINED", output)
            self.assertIn("TypeError: wrong exception", output)
            self.assertTrue((root / "after-abort.txt").is_file())

    def test_windows_crash_code_is_readable_signed_or_unsigned(self):
        self.assertIn("0xC0000005", exit_description(-1073741819))
        self.assertIn("0xC0000005", exit_description(3221225477))

    def test_empty_suite_cannot_be_reported_as_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            runner = Path(__file__).resolve().parents[2] / "run_test_packages.py"
            process = subprocess.run([sys.executable, str(runner), "--root", str(root)],
                                     text=True, capture_output=True, timeout=15,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(process.returncode, 1)
            self.assertIn("No test packages", process.stdout)
