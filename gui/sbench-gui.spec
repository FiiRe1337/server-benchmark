# PyInstaller spec (draft) -- one-file windowed EXE with sbench.exe embedded. Run from gui/:
#   pyinstaller --noconfirm sbench-gui.spec      (expects the core binary in ./core-bin/sbench.exe)
import os
block_cipher = None
core = [(os.path.join("core-bin", "sbench.exe"), ".")] if os.path.exists(os.path.join("core-bin", "sbench.exe")) else []
fonts = [("assets/fonts", "assets/fonts")] if os.path.isdir("assets/fonts") else []
a = Analysis(
    ["sbgui/__main__.py"],
    pathex=["."],
    binaries=core,
    datas=fonts + [
        ("../schema", "schema"),          # contract (sb_contract.base_dir: <here>/schema when frozen)
        ("contract", "contract"),         # sb-bundle/1 until it is on main
        ("profiles", "profiles"), ("demo", "demo"),
        ("mock_sbench.py", "."),          # fallback when no sbench.exe is bundled (demo builds only)
    ],
    hiddenimports=["sb_contract", "sbgui.pipe_win"],   # pipe_win is imported lazily (Windows only)
    excludes=["PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtWebEngineCore", "PySide6.QtOpenGL", "PySide6.Qt3DCore", "tkinter"],
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
