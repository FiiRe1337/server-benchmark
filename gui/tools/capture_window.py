#!/usr/bin/env python3
"""Windows: capture a real top-level window (e.g. Explorer, console) as PNG for the README guide.

  python tools/capture_window.py --title "sbench" --class CabinetWClass --size 980x560 --out ../docs/img/guide/01_download.png
  python tools/capture_window.py --title "sbench CLI" --out 07_cli.png --wait-file D:\\sbench\\cli-done.txt

Finds the first visible window whose title contains --title (and optionally has window class --class), optionally
resizes it, brings it to the front (topmost for the capture) and grabs its on-screen area via Qt (QScreen.grabWindow).
Uses DWM extended frame bounds, so the invisible resize border is not included."""
import argparse, ctypes, os, sys, time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwm = ctypes.WinDLL("dwmapi")


def find(title, cls=None):
    hits = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        if not user32.IsWindowVisible(h):
            return True
        n = user32.GetWindowTextLengthW(h); buf = ctypes.create_unicode_buffer(n + 1); user32.GetWindowTextW(h, buf, n + 1)
        cb_ = ctypes.create_unicode_buffer(256); user32.GetClassNameW(h, cb_, 256)
        if title.lower() in buf.value.lower() and (not cls or cb_.value == cls):
            hits.append((h, buf.value, cb_.value))
        return True
    user32.EnumWindows(PROC(cb), 0)
    return hits


def rect(h):
    r = wintypes.RECT()
    if dwm.DwmGetWindowAttribute(wintypes.HWND(h), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:   # DWMWA_EXTENDED_FRAME_BOUNDS
        user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True); ap.add_argument("--class", dest="cls"); ap.add_argument("--out", required=True)
    ap.add_argument("--size"); ap.add_argument("--pos", default="40x40"); ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--wait-file", help="wait until this file exists (e.g. end marker of a CLI demo)")
    a = ap.parse_args()
    t0 = time.time()
    if a.wait_file:
        while not os.path.exists(a.wait_file):
            if time.time() - t0 > a.timeout * 4:
                sys.exit(f"{a.wait_file} nicht erschienen")
            time.sleep(0.5)
    while not (hits := find(a.title, a.cls)):
        if time.time() - t0 > a.timeout:
            sys.exit(f"kein Fenster mit Titel '{a.title}' gefunden")
        time.sleep(0.5)
    h, title, cls = hits[0]
    print(f"Fenster: {title!r} ({cls})")
    x, y = (int(v) for v in a.pos.split("x"))
    HWND_TOPMOST, HWND_NOTOPMOST, SWP_NOSIZE, SWP_SHOWWINDOW = -1, -2, 0x1, 0x40
    user32.ShowWindow(h, 9)   # SW_RESTORE
    if a.size:
        w, hh = (int(v) for v in a.size.split("x"))
        user32.SetWindowPos(h, HWND_TOPMOST, x, y, w, hh, SWP_SHOWWINDOW)
    else:
        user32.SetWindowPos(h, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_SHOWWINDOW)
    user32.SetForegroundWindow(h)
    time.sleep(1.5)
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(["capture", "-platform", "windows"])  # noqa: F841 -- keep alive
    rx, ry, rw, rh = rect(h)
    pm = app.primaryScreen().grabWindow(0, rx, ry, rw, rh)
    user32.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | 0x2)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    if pm.isNull() or not pm.save(a.out):
        sys.exit("Aufnahme fehlgeschlagen")
    print(f"{a.out}: {pm.width()}x{pm.height()} @ {rx},{ry}")


if __name__ == "__main__":
    main()
