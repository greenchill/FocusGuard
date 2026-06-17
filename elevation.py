# -*- coding: utf-8 -*-
"""
Admin-elevation helpers (Windows).

Editing the hosts file (site blocking) needs administrator rights. Windows cannot
elevate a running process IN PLACE, so we have two options, both here:

  * run_hosts_helper(action) — spawn a SHORT-LIVED elevated copy of ourselves
    (FocusGuard.exe --fw-block / --fw-unblock, handled in app.py) that does ONLY the
    hosts edit and exits. The main app keeps running, no restart. One UAC per call.
  * relaunch_as_admin() — relaunch the WHOLE app elevated (one UAC, then blocking is
    seamless with no further prompts). The fresh elevated copy passes --relaunched-admin
    so it waits for this copy's single-instance lock to free up.
"""
import os
import sys
import platform

IS_WINDOWS = platform.system() == "Windows"


def is_admin():
    try:
        if IS_WINDOWS:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        return os.geteuid() == 0
    except Exception:
        return False


def _quote(s: str) -> str:
    return '"' + s + '"' if (s and ' ' in s and not s.startswith('"')) else s


def _target_and_prefix():
    """Return (exe_to_run, param_prefix) for re-invoking ourselves.

    Frozen: run the .exe directly (no prefix). Dev: run python with the script path."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    return sys.executable, _quote(os.path.abspath(sys.argv[0])) + " "


def relaunch_as_admin(extra_args=None):
    """Relaunch the whole app elevated (UAC). Returns True if a relaunch was triggered
    (caller should then quit). No-op (False) if already admin / not Windows / cancelled."""
    if not IS_WINDOWS or is_admin():
        return False
    try:
        import ctypes
        target, prefix = _target_and_prefix()
        args = list(extra_args or [])
        if "--relaunched-admin" not in args:
            args.append("--relaunched-admin")
        params = prefix + " ".join(_quote(a) for a in args)
        # ShellExecuteW "runas" triggers the UAC dialog. >32 == success.
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, params, None, 1)
        return rc > 32
    except Exception as e:
        print(f"[Elevation] Could not relaunch as admin: {e}")
        return False


def run_hosts_helper(action: str, wait: bool = True, timeout_ms: int = 30000) -> bool:
    """Apply ('block') or remove ('unblock') the hosts block via a brief ELEVATED copy of
    ourselves, WITHOUT restarting the app. Shows one UAC prompt.

    wait=True: block until the helper finishes and return True only on success (used
    interactively so we can report the result). wait=False: fire-and-forget (used on app
    exit so shutdown can't hang on the UAC) — returns True if the launch was accepted.
    Returns False if cancelled / not Windows / on any error."""
    if not IS_WINDOWS:
        return False
    flag = "--fw-unblock" if action == "unblock" else "--fw-block"
    try:
        import ctypes
        from ctypes import wintypes

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SW_HIDE = 0
        target, prefix = _target_and_prefix()
        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = target
        sei.lpParameters = prefix + flag
        sei.nShow = SW_HIDE
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            return False                     # user cancelled UAC or launch failed
        if not sei.hProcess:
            return True                      # launched but no handle — assume ok
        if not wait:
            ctypes.windll.kernel32.CloseHandle(sei.hProcess)
            return True                      # fire-and-forget (exit path)
        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, int(timeout_ms))
        code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        return code.value == 0
    except Exception as e:
        print(f"[Elevation] hosts helper failed: {e}")
        return False
