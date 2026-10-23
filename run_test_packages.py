"""Run unittest packages in separate processes and retain every package result."""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CountingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = 0

    def addSuccess(self, test):
        self.passed += 1
        super().addSuccess(test)


def discover_packages(root: Path) -> list[str]:
    tests = root / "tests"
    packages = sorted(
        folder.name for folder in tests.iterdir()
        if folder.is_dir() and (folder / "__init__.py").is_file()
        and any(folder.rglob("test_*.py"))
    )
    if any(tests.glob("test_*.py")):
        packages.append("__root__")
    return packages


def run_package(root: Path, package: str, result_path: Path) -> int:
    faulthandler.enable(all_threads=True)
    sys.path.insert(0, str(root))
    loader = unittest.TestLoader()
    if package == "__root__":
        suite = loader.loadTestsFromNames([
            f"tests.{path.stem}" for path in sorted((root / "tests").glob("test_*.py"))
        ])
    else:
        suite = loader.discover(str(root / "tests" / package), top_level_dir=str(root))
    result = unittest.TextTestRunner(
        verbosity=2, buffer=True, resultclass=CountingResult,
    ).run(suite)
    summary = {
        "package": package, "tests": result.testsRun, "passed": result.passed,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful() and (result.testsRun > 0 or bool(result.skipped)),
    }
    result_path.write_text(json.dumps(summary), encoding="utf-8")
    return 0 if summary["successful"] else 1


def exit_description(code: int) -> str:
    if code < 0 or code > 0x7FFFFFFF:
        return f"{code} (0x{code & 0xFFFFFFFF:08X})"
    return str(code)


def run_all(root: Path) -> int:
    packages = discover_packages(root)
    if not packages:
        print("ERROR: No test packages found.", flush=True)
        return 1
    summaries = []
    environment = {**os.environ, "PYTHONFAULTHANDLER": "1", "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
    with tempfile.TemporaryDirectory(prefix="ai-adventure-test-results-") as directory:
        for index, package in enumerate(packages, 1):
            result_path = Path(directory) / f"{package}.json"
            print(f"\n{'=' * 64}\n[{index}/{len(packages)}] {package} - isolated process, fault handler ON\n{'=' * 64}", flush=True)
            command = [sys.executable, "-X", "faulthandler", "-u", str(Path(__file__).resolve()),
                       "--root", str(root), "--package", package, "--result", str(result_path)]
            try:
                process = subprocess.run(command, cwd=root, env=environment,
                                         stdout=sys.stdout, stderr=sys.stderr,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                code = process.returncode
                report = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
                if report is None:
                    status = "CRASHED" if code < 0 or code > 0x7FFFFFFF else "ABORTED"
                else:
                    status = "PASS" if code == 0 and report["successful"] else "FAIL"
                summaries.append((package, status, code, report))
                print(f"\n{package}: {status}; exit {exit_description(code)}", flush=True)
            except (OSError, ValueError, KeyError) as error:
                print(f"\n{package}: RUNNER ERROR: {error}", flush=True)
                summaries.append((package, "RUNNER ERROR", None, None))

    print(f"\n{'=' * 64}\nALL TESTS - AGGREGATE RESULTS\n{'=' * 64}", flush=True)
    totals = {key: 0 for key in ("tests", "passed", "failures", "errors", "skipped", "expected_failures", "unexpected_successes")}
    for package, status, code, report in summaries:
        if report is not None:
            for key in totals:
                totals[key] += report[key]
            print(f"{package}: {status} | tests={report['tests']} passed={report['passed']} "
                  f"failures={report['failures']} errors={report['errors']} skipped={report['skipped']} "
                  f"known-bug expected failures={report['expected_failures']} unexpected successes={report['unexpected_successes']}")
        else:
            print(f"{package}: {status} | exit={exit_description(code) if code is not None else 'unavailable'}; test counts unavailable")
    print("\nReported totals: " + ", ".join(f"{key}={value}" for key, value in totals.items()))
    print("Counts exclude unfinished packages. Failures/errors may include multiple subtest failures.")
    print("Expected exceptions asserted by tests count as passes. Known-bug expected failures are listed separately.")
    successful = all(status == "PASS" for _, status, _, _ in summaries)
    print("OVERALL: " + ("PASSED" if successful else "FAILED"), flush=True)
    return 0 if successful else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--package")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.package:
        if args.result is None:
            parser.error("--package requires --result")
        return run_package(args.root.resolve(), args.package, args.result)
    return run_all(args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
