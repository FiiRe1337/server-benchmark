import sys

# crash log FIRST (stdlib only): failures while importing PySide6 / sbgui or before the QApplication exists must end up
# in %LOCALAPPDATA%\sbench\crash.log with a message box instead of a silently vanishing windowed EXE (sbgui/crashlog.py)
from sbgui import crashlog

crashlog.install()

# absolute import: this file is also the PyInstaller entry script (sbench-gui.spec), where it runs as a
# top-level script without a parent package, so `from .app import main` would fail in the frozen EXE
try:
    from sbgui.app import main
    rc = main()
except SystemExit:
    raise
except BaseException:  # noqa: BLE001 -- KeyboardInterrupt included: log it, never vanish without a trace
    crashlog.report(*sys.exc_info(), where="Start")
    rc = 1
sys.exit(rc)
