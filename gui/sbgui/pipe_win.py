"""Windows-only: named-pipe progress channel + UAC 'runas' start of sbench.exe (docs/cli.md 1.1, "Named Pipe").

- The GUI is the pipe SERVER: CreateNamedPipeW(PIPE_ACCESS_INBOUND, byte mode, 1 instance,
  PIPE_REJECT_REMOTE_CLIENTS, FILE_FLAG_FIRST_PIPE_INSTANCE) with security descriptor
  SDDL  D:P(A;;GA;;;<current user SID>)(A;;GA;;;BA)   (protected DACL: only this user + BUILTIN\\Administrators;
  the BA entry is needed because the runas'ed sbench may run under a different admin account).
- The pipe is created BEFORE sbench starts; sbench waits up to 10 s (WaitNamedPipe) else E_PIPE_CONNECT / exit 1.
- If the GUI dies the pipe breaks; sbench keeps running and the GUI can re-attach via runs/<id>.jsonl.
Untested on real Windows in this prototype (box is Linux) -> M2 task: test on Server 2016/2022 + Win11.
"""
import ctypes, subprocess, threading
from ctypes import wintypes
from PySide6.QtCore import QObject, Signal

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)
sh32 = ctypes.WinDLL("shell32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PIPE_ACCESS_INBOUND = 0x00000001
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_TYPE_BYTE = PIPE_READMODE_BYTE = PIPE_WAIT = 0
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
ERROR_PIPE_CONNECTED, ERROR_BROKEN_PIPE, ERROR_CANCELLED = 535, 109, 1223
TOKEN_QUERY, TokenUser = 0x0008, 1
SDDL_REVISION_1 = 1
SEE_MASK_NOCLOSEPROCESS, SEE_MASK_FLAG_NO_UI, SEE_MASK_NOASYNC = 0x40, 0x400, 0x100
INFINITE = 0xFFFFFFFF
GENERIC_WRITE, OPEN_EXISTING = 0x40000000, 3


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def _sig(fn, res, *args):
    fn.restype = res; fn.argtypes = list(args); return fn


OpenProcessToken = _sig(adv.OpenProcessToken, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
GetTokenInformation = _sig(adv.GetTokenInformation, wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD))
ConvertSidToStringSidW = _sig(adv.ConvertSidToStringSidW, wintypes.BOOL, ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
ConvertSDDL = _sig(adv.ConvertStringSecurityDescriptorToSecurityDescriptorW, wintypes.BOOL, wintypes.LPCWSTR, wintypes.DWORD,
                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG))
CreateNamedPipeW = _sig(k32.CreateNamedPipeW, wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES))
ConnectNamedPipe = _sig(k32.ConnectNamedPipe, wintypes.BOOL, wintypes.HANDLE, ctypes.c_void_p)
ReadFile = _sig(k32.ReadFile, wintypes.BOOL, wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
CreateFileW = _sig(k32.CreateFileW, wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                   wintypes.DWORD, wintypes.HANDLE)
CloseHandle = _sig(k32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
LocalFree = _sig(k32.LocalFree, ctypes.c_void_p, ctypes.c_void_p)
GetCurrentProcess = _sig(k32.GetCurrentProcess, wintypes.HANDLE)
WaitForSingleObject = _sig(k32.WaitForSingleObject, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
GetExitCodeProcess = _sig(k32.GetExitCodeProcess, wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
ShellExecuteExW = _sig(sh32.ShellExecuteExW, wintypes.BOOL, ctypes.POINTER(SHELLEXECUTEINFOW))


def current_user_sid():
    tok = wintypes.HANDLE()
    if not OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(tok)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        n = wintypes.DWORD()
        GetTokenInformation(tok, TokenUser, None, 0, ctypes.byref(n))
        buf = ctypes.create_string_buffer(n.value)
        if not GetTokenInformation(tok, TokenUser, buf, n, ctypes.byref(n)):
            raise ctypes.WinError(ctypes.get_last_error())
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]   # TOKEN_USER.User.Sid
        s = wintypes.LPWSTR()
        if not ConvertSidToStringSidW(psid, ctypes.byref(s)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return s.value
        finally:
            LocalFree(s)
    finally:
        CloseHandle(tok)


def pipe_sddl():
    return f"D:P(A;;GA;;;{current_user_sid()})(A;;GA;;;BA)"


def create_pipe(name):
    psd = ctypes.c_void_p()
    if not ConvertSDDL(pipe_sddl(), SDDL_REVISION_1, ctypes.byref(psd), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), psd, False)
        h = CreateNamedPipeW(name, PIPE_ACCESS_INBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                             PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                             1, 0, 65536, 0, ctypes.byref(sa))
        if h in (None, INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        return h
    finally:
        LocalFree(psd)


class PipeServer(QObject):
    """Creates the pipe immediately (constructor), waits for sbench in a thread, emits raw bytes."""
    data = Signal(bytes)
    error = Signal(str)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name; self.h = create_pipe(name); self._closed = False

    def start(self):
        threading.Thread(target=self._loop, name="sbench-pipe", daemon=True).start()

    def _loop(self):
        if not ConnectNamedPipe(self.h, None) and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
            if not self._closed:
                self.error.emit(f"ConnectNamedPipe: {ctypes.get_last_error()}")
            return
        buf = ctypes.create_string_buffer(65536); n = wintypes.DWORD()
        while not self._closed:
            if not ReadFile(self.h, buf, len(buf), ctypes.byref(n), None) or n.value == 0:
                break   # ERROR_BROKEN_PIPE: sbench closed its end
            self.data.emit(buf.raw[: n.value])

    def close(self):
        if self._closed:
            return
        self._closed = True
        # unblock a pending ConnectNamedPipe (sbench never connected) by connecting ourselves
        h = CreateFileW(self.name, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if h not in (None, INVALID_HANDLE_VALUE):
            CloseHandle(h)
        CloseHandle(self.h)


class RunasProcess(QObject):
    """ShellExecuteExW(verb='runas') -> UAC prompt; waits for the process in a thread. exited(-1223) = UAC cancelled."""
    exited = Signal(int)

    def __init__(self, cmd, parent=None):
        super().__init__(parent); self.cmd = cmd

    def start(self):
        sei = SHELLEXECUTEINFOW(); sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI | SEE_MASK_NOASYNC
        sei.lpVerb = "runas"; sei.lpFile = self.cmd[0]; sei.lpParameters = subprocess.list2cmdline(self.cmd[1:]); sei.nShow = 0
        if not ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.get_last_error()
            self.exited.emit(-1223 if err == ERROR_CANCELLED else -err); return
        h = sei.hProcess

        def wait():
            WaitForSingleObject(h, INFINITE); code = wintypes.DWORD()
            GetExitCodeProcess(h, ctypes.byref(code)); CloseHandle(h); self.exited.emit(int(code.value))
        threading.Thread(target=wait, name="sbench-runas-wait", daemon=True).start()
