# -*- coding: utf-8 -*-
"""
windows_perch.py — desktop window "ledges" for the pixel cat (shimeji-style).

Enumerates the real top-level application windows on screen and returns the
VISIBLE segments of each window's TOP EDGE as QRects ("ledges") the cat can stand
on. Z-order is respected: a window's top edge is split where windows ABOVE it (and
overlapping that line) cover it, so the cat only perches on visible edges and never
gets stuck on an edge hidden behind another window.

Windows-only (uses the Win32 API via ctypes — no extra dependency, works frozen).
On any other platform, or on any error, it returns [] and the cat just uses the
desktop floor + the host app window (fed separately).

Public API:
    visible_window_ledges(exclude_hwnds=(), dpr=1.0, min_w=90, lift=8) -> list[QRect]
    compute_ledges(rects_top_to_bottom, dpr, min_w, lift) -> list[QRect]   # pure, testable
"""

from __future__ import annotations

import sys


def _subtract(intervals, a, b):
    """Remove the open span [a, b] from a list of (start, end) intervals."""
    out = []
    for (s, e) in intervals:
        if b <= s or a >= e:        # no overlap
            out.append((s, e))
            continue
        if a > s:                   # left remainder
            out.append((s, a))
        if b < e:                   # right remainder
            out.append((b, e))
    return out


def compute_ledges(rects, dpr=1.0, min_w=90, lift=8):
    """Pure geometry: given window rects in Z-ORDER (topmost first), in PHYSICAL
    pixels as (left, top, right, bottom), return the visible top-edge ledges as
    QRects in Qt-LOGICAL pixels (divided by dpr), each lifted up by `lift` so the
    cat's feet rest cleanly on the edge. Only segments at least `min_w` logical px
    wide are kept."""
    from PyQt6.QtCore import QRect
    dpr = float(dpr) or 1.0
    ledges = []
    for i in range(len(rects)):
        l, t, r, b = rects[i]
        if r <= l:
            continue
        intervals = [(l, r)]
        # Subtract the x-spans of windows ABOVE this one (earlier in Z-order) that
        # also cover the horizontal line y = t (their vertical span includes t).
        for j in range(i):
            al, at, ar, ab = rects[j]
            if at <= t <= ab and ar > l and al < r:
                intervals = _subtract(intervals, al, ar)
                if not intervals:
                    break
        for (sl, sr) in intervals:
            width_log = (sr - sl) / dpr
            if width_log >= min_w:
                ledges.append(QRect(
                    int(round(sl / dpr)),
                    int(round(t / dpr)) - int(lift),
                    int(round((sr - sl) / dpr)),
                    6,
                ))
    return ledges


# Class names that are NOT real perchable app windows (taskbar, desktop, shells,
# UWP host/popup helpers, IME, tooltips, ...).
_BLOCKED_CLASSES = {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
    "SysShadow", "ForegroundStaging", "XamlExplorerHostIslandWindow",
    "Xaml_WindowedPopupClass", "Windows.UI.Core.CoreWindow",
    "DummyDWMListenerWindow", "MSCTFIME UI", "Default IME", "tooltips_class32",
    "TaskManagerWindow", "NotifyIconOverflowWindow", "Microsoft.Windows.StartMenuExperienceHost",
}


def visible_window_ledges(exclude_hwnds=(), dpr=1.0, min_w=90, lift=8):
    """Enumerate visible top-level app windows and return their visible top-edge
    ledges (Qt-logical QRects). Returns [] on non-Windows or on any failure."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi

        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = wintypes.LONG
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        DWMWA_CLOAKED = 14
        DWMWA_EXTENDED_FRAME_BOUNDS = 9

        exclude = {int(h) for h in exclude_hwnds}
        rects = []  # (l, t, r, b) physical, Z-order topmost first

        def _bounds(hwnd):
            """Visible frame bounds (DWM extended frame; excludes invisible borders),
            falling back to GetWindowRect."""
            rc = wintypes.RECT()
            try:
                hr = dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                    ctypes.byref(rc), ctypes.sizeof(rc))
                if hr == 0 and (rc.right - rc.left) > 0:
                    return rc.left, rc.top, rc.right, rc.bottom
            except Exception:
                pass
            if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                return rc.left, rc.top, rc.right, rc.bottom
            return None

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if int(hwnd) in exclude:
                    return True
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    return True
                if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                    return True
                cloaked = ctypes.c_int(0)
                try:
                    dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(hwnd), DWMWA_CLOAKED,
                        ctypes.byref(cloaked), ctypes.sizeof(cloaked))
                except Exception:
                    pass
                if cloaked.value != 0:
                    return True
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                if cls.value in _BLOCKED_CLASSES:
                    return True
                title = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title, 512)
                if not title.value.strip():
                    return True            # real app windows have a title
                bounds = _bounds(hwnd)
                if bounds is None:
                    return True
                l, t, r, b = bounds
                if (r - l) < 120 or (b - t) < 60:
                    return True            # skip tiny popups
                rects.append((l, t, r, b))
            except Exception:
                pass
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        return compute_ledges(rects, dpr=dpr, min_w=min_w, lift=lift)
    except Exception:
        return []
