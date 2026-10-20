import sys

# absolute import: this file is also the PyInstaller entry script (sbench-gui.spec), where it runs as a
# top-level script without a parent package, so `from .app import main` would fail in the frozen EXE
from sbgui.app import main

sys.exit(main())
