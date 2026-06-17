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


def _acquire_single_instance(app):
    """Return a held QSharedMemory if we're the first instance, else None.

    A second copy would fight the first for the webcam (camera "doesn't work") and
    spawn a duplicate desktop cat, so we only allow one running instance."""
    shm = QSharedMemory("FocusGuard-pixelcatpet-single-instance")
    if shm.attach():            # segment exists -> another instance is running
        shm.detach()
        return None
    if not shm.create(1):       # we couldn't create it (race / already there)
        return None
    return shm                  # keep this object alive for the process lifetime


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FocusGuard")

    _singleton = _acquire_single_instance(app)
    if _singleton is None:
        QMessageBox.information(None, "FocusGuard", "FocusGuard is already running.")
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
