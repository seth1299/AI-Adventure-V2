# UI test performance

`UI_Tests.bat` runs real Qt tests sequentially and reports the 15 slowest tests.
The GUI test runner uses this same batch file. For a smaller check, run a module
or individual test with the project interpreter, for example:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest tests.UI_Tests.test_window_layout -v --durations 15
```

Full-window construction, SQLite save setup, font/theme changes, and the
separate-process restart check naturally cost more than pure logic tests.
Do not remove their assertions or replace real interaction with fixed sleeps to
improve timings. Screenshot capture has an optional wait only when explicitly
enabled through `AI_ADVENTURE_UI_CAPTURE_DIR`.

Widget tests should inherit `tests.qt_test_case.QtTestCase`. It preserves the
process-wide QApplication and pre-existing widgets, deletes each test's new
top-level widgets, and drains deferred deletion after all normal test cleanups.
`QWidget.close()` alone usually hides a widget; hidden, undeleted widgets still
participate in application-wide font and stylesheet changes. Cleanup is installed
through `run()` so custom `setUp` methods cannot accidentally omit it.

Deferred application callbacks should use `QTimer.singleShot(delay, owner, callback)`
so destroying the owner cancels them. Destruction-time signal handlers must also
check that a widget is still valid before scheduling work.

Pure parsing and prompt-composition assertions belong in the AI/application tests,
without creating windows or temporary save databases. Keep the Qt interaction and
restart tests in UI Tests. Offscreen tests verify Qt behavior in that environment;
they do not establish native desktop appearance or live provider behavior.
