# -*- coding: utf-8 -*-
"""
Admin-elevation helpers (Windows).

Blocking sites (hosts) and suspending some processes need administrator rights.
This module checks admin status and can relaunch the app elevated via UAC.
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


def relaunch_as_admin(args=None):
    """Relaunch the current program with a UAC prompt. Returns True if a relaunch
    was triggered (caller should then exit). No-op (False) if already admin or
    not on Windows."""
    if not IS_WINDOWS or is_admin():
        return False
    try:
        import ctypes
        params = " ".join(args if args is not None else sys.argv)
        # ShellExecuteW "runas" triggers the UAC elevation dialog
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1)
        return rc > 32          # >32 means success per WinAPI
    except Exception as e:
        print(f"[Elevation] Could not relaunch as admin: {e}")
        return False
