"""Desktop entry point; CLI and Telegram entry points remain independent."""

import sys


def main():
    from PySide6.QtWidgets import QApplication
    from gui.windows.main_window import MainWindow

    application = QApplication(sys.argv)
    application.setApplicationName("V-CODE License Manager")
    application.setStyle("Fusion")
    # Never dump raw exceptions or Qt updates that could contain signing input.
    sys.excepthook = lambda *_: sys.stderr.write("Unexpected desktop error. Close and restart the application.\n")
    window = MainWindow()
    window.show()
    try:
        return application.exec()
    finally:
        window.session.lock()
        window.runner.pool.waitForDone()


if __name__ == "__main__":
    raise SystemExit(main())
