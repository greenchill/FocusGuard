# -*- coding: utf-8 -*-
"""
widget_timer.py — TimerWidget: a circular "ring" timer with a dual dial.

- RingTimer: a custom QWidget with two modes:
    * DIAL (idle): ONE ring shows the focus arc + the break arc with two draggable
      handles. Dragging the focus handle shifts the break arc; dragging the break
      handle resizes the break. The center shows both lengths.
    * RUN (session): a single arc that fills smoothly 0->1 as the phase elapses,
      with mm:ss + the phase label in the center.
- TimerWidget: ring + Start / Pause / Stop, routed to the GameController.

Signals:
    focus_dialed(int) / break_dialed(int) — re-exposed by TimerWidget as
        duration_dialed(target, minutes).
    started / paused / stopped / finished / tick(int).
"""

import math

from PyQt6.QtCore import (
    Qt, QTimer, QRectF, QPointF, pyqtSignal, pyqtProperty, QPropertyAnimation,
    QEasingCurve,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QConicalGradient, QFont
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton

from theme import COLORS, FONTS
import icons


# --------------------------------------------------------------------------- #
# TIMER RING — dual-handle dial (idle) + smooth fill arc (running).            #
# --------------------------------------------------------------------------- #
class RingTimer(QWidget):
    """Ring widget: dual focus/break dial when idle, smooth countdown when running."""

    focus_dialed = pyqtSignal(int)   # minutes, while dragging the focus handle
    break_dialed = pyqtSignal(int)   # minutes, while dragging the break handle

    # Dial scale: the full circle maps to DIAL_MAX minutes (focus 120 + break 30).
    # Ranges MUST match the Settings spinboxes so the dial and the spinbox agree.
    DIAL_MAX = 150
    FOCUS_MIN, FOCUS_MAX, FOCUS_STEP = 5, 120, 5
    BREAK_MIN, BREAK_MAX, BREAK_STEP = 1, 30, 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(196, 196)
        self._mode = "dial"            # "dial" | "run"

        # run state
        self._progress = 0.0           # 0..1 elapsed fraction (the fill arc)
        self._remaining = 0            # remaining seconds (mm:ss)
        self._run_label = "Pomodoro"

        # dial state
        self._focus = 50
        self._break = 10
        self._drag_handle = None       # "focus" | "break" | None

        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(950)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)

    # ---- mode switches ---------------------------------------------------- #
    def set_dial(self, focus_minutes: int, break_minutes: int) -> None:
        self._mode = "dial"
        self._focus = max(self.FOCUS_MIN, min(self.FOCUS_MAX, int(focus_minutes)))
        self._break = max(self.BREAK_MIN, min(self.BREAK_MAX, int(break_minutes)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()

    def set_run_mode(self) -> None:
        self._mode = "run"
        self._anim.stop()
        self._progress = 0.0           # the live arc starts EMPTY (set once here)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    # ---- run helpers ------------------------------------------------------ #
    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, value))
        self.update()

    progress = pyqtProperty(float, fget=get_progress, fset=set_progress)

    def animate_to(self, value: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(max(0.0, min(1.0, value)))
        self._anim.start()

    def set_remaining(self, seconds: int) -> None:
        self._remaining = max(0, int(seconds))
        self.update()

    def set_mode(self, label: str) -> None:
        self._run_label = label
        self.update()

    # ---- dial interaction ------------------------------------------------- #
    def _pos_to_minutes(self, posf) -> float:
        cx, cy = self.width() / 2.0, self.height() / 2.0
        ang = math.degrees(math.atan2(posf.y() - cy, posf.x() - cx))
        frac = ((ang + 90.0) % 360.0) / 360.0          # 0 at top, clockwise
        return frac * self.DIAL_MAX

    def mousePressEvent(self, event):
        if self._mode == "dial" and event.button() == Qt.MouseButton.LeftButton:
            cm = self._pos_to_minutes(event.position())
            # Grab whichever handle is nearer: focus-end (focus) or break-end (focus+break).
            d_focus = abs(cm - self._focus)
            d_break = abs(cm - (self._focus + self._break))
            self._drag_handle = "focus" if d_focus <= d_break else "break"
            self._drag_to(cm)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._mode == "dial" and self._drag_handle and (event.buttons() & Qt.MouseButton.LeftButton):
            self._drag_to(self._pos_to_minutes(event.position()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_handle = None
        super().mouseReleaseEvent(event)

    def _drag_to(self, cm: float) -> None:
        if self._drag_handle == "focus":
            m = round(cm / self.FOCUS_STEP) * self.FOCUS_STEP
            m = max(self.FOCUS_MIN, min(self.FOCUS_MAX, min(m, self.DIAL_MAX - self._break)))
            if m != self._focus:
                self._focus = int(m)
                self.update()
                self.focus_dialed.emit(self._focus)
        elif self._drag_handle == "break":
            m = round((cm - self._focus) / self.BREAK_STEP) * self.BREAK_STEP
            m = max(self.BREAK_MIN, min(self.BREAK_MAX, m))
            if m != self._break:
                self._break = int(m)
                self.update()
                self.break_dialed.emit(self._break)

    # ---- rendering -------------------------------------------------------- #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        pen_w = side * 0.085
        margin = pen_w / 2 + 4
        rect = QRectF(margin, margin, side - 2 * margin, side - 2 * margin)
        center = rect.center()

        # Track.
        track = QPen(QColor(COLORS["track"]))
        track.setWidthF(pen_w)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)

        if self._mode == "dial":
            self._paint_dial(p, rect, center, pen_w, side)
        else:
            self._paint_run(p, rect, center, pen_w, side)

    def _arc(self, p, rect, pen_w, start_min, span_min, color, cap=Qt.PenCapStyle.RoundCap):
        if span_min <= 0:
            return
        pen = QPen(QColor(color), pen_w)
        pen.setCapStyle(cap)
        p.setPen(pen)
        start_deg = 90.0 - start_min * (360.0 / self.DIAL_MAX)   # 12 o'clock, clockwise
        span_deg = -span_min * (360.0 / self.DIAL_MAX)
        p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))

    def _knob(self, p, rect, center, pen_w, at_min, color):
        ang = math.radians(90.0 - at_min * (360.0 / self.DIAL_MAX))
        r = rect.width() / 2.0
        kx = center.x() + r * math.cos(ang)
        ky = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor(COLORS["surface"]), 2))
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(kx, ky), pen_w * 0.6, pen_w * 0.6)

    def _paint_dial(self, p, rect, center, pen_w, side):
        # Focus arc (accent), then break arc (success) continuing from focus end.
        self._arc(p, rect, pen_w, 0, self._focus, COLORS["accent"])
        self._arc(p, rect, pen_w, self._focus, self._break, COLORS["success"])
        self._knob(p, rect, center, pen_w, self._focus, COLORS["accent_soft"])
        self._knob(p, rect, center, pen_w, self._focus + self._break, COLORS["success"])

        # Center: focus (accent) + break (success), each with a tiny label.
        p.setFont(QFont(FONTS["pixel"], int(side * 0.135)))
        p.setPen(QColor(COLORS["accent"]))
        p.drawText(QRectF(rect.left(), rect.top() + rect.height() * 0.24,
                          rect.width(), rect.height() * 0.20),
                   Qt.AlignmentFlag.AlignCenter, str(self._focus))
        p.setFont(QFont(FONTS["pixel"], int(side * 0.04)))
        p.setPen(QColor(COLORS["muted"]))
        p.drawText(QRectF(rect.left(), rect.top() + rect.height() * 0.44,
                          rect.width(), rect.height() * 0.08),
                   Qt.AlignmentFlag.AlignCenter, "MIN FOCUS")
        p.setFont(QFont(FONTS["pixel"], int(side * 0.10)))
        p.setPen(QColor(COLORS["success"]))
        p.drawText(QRectF(rect.left(), rect.top() + rect.height() * 0.55,
                          rect.width(), rect.height() * 0.15),
                   Qt.AlignmentFlag.AlignCenter, str(self._break))
        p.setFont(QFont(FONTS["pixel"], int(side * 0.04)))
        p.setPen(QColor(COLORS["muted"]))
        p.drawText(QRectF(rect.left(), rect.top() + rect.height() * 0.70,
                          rect.width(), rect.height() * 0.08),
                   Qt.AlignmentFlag.AlignCenter, "MIN BREAK")

    def _paint_run(self, p, rect, center, pen_w, side):
        if self._progress > 0:
            grad = QConicalGradient(center, 90.0)
            grad.setColorAt(0.0, QColor(COLORS["accent"]))
            grad.setColorAt(0.5, QColor(COLORS["accent_soft"]))
            grad.setColorAt(1.0, QColor(COLORS["accent"]))
            pen = QPen(grad, pen_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, -int(360 * 16 * self._progress))

        minutes, seconds = self._remaining // 60, self._remaining % 60
        p.setFont(QFont(FONTS["pixel"], int(side * 0.13)))
        p.setPen(QColor(COLORS["text"]))
        p.drawText(QRectF(rect.left(), rect.top() + rect.height() * 0.30,
                          rect.width(), rect.height() * 0.30),
                   Qt.AlignmentFlag.AlignCenter, f"{minutes:02d}:{seconds:02d}")
        p.setFont(QFont(FONTS["pixel"], int(side * 0.045)))
        p.setPen(QColor(COLORS["muted"]))
        p.drawText(QRectF(rect.left(), rect.top() + rect.height() * 0.58,
                          rect.width(), rect.height() * 0.18),
                   Qt.AlignmentFlag.AlignCenter, self._run_label.upper())


# --------------------------------------------------------------------------- #
# COMPOSITION: ring + control buttons + countdown logic.                       #
# --------------------------------------------------------------------------- #
class TimerWidget(QWidget):
    """Complete timer widget with Start / Pause / Stop buttons and a countdown."""

    started = pyqtSignal()
    paused = pyqtSignal()
    stopped = pyqtSignal()
    finished = pyqtSignal()
    tick = pyqtSignal(int)
    duration_dialed = pyqtSignal(str, int)   # (target 'focus'|'break', minutes)

    def __init__(self, parent=None, duration_seconds: int = 25 * 60):
        super().__init__(parent)

        self._total = max(1, duration_seconds)
        self._remaining = self._total
        self._running = False
        self._dial_active = None         # tracks dial<->run transitions (idempotent)

        self._focus_minutes = max(1, duration_seconds // 60)
        self._break_minutes = 10

        self._controller = None
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._on_second)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addStretch(1)

        self.ring = RingTimer(self)
        self.ring.set_remaining(self._remaining)
        self.ring.focus_dialed.connect(lambda m: self._on_dial("focus", m))
        self.ring.break_dialed.connect(lambda m: self._on_dial("break", m))
        root.addWidget(self.ring, alignment=Qt.AlignmentFlag.AlignCenter)

        root.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.btn_start = QPushButton("  Start")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.setIcon(icons.icon("play", COLORS["ink"], 16))
        self.btn_start.clicked.connect(self.start)
        self.btn_pause = QPushButton("  Pause")
        self.btn_pause.setObjectName("GhostButton")
        self.btn_pause.setIcon(icons.icon("pause", COLORS["muted"], 16))
        self.btn_pause.clicked.connect(self.pause)
        self.btn_stop = QPushButton("  Stop")
        self.btn_stop.setObjectName("GhostButton")
        self.btn_stop.setIcon(icons.icon("stop", COLORS["muted"], 16))
        self.btn_stop.clicked.connect(self.stop)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b)
        root.addLayout(buttons)

        self._sync_buttons()
        self._update_dial_mode()

    # ------------------------------------------------------------------ #
    # DIAL
    # ------------------------------------------------------------------ #
    def set_durations(self, focus_minutes: int, break_minutes: int) -> None:
        """Set the focus/break minutes shown by the idle dial (from cfg)."""
        self._focus_minutes = max(1, int(focus_minutes))
        self._break_minutes = max(1, int(break_minutes))
        if not self._running:
            self.ring.set_dial(self._focus_minutes, self._break_minutes)

    def _on_dial(self, target: str, minutes: int) -> None:
        if target == "focus":
            self._focus_minutes = minutes
        else:
            self._break_minutes = minutes
        self.duration_dialed.emit(target, minutes)

    def _update_dial_mode(self) -> None:
        """Switch the ring between the idle dial and the live countdown — ONLY on a
        real transition, so the running arc isn't reset to 0 on every tick."""
        editing = not self._running
        if self._dial_active == editing:
            return
        self._dial_active = editing
        if editing:
            self.ring.set_dial(self._focus_minutes, self._break_minutes)
        else:
            self.ring.set_run_mode()      # resets the arc to empty exactly once

    # ------------------------------------------------------------------ #
    # PUBLIC API
    # ------------------------------------------------------------------ #
    def set_mode(self, name: str) -> None:
        self.ring.set_mode(name)

    def set_duration(self, seconds: int) -> None:
        """Set the focus duration (sec) for the idle dial + reset the countdown."""
        self._total = max(1, int(seconds))
        self._remaining = self._total
        self._focus_minutes = max(1, self._total // 60)
        self.ring.set_remaining(self._remaining)
        self._sync_buttons()
        if not self._running:
            self.ring.set_dial(self._focus_minutes, self._break_minutes)

    def remaining(self) -> int:
        return self._remaining

    def set_controller(self, controller) -> None:
        self._controller = controller

    def set_session_display(self, remaining_sec, phase: str, mode: str,
                            ring_fraction: float = 0.0) -> None:
        """Display mode: the controller dictates time / phase / ring fraction."""
        self.ring.set_remaining(0 if remaining_sec is None else int(remaining_sec))
        self.ring.set_mode("Break" if phase == "break" else mode)
        self._running = (phase in ("focus", "break"))
        self._update_dial_mode()          # idempotent: switches only on transition
        if self._running:
            self.ring.animate_to(max(0.0, min(1.0, float(ring_fraction))))
        self._sync_buttons()

    # ------------------------------------------------------------------ #
    # CONTROL
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._controller is not None:
            self.started.emit()
            self._controller.start_session()
            return
        if self._running:
            return
        if self._remaining <= 0:
            self._remaining = self._total
        self._running = True
        self._update_dial_mode()
        self._countdown.start()
        self._sync_buttons()
        self.started.emit()

    def pause(self) -> None:
        if self._controller is not None:
            self.paused.emit()
            self._controller.toggle_pause()
            return
        if not self._running:
            return
        self._running = False
        self._countdown.stop()
        self._sync_buttons()
        self.paused.emit()

    def stop(self) -> None:
        if self._controller is not None:
            self.stopped.emit()
            self._controller.stop_session()
            return
        self._running = False
        self._countdown.stop()
        self._remaining = self._total
        self.ring.set_remaining(self._remaining)
        self._update_dial_mode()
        self._sync_buttons()
        self.stopped.emit()

    # ------------------------------------------------------------------ #
    def _on_second(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._remaining = 0
            self.ring.set_remaining(0)
            self.ring.animate_to(1.0)
            self._running = False
            self._countdown.stop()
            self._sync_buttons()
            self.tick.emit(0)
            self.finished.emit()
            return
        self.ring.set_remaining(self._remaining)
        self.ring.animate_to(1.0 - (self._remaining / self._total))
        self.tick.emit(self._remaining)

    def _sync_buttons(self) -> None:
        self.btn_start.setEnabled(not self._running)
        self.btn_pause.setEnabled(self._running)
        self.btn_stop.setEnabled(self._remaining != self._total or self._running)
