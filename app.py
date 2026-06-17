# -*- coding: utf-8 -*-
"""
app.py — entry point for the FocusGuard application.

What it does:
1. Creates the QApplication.
2. Sets up fonts (theme.setup_fonts).
3. Loads and applies style.qss (next to this file). If the file is missing,
   the app still launches (just without the custom theme).
4. Creates MainWindow.
5. Starts GlobalHotkeyManager (Ctrl+Alt+P) and connects it to the window.
6. Starts the event loop via app.exec().

High-DPI in Qt6 is enabled automatically — no separate flags are needed.

Run: python app.py  (or double-click run_gui.bat).
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QSharedMemory
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from theme import setup_fonts
from main_window import MainWindow
from hotkey import GlobalHotkeyManager


def load_stylesheet(app: QApplication) -> None:
    """Read style.qss next to app.py and apply it. Silently skip if the file is missing."""
    qss_path = Path(__file__).resolve().parent / "style.qss"
    try:
        if qss_path.exists():
            app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — the style is not critical for launching
        # Don't crash the app over style issues — just report it to the console.
        print(f"[FocusGuard] Failed to load style.qss: {exc}")


def _icon_path() -> str:
    """app_icon.ico location: next to app.py in dev, inside the bundle when frozen."""
    base = getattr(sys, "_MEIPASS", None) or str(Path(__file__).resolve().parent)
    p = Path(base) / "app_icon.ico"
    return str(p) if p.exists() else ""


def _run_firewall_helper() -> int | None:
    """Elevated hosts helper: if launched with --fw-block / --fw-unblock, do ONLY the
    hosts edit and exit (no GUI, no single-instance lock). Used by elevation.run_hosts_helper
    so the main app can block/unblock without restarting. Returns an exit code, or None
    when this is a normal launch."""
    argv = sys.argv
    if "--fw-block" not in argv and "--fw-unblock" not in argv:
        return None
    try:
        import firewall
        if "--fw-unblock" in argv:
            ok, msg = firewall.unblock()
        else:
            ok, msg = firewall.block()
        print(("[OK] " if ok else "[!] ") + msg)
        return 0 if ok else 2
    except Exception as exc:
        print(f"[firewall-helper] {exc}")
        return 3


def _acquire_single_instance(app, retry: bool = False):
    """Return a held QSharedMemory if we're the first instance, else None.

    A second copy would fight the first for the webcam (camera "doesn't work") and
    spawn a duplicate desktop cat, so we only allow one running instance. When relaunched
    elevated (retry=True) we wait a few seconds for the OLD copy to release the lock as it
    quits, so 'Restart as administrator' isn't rejected as 'already running'."""
    import time as _t
    shm = QSharedMemory("FocusGuard-pixelcatpet-single-instance")
    attempts = 24 if retry else 1            # ~6 s of 0.25 s retries when relaunched
    for i in range(attempts):
        if shm.create(1):                    # got it -> we're the sole instance
            return shm
        if shm.attach():                     # the segment exists -> someone else holds it
            shm.detach()
        if i < attempts - 1:
            _t.sleep(0.25)
    return None


def main() -> int:
    # Elevated hosts helper path: do the privileged hosts edit and exit before any GUI.
    _hc = _run_firewall_helper()
    if _hc is not None:
        return _hc

    app = QApplication(sys.argv)
    app.setApplicationName("FocusCat++")
    app.setApplicationDisplayName("FocusCat++")

    # When relaunched elevated, wait for the previous (non-admin) copy to release the lock.
    _relaunched = "--relaunched-admin" in sys.argv
    _singleton = _acquire_single_instance(app, retry=_relaunched)
    if _singleton is None:
        QMessageBox.information(None, "FocusCat++", "FocusCat++ is already running.")
        return 0
    app._fg_singleton = _singleton   # prevent GC while running

    _icon = _icon_path()
    if _icon:
        app.setWindowIcon(QIcon(_icon))

    # Fonts and theme.
    setup_fonts(app)
    load_stylesheet(app)

    # Main window.
    window = MainWindow()
    window.show()

    # First-run onboarding (name the pet), once, before the camera/consent flow.
    window.first_run_setup()

    # Start the camera AFTER show(): the window constructor does not open the camera,
    # so the window has time to render, and DetectionWorker only touches the device
    # inside run() from within its own thread.
    window.start_camera()

    # Hotkey: the parent is the main window (needed for the QShortcut fallback).
    hotkey = GlobalHotkeyManager(parent=window)
    window.connect_hotkey(hotkey)
    hotkey.start()
    print(f"[FocusGuard] Hotkey Ctrl+Alt+P, backend: {hotkey.backend}")

    # Cleanly stop both the listener AND the camera on exit. shutdown_camera does
    # stop()+wait() — otherwise a zombie thread keeps holding the camera (a real race in FocusGuard).
    # _unblock_on_exit is the final safety net for site blocking: the user's hosts file
    # must never be left blocked after the app quits (idempotent with closeEvent).
    app.aboutToQuit.connect(window._unblock_on_exit)
    app.aboutToQuit.connect(hotkey.stop)
    app.aboutToQuit.connect(window.shutdown_game)
    app.aboutToQuit.connect(window.shutdown_camera)

    # Play the summon chime once on start (a friendly "I'm awake" blip). Routed through
    # the game's ChimePlayer so it respects sound_enabled/volume and never blocks the GUI.
    try:
        window.game.chime.play("summon")
    except Exception:
        pass

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
