# -*- coding: utf-8 -*-
"""
hotkey.py — global hotkey manager (Ctrl+Alt+P) for FocusGuard.

Idea: a single class GlobalHotkeyManager(QObject) with ONE public signal
`toggled`. The rest of the app simply connects to this signal and doesn't know
how the hotkey was caught.

Strategy (with crash-free degradation):
  1) If pynput is installed — listen for a SYSTEM-WIDE (global) hotkey,
     working even when the window is not focused.
  2) If pynput is missing — register an IN-WINDOW QShortcut(Ctrl+Alt+P)
     on the given parent. It only works when the app is focused,
     but requires no extra dependencies.

============================ THREAD-SAFETY RULE ============================
pynput.keyboard.GlobalHotKeys is a threading.Thread. Its callback runs NOT on
Qt's GUI thread. Touching any QWidget (show/hide/raise) from that callback is
FORBIDDEN — it leads to crashes and undefined behavior.

That is why the pynput callback does EXACTLY ONE thing: emit self.toggled.
Since the signal sender and receiver live in different threads, Qt
automatically uses a queued connection and runs the connected slot on
the GUI thread — touching widgets there is safe.
==================================================================================
"""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QShortcut, QKeySequence  # in PyQt6 they live in QtGui

# Optional pynput import. If the module is missing — the app still works,
# the hotkey will just be local (via QShortcut).
try:
    from pynput import keyboard as _pynput_keyboard  # type: ignore
    _HAS_PYNPUT = True
except Exception:  # noqa: BLE001 — catch everything: missing module or platform error
    _pynput_keyboard = None
    _HAS_PYNPUT = False


class GlobalHotkeyManager(QObject):
    """Hotkey manager.

    Public signal:
        toggled — emitted when Ctrl+Alt+P is pressed (by either backend).

    Backend for integration: connect manager.toggled to a GUI-thread slot
    that shows/hides the pet widget (see MainWindow.toggle_pet).
    """

    toggled = pyqtSignal()  # THE single public signal — a stable contract

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_widget = parent      # needed for the QShortcut fallback
        self._listener = None             # reference to the pynput listener (so GC doesn't eat it)
        self._shortcut = None             # reference to the QShortcut fallback
        self._backend = "none"            # 'pynput' | 'qshortcut' | 'none'

    # ------------------------------------------------------------------ #
    @property
    def backend(self) -> str:
        """Which backend is actually active ('pynput' / 'qshortcut' / 'none')."""
        return self._backend

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start watching for the hotkey.

        First we try to enable the global pynput listener; on any error
        we silently fall back to the in-window QShortcut.
        """
        if _HAS_PYNPUT and self._start_pynput():
            self._backend = "pynput"
            return
        # Fallback — local Qt hotkey.
        if self._start_qshortcut():
            self._backend = "qshortcut"
            return
        self._backend = "none"

    # ------------------------------------------------------------------ #
    def _start_pynput(self) -> bool:
        """Start the global pynput listener. Return True on success."""
        try:
            # WARNING: the callback runs on the listener thread, so it ONLY emits the signal.
            self._listener = _pynput_keyboard.GlobalHotKeys(
                {"<ctrl>+<alt>+p": self._on_global_hotkey}
            )
            self._listener.start()  # non-blocking start (NOT .join())
            return True
        except Exception:  # noqa: BLE001 — may crash on some platforms
            self._listener = None
            return False

    def _on_global_hotkey(self) -> None:
        """pynput callback (FOREIGN thread!). May ONLY emit the signal.

        No QWidget access must happen here — see the rule at the top of the file.
        Qt will deliver the signal to the GUI thread via a queued connection.
        """
        self.toggled.emit()

    # ------------------------------------------------------------------ #
    def _start_qshortcut(self) -> bool:
        """Register a local QShortcut(Ctrl+Alt+P). Return True on success.

        Works only when the app is focused, but with no dependencies and on the GUI thread.
        """
        if self._parent_widget is None:
            return False
        try:
            self._shortcut = QShortcut(QKeySequence("Ctrl+Alt+P"), self._parent_widget)
            # activated — a Qt signal on the GUI thread, we forward it straight into our toggled.
            self._shortcut.activated.connect(self.toggled.emit)
            return True
        except Exception:  # noqa: BLE001
            self._shortcut = None
            return False

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        """Stop the listener and release resources (call this on app exit)."""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
        # The QShortcut is freed together with its parent; no explicit action needed.
        self._shortcut = None
