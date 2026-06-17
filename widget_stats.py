# -*- coding: utf-8 -*-
"""
widget_stats.py — StatsWidget: the Stats page.

Contents:
- BarChart: a 7-day bar chart of focus time, drawn by hand with
  QPainter (no matplotlib/pyqtgraph — zero dependencies). X-axis labels Mon..Sun.
- 'Daily goal' — QProgressBar.
- Clean card-based layout in the app's style.

Backend integration:
    set_week_data([m, t, w, ...])   — 7 values (focus minutes per day).
    set_daily_goal(done, target)    — fill in the daily goal progress.
"""

import datetime

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QLinearGradient, QFont, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QFrame, QSpinBox,
    QGraphicsDropShadowEffect,
)

from theme import COLORS, SPACING, FONTS


# Weekday abbreviations indexed by date.weekday() (0 = Monday).
_WD_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _last_7_weekday_labels():
    """Weekday labels for the last 7 days ENDING TODAY (so the rightmost bar = today).

    The chart data is last_n_days(7) ending today, so a fixed Mon..Sun axis mislabels it
    (e.g. today's bar shown as 'Sun'). Compute the real labels instead."""
    today = datetime.date.today()
    return [_WD_NAMES[(today - datetime.timedelta(days=(6 - i))).weekday()] for i in range(7)]


class BarChart(QWidget):
    """Bar chart (7 bars) with labels and gridlines, drawn with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        # Placeholder data (focus minutes per day) — the backend will replace it.
        self._data = [45, 80, 60, 120, 95, 30, 70]

    def set_data(self, values) -> None:
        """Set 7 values (minutes) and redraw."""
        if values:
            self._data = list(values)[:7]
            # Pad up to 7 if there is less data.
            while len(self._data) < 7:
                self._data.append(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        pad_left = 8
        pad_bottom = 26   # room for the day labels
        pad_top = 12
        chart_w = w - pad_left * 2
        chart_h = h - pad_bottom - pad_top

        max_val = max(self._data) if self._data and max(self._data) > 0 else 1

        # Horizontal gridlines (4 levels) — dim.
        grid_pen = QPen(QColor(COLORS["track"]))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        for i in range(1, 4):
            y = pad_top + chart_h * i / 4
            painter.drawLine(int(pad_left), int(y), int(pad_left + chart_w), int(y))

        # Bars.
        n = len(self._data)
        slot = chart_w / n
        bar_w = slot * 0.55
        label_font = QFont(FONTS["body"], 9)
        weekdays = _last_7_weekday_labels()   # real labels (rightmost = today)

        for i, value in enumerate(self._data):
            bar_h = (value / max_val) * chart_h
            x = pad_left + slot * i + (slot - bar_w) / 2
            y = pad_top + (chart_h - bar_h)
            rect = QRectF(x, y, bar_w, bar_h)

            # Bar gradient from bottom to top (accent -> soft accent).
            gradient = QLinearGradient(x, y + bar_h, x, y)
            gradient.setColorAt(0.0, QColor(COLORS["accent"]))
            gradient.setColorAt(1.0, QColor(COLORS["accent_soft"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(rect, 5, 5)

            # Weekday label below the bar.
            painter.setPen(QColor(COLORS["muted"]))
            painter.setFont(label_font)
            day = weekdays[i] if i < len(weekdays) else ""
            label_rect = QRectF(pad_left + slot * i, h - pad_bottom + 4, slot, 18)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, day)


class StatsWidget(QWidget):
    """Stats page: weekly chart + an editable daily goal."""

    daily_goal_changed = pyqtSignal(int)   # the user edited the daily goal (minutes)

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING["lg"], SPACING["lg"], SPACING["lg"], SPACING["lg"])
        root.setSpacing(SPACING["lg"])

        title = QLabel("Stats")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # --- Chart card. --- #
        chart_card = QFrame()
        chart_card.setObjectName("Card")
        self._shadow(chart_card)
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(20, 18, 20, 18)
        chart_layout.setSpacing(12)

        chart_title = QLabel("Focus this week (minutes)")
        chart_title.setObjectName("CardTitle")
        chart_layout.addWidget(chart_title)

        self.chart = BarChart()
        chart_layout.addWidget(self.chart)
        root.addWidget(chart_card)

        # --- Daily goal card. --- #
        goal_card = QFrame()
        goal_card.setObjectName("Card")
        self._shadow(goal_card)
        goal_layout = QVBoxLayout(goal_card)
        goal_layout.setContentsMargins(20, 18, 20, 18)
        goal_layout.setSpacing(10)

        # Title + value label on one line.
        # IMPORTANT (contrast): we do NOT draw the value inside the bar
        # (setTextVisible(False)) — white text on the green fill (#34D399)
        # gives ~1.67:1 contrast and fails WCAG. We place the label next to the
        # title, on the card's dark background.
        goal_header = QHBoxLayout()
        goal_header.setContentsMargins(0, 0, 0, 0)
        goal_header.setSpacing(8)
        goal_title = QLabel("Daily goal")
        goal_title.setObjectName("CardTitle")
        goal_header.addWidget(goal_title)
        goal_header.addStretch(1)
        # Editable target (minutes). Steps of 5; emits daily_goal_changed.
        goal_header.addWidget(QLabel("Target"))
        self.goal_spin = QSpinBox()
        self.goal_spin.setRange(5, 600)
        self.goal_spin.setSingleStep(5)
        self.goal_spin.setValue(120)
        self.goal_spin.setSuffix(" min")
        self.goal_spin.setToolTip("Your daily focus goal in minutes.")
        self.goal_spin.valueChanged.connect(self.daily_goal_changed.emit)
        goal_header.addWidget(self.goal_spin)
        goal_layout.addLayout(goal_header)

        # Progress caption under the header, on the card's dark background (good contrast).
        self._goal_value = QLabel("0 / 120 min")
        self._goal_value.setObjectName("Muted")
        self._goal_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        goal_layout.addWidget(self._goal_value)

        self.goal_bar = QProgressBar()
        self.goal_bar.setObjectName("GoalBar")
        self.goal_bar.setTextVisible(False)   # text moved out to _goal_value
        self.goal_bar.setRange(0, 240)   # default goal: 240 minutes
        self.goal_bar.setValue(150)
        goal_layout.addWidget(self.goal_bar)

        root.addWidget(goal_card)
        root.addStretch(1)

    # ------------------------------------------------------------------ #
    def _shadow(self, widget: QWidget) -> None:
        """Soft shadow for a card (QSS does not support box-shadow)."""
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(26)
        effect.setOffset(0, 5)
        effect.setColor(QColor(120, 105, 75, 55))
        widget.setGraphicsEffect(effect)

    # ------------------------------------------------------------------ #
    # PUBLIC SLOTS FOR THE BACKEND                                         #
    # ------------------------------------------------------------------ #
    def set_week_data(self, values) -> None:
        """Slot: set 7 focus-minute values per day (Mon..Sun)."""
        self.chart.set_data(values)

    def set_daily_goal(self, done: int, target: int) -> None:
        """Slot: update daily goal progress (done / target, in minutes).

        Also syncs the target spinbox without re-emitting, so the control reflects
        the backend value (e.g. on load) without a feedback loop."""
        target = max(1, target)
        done = max(0, min(done, target))
        self.goal_bar.setRange(0, target)
        self.goal_bar.setValue(done)
        # The value label lives outside the bar — keep it in sync.
        self._goal_value.setText(f"{done} / {target} min")
        self.goal_spin.blockSignals(True)
        self.goal_spin.setValue(target)
        self.goal_spin.blockSignals(False)
