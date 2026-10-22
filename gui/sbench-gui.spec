# PyInstaller spec -- one-file windowed EXE with the REAL core sbench.exe embedded. Run from gui/ after
#   python tools/gen_build_info.py --core <sbench.exe> --version <v> --run-core
# which stages core-bin/sbench.exe and writes sbgui/_build_info.py (version, commit, core SHA-256).
#   pyinstaller --noconfirm --clean sbench-gui.spec
# At runtime the GUI never executes the copy in _MEIPASS: it copies it to <data_dir>\bin\sbench-<ver>.exe and
# verifies the SHA-256 before every launch (sbgui/core.py). No mock in frozen builds.
import os
block_cipher = None
CORE = os.path.join("core-bin", "sbench.exe")
INFO = os.path.join("sbgui", "_build_info.py")
missing = [p for p in (CORE, INFO) if not os.path.isfile(p)]
if missing:
    raise SystemExit(f"sbench-gui.spec: {missing} fehlt -- zuerst tools/gen_build_info.py --core <sbench.exe> ausführen")
fonts = [("assets/fonts", "assets/fonts")] if os.path.isdir("assets/fonts") else []
a = Analysis(
    ["sbgui/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=fonts + [
        (CORE, "core"),                   # datas, not binaries: copied verbatim (no dependency scan); hash-checked at runtime
        ("../schema", "schema"),          # contract (sb_contract.base_dir: <here>/schema when frozen)
        ("profiles", "profiles"), ("demo", "demo"),
    ],
    hiddenimports=["sb_contract", "sbgui.pipe_win", "sbgui._build_info", "sbgui.clicktest", "sbgui.errors", "jsonschema", "referencing"],
    excludes=["PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "tkinter"],   # not QtOpenGL: pyqtgraph imports it
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="sbench-gui",
    console=False,            # windowed; --self-test/--screenshot write JSON via --report <file>
    uac_admin=False,          # GUI never elevated; only sbench.exe via runas when needed
    upx=False,                # UPX raises AV false positives
    runtime_tmpdir=None,
    version=None, icon=None,
)
