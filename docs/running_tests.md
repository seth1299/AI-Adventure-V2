# Running tests

Use **All Tests** in `test_runner.py`, or run `All_Tests.bat`. The launcher uses
the project virtual environment. Each discovered test package runs in its own
Python process with fault handling enabled. A package failure or crash does not
stop subsequent packages. The final summary reports package outcomes and totals;
the overall exit code is nonzero if any package fails, aborts, or crashes.
Test counts from a crashed package are unavailable and excluded from the totals.
Individual package batch files also enable fault handling.

Output from passing tests is buffered. Failed tests retain their captured output
and traceback. The fault handler writes native crash diagnostics directly to the
log, even while test output is buffered. Test discovery/import messages may still
appear before individual tests start. The desktop launcher can save the full log.

## Expected exceptions

An exception that is part of the behavior under test should be asserted:

```python
with self.assertRaisesRegex(ValueError, "quantity must be positive"):
    operation(quantity=-1)
```

This is a **passing test** when the matching exception occurs. No exception, a
different exception type, or an incorrect message fails the test. `assertRaises`
also accepts subclasses; to require precisely one class:

```python
with self.assertRaises(ValueError) as caught:
    operation(quantity=-1)
self.assertIs(type(caught.exception), ValueError)
```

Use `assertLogs` when a test needs to verify an expected error log. Buffering
reduces console noise but does not replace assertions about exceptions or logs.
Unexpected exceptions are never automatically converted into passes.

`@unittest.expectedFailure` has a different purpose: documenting a known broken
test. These are listed separately as known-bug expected failures, not passes.
An unexpected success fails the run so the obsolete decorator can be removed.
Do not use this decorator to hide a stale fixture or ordinary exception test.
