"""Keep real Qt tests isolated without leaving hidden windows behind."""
import unittest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


class QtTestCase(unittest.TestCase):
    """Delete each test's top-level widgets and drain deferred deletion.

    Closing a QWidget normally hides it rather than deleting it. Leftover windows
    still participate in application-wide theme/font updates. Register cleanup in
    run(), so individual tests can keep their existing setUp/tearDown methods.
    """

    def run(self, result=None):
        app = QApplication.instance()
        existing = {id(widget) for widget in app.topLevelWidgets()} if app else set()
        self.addCleanup(self._delete_test_widgets, existing)
        return super().run(result)

    @staticmethod
    def _delete_test_widgets(existing):
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            if id(widget) not in existing and isValid(widget):
                widget.deleteLater()
        # Only drain destruction events: don't run unrelated audio, provider, or
        # deferred visual-scan timers after temporary save directories are gone.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
