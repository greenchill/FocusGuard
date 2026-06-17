# -*- coding: utf-8 -*-
"""
widget_settings.py — SettingsWidget: the FocusGuard Settings page.

Contents (everything inside cards):
- Camera calibration: 'Remember my normal' button.
- Camera selection: QComboBox (placeholder list of devices).
- Sensitivity: Relaxed / Normal / Strict radio buttons.
- Hardcore site block: QCheckBox + multi-line domains field (QPlainTextEdit).
- Volume: QSlider.

A separate signal is emitted on each change — the backend listens for them:
    calibrate_requested ()           — calibration pressed.
    camera_changed (str)             — a different camera was selected.
    sensitivity_changed (str)        — 'relaxed' / 'normal' / 'strict'.
    blocking_toggled (bool)          — hardcore site block enabled/disabled.
    domains_changed (str)            — domain list changed (full text).
    volume_changed (int)             — volume 0..100.
    restart_admin_requested ()       — 'Restart as administrator' pressed.
"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
    QRadioButton, QButtonGroup, QCheckBox, QPlainTextEdit, QSlider, QFrame,
    QSpinBox, QScrollArea, QGraphicsDropShadowEffect,
)
from PyQt6.QtGui import QColor

from theme import COLORS, SPACING
import icons

# Site blocking edits the system hosts file, which needs admin rights. We read the
# current domain list / admin status straight from the firewall module so the page
# reflects reality on construct. Imported defensively: the Settings page must still
# build if firewall.py is missing for any reason.
try:
    import firewall
except Exception:  # pragma: no cover - firewall is part of the project
    firewall = None


def _card(title: str) -> tuple:
    """Create a section card with a header. Return (frame, inner_layout).

    No drop shadow here: inside the QScrollArea the shadow rendered as a hard dark
    rectangle ("dark outline"); the QSS #Card border alone reads cleanly."""
    frame = QFrame()
    frame.setObjectName("Card")

    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)
    header = QLabel(title)
    header.setObjectName("CardTitle")
    layout.addWidget(header)
    return frame, layout


class SettingsWidget(QWidget):
    """Settings page with a signal for each change (for the backend)."""

    use_camera_toggled = pyqtSignal(bool)    # master: run with/without the webcam
    calibrate_requested = pyqtSignal()
    camera_changed = pyqtSignal(str)
    sensitivity_changed = pyqtSignal(str)
    blocking_toggled = pyqtSignal(bool)
    domains_changed = pyqtSignal(str)
    volume_changed = pyqtSignal(int)
    restart_admin_requested = pyqtSignal()
    mute_toggled = pyqtSignal(bool)          # mute all sounds
    reduce_motion_toggled = pyqtSignal(bool)  # accessibility: less motion
    light_mode_toggled = pyqtSignal(bool)     # performance: lower analysis cadence
    focus_minutes_changed = pyqtSignal(int)   # Pomodoro focus length (minutes)
    break_minutes_changed = pyqtSignal(int)   # Pomodoro break length (minutes)
    brown_noise_toggled = pyqtSignal(bool)    # loop brown noise during focus

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING["lg"], SPACING["lg"], SPACING["lg"], SPACING["md"])
        root.setSpacing(SPACING["md"])

        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # All cards live in a scroll area so they never overlap or clip on a short
        # window (the old fixed layout squeezed cards into each other). The page
        # scrolls vertically instead.
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        content = QVBoxLayout(inner)
        content.setContentsMargins(0, 0, 8, 0)   # small right pad so cards clear the scrollbar
        content.setSpacing(SPACING["md"])
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

        # ----------------------- Camera and calibration ----------------------- #
        cam_card, cam_layout = _card("Camera")

        # Master toggle: run with the webcam (focus tracking) or as a pure Pomodoro
        # timer. When off, calibration + device selection are disabled and no camera
        # is ever opened.
        self.use_camera_check = QCheckBox("Use camera for focus tracking")
        self.use_camera_check.setToolTip(
            "Off: run as a plain Pomodoro timer — no webcam, no detection. "
            "You still earn focus time for completed sessions.")
        self.use_camera_check.setChecked(True)
        self.use_camera_check.toggled.connect(self.use_camera_toggled.emit)
        self.use_camera_check.toggled.connect(self._sync_camera_controls)
        cam_layout.addWidget(self.use_camera_check)

        self.calibrate_btn = QPushButton("  Remember my normal")
        self.calibrate_btn.setObjectName("PrimaryButton")
        self.calibrate_btn.setIcon(icons.icon("target", COLORS["ink"], 16))
        self.calibrate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.calibrate_btn.clicked.connect(self.calibrate_requested.emit)
        cam_layout.addWidget(self.calibrate_btn)

        cam_row = QHBoxLayout()
        cam_row.setSpacing(10)
        cam_row.addWidget(QLabel("Device"))
        self.camera_combo = QComboBox()
        # Placeholder list — the backend will substitute the real cameras.
        self.camera_combo.addItems(["Default camera", "USB camera 1", "USB camera 2"])
        self.camera_combo.currentTextChanged.connect(self.camera_changed.emit)
        cam_row.addWidget(self.camera_combo, stretch=1)
        cam_layout.addLayout(cam_row)
        content.addWidget(cam_card)

        # ----------------------- Pomodoro (custom durations) ----------------------- #
        sess_card, sess_layout = _card("Pomodoro")
        sess_grid = QGridLayout()
        sess_grid.setHorizontalSpacing(12)
        sess_grid.setVerticalSpacing(10)
        sess_grid.addWidget(QLabel("Focus length"), 0, 0)
        self.focus_spin = QSpinBox()
        self.focus_spin.setRange(5, 120)        # matches the ring dial (RingTimer)
        self.focus_spin.setSingleStep(5)
        self.focus_spin.setValue(50)
        self.focus_spin.setSuffix(" min")
        self.focus_spin.valueChanged.connect(self.focus_minutes_changed.emit)
        sess_grid.addWidget(self.focus_spin, 0, 1)
        sess_grid.addWidget(QLabel("Break length"), 1, 0)
        self.break_spin = QSpinBox()
        self.break_spin.setRange(1, 30)         # matches the ring dial (RingTimer)
        self.break_spin.setValue(10)
        self.break_spin.setSuffix(" min")
        self.break_spin.valueChanged.connect(self.break_minutes_changed.emit)
        sess_grid.addWidget(self.break_spin, 1, 1)
        sess_grid.setColumnStretch(1, 1)
        sess_layout.addLayout(sess_grid)
        content.addWidget(sess_card)

        # ----------------------- Sensitivity ----------------------- #
        sens_card, sens_layout = _card("Detector sensitivity")
        sens_row = QHBoxLayout()
        self.sensitivity_group = QButtonGroup(self)
        # (label, internal value)
        presets = [("Relaxed", "relaxed"), ("Normal", "normal"), ("Strict", "strict")]
        self._sens_values = {}
        for label, value in presets:
            radio = QRadioButton(label)
            self.sensitivity_group.addButton(radio)
            self._sens_values[radio] = value
            sens_row.addWidget(radio)
            if value == "normal":
                radio.setChecked(True)  # default value
        self.sensitivity_group.buttonClicked.connect(self._on_sensitivity)
        sens_layout.addLayout(sens_row)
        content.addWidget(sens_card)

        # ----------------------- Hardcore site block ----------------------- #
        block_card, block_layout = _card("Distracting site blocking")
        self.hardcore_check = QCheckBox("Enable hardcore site block (requires admin rights)")
        self.hardcore_check.toggled.connect(self.blocking_toggled.emit)
        block_layout.addWidget(self.hardcore_check)

        # Admin row: a live hint + a button to relaunch the app elevated. Writing the
        # hosts file is impossible without admin, so make the requirement visible here.
        admin_row = QHBoxLayout()
        self.admin_hint = QLabel("Admin: …")
        self.admin_hint.setObjectName("Muted")
        admin_row.addWidget(self.admin_hint)
        admin_row.addStretch(1)
        self.restart_admin_btn = QPushButton("Restart as administrator")
        self.restart_admin_btn.setObjectName("PrimaryButton")
        self.restart_admin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_admin_btn.clicked.connect(self.restart_admin_requested.emit)
        admin_row.addWidget(self.restart_admin_btn)
        block_layout.addLayout(admin_row)

        block_layout.addWidget(QLabel("Domains (one per line):"))
        self.domains_edit = QPlainTextEdit()
        self.domains_edit.setObjectName("DomainsEdit")
        self.domains_edit.setPlaceholderText("youtube.com\nvk.com\ntiktok.com")
        self.domains_edit.setFixedHeight(110)
        # Debounce: typing in the domains box fires textChanged on EVERY keystroke.
        # Emitting domains_changed per keystroke makes the backend rewrite the system
        # hosts file (+ flush DNS) for every character while a session is live. Route
        # it through a single-shot timer so domains_changed fires at most once, ~500 ms
        # after the user stops typing (and never with a half-typed domain mid-session).
        self._domains_timer = QTimer(self)
        self._domains_timer.setSingleShot(True)
        self._domains_timer.setInterval(500)
        self._domains_timer.timeout.connect(self._emit_domains)
        self.domains_edit.textChanged.connect(self._on_domains)
        block_layout.addWidget(self.domains_edit)
        content.addWidget(block_card)

        # Reflect the admin status on construct (and let the backend refresh it later).
        self.refresh_admin_hint()

        # ----------------------- Volume ----------------------- #
        vol_card, vol_layout = _card("Sound")
        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)
        _vol_icon = QLabel()
        _vol_icon.setPixmap(icons.pixmap("volume", COLORS["muted"], 18))
        vol_row.addWidget(_vol_icon)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(self.volume_changed.emit)
        vol_row.addWidget(self.volume_slider, stretch=1)
        self._vol_value = QLabel("70")
        self._vol_value.setObjectName("Muted")
        self.volume_slider.valueChanged.connect(lambda v: self._vol_value.setText(str(v)))
        vol_row.addWidget(self._vol_value)
        vol_layout.addLayout(vol_row)
        self.mute_check = QCheckBox("Mute all sounds")
        self.mute_check.toggled.connect(self.mute_toggled.emit)
        vol_layout.addWidget(self.mute_check)
        self.brown_noise_check = QCheckBox("Play brown noise while focusing")
        self.brown_noise_check.setToolTip(
            "Loop calming brown noise during focus phases. It pauses on breaks and "
            "continues afterwards, and stops when the session ends.")
        self.brown_noise_check.toggled.connect(self.brown_noise_toggled.emit)
        vol_layout.addWidget(self.brown_noise_check)
        content.addWidget(vol_card)

        # ----------------------- Accessibility ----------------------- #
        a11y_card, a11y_layout = _card("Accessibility")
        self.reduce_motion_check = QCheckBox("Reduce motion (less blinking and animation)")
        self.reduce_motion_check.setToolTip(
            "Hold steady colors instead of blinking chips, and keep the cat still.")
        self.reduce_motion_check.toggled.connect(self.reduce_motion_toggled.emit)
        a11y_layout.addWidget(self.reduce_motion_check)
        content.addWidget(a11y_card)

        # ----------------------- Performance ----------------------- #
        perf_card, perf_layout = _card("Performance")
        self.light_mode_check = QCheckBox("Light mode (save CPU / battery)")
        self.light_mode_check.setToolTip(
            "Analyze fewer frames to lower CPU and battery use. "
            "Detection is slightly less responsive.")
        self.light_mode_check.toggled.connect(self.light_mode_toggled.emit)
        perf_layout.addWidget(self.light_mode_check)
        content.addWidget(perf_card)

        content.addStretch(1)

        # Prefill the domain list from blocklist.txt so the field shows what is
        # actually blocked, not an empty box. Done last, with signals blocked, so the
        # initial fill does not re-emit domains_changed back into the backend.
        self._prefill_domains()

    # ------------------------------------------------------------------ #
    def _prefill_domains(self) -> None:
        """Fill the domains field from firewall.load_domains() without firing signals."""
        if firewall is None:
            return
        try:
            domains = firewall.load_domains()
        except Exception:
            domains = []
        self.domains_edit.blockSignals(True)
        self.domains_edit.setPlainText("\n".join(domains))
        self.domains_edit.blockSignals(False)

    def set_use_camera(self, on: bool) -> None:
        """Set the 'Use camera' checkbox from cfg without re-emitting, and reflect it
        on the dependent camera controls."""
        self.use_camera_check.blockSignals(True)
        self.use_camera_check.setChecked(bool(on))
        self.use_camera_check.blockSignals(False)
        self._sync_camera_controls(bool(on))

    def _sync_camera_controls(self, on: bool) -> None:
        """Enable/disable calibration + device selection with the master camera toggle."""
        for w in (self.calibrate_btn, self.camera_combo):
            try:
                w.setEnabled(bool(on))
            except Exception:
                pass

    def set_blocking_enabled(self, enabled: bool) -> None:
        """Set the 'Hardcore site block' checkbox from cfg without re-emitting."""
        self.hardcore_check.blockSignals(True)
        self.hardcore_check.setChecked(bool(enabled))
        self.hardcore_check.blockSignals(False)

    def set_volume(self, value_0_100: int) -> None:
        """Set the volume slider from cfg (0..100) without re-emitting volume_changed."""
        v = max(0, min(100, int(value_0_100)))
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(v)
        self.volume_slider.blockSignals(False)
        self._vol_value.setText(str(v))

    def set_muted(self, muted: bool) -> None:
        """Set the mute checkbox from cfg without re-emitting."""
        self.mute_check.blockSignals(True)
        self.mute_check.setChecked(bool(muted))
        self.mute_check.blockSignals(False)

    def set_brown_noise(self, on: bool) -> None:
        """Set the brown-noise checkbox from cfg without re-emitting."""
        self.brown_noise_check.blockSignals(True)
        self.brown_noise_check.setChecked(bool(on))
        self.brown_noise_check.blockSignals(False)

    def set_reduce_motion(self, on: bool) -> None:
        """Set the reduce-motion checkbox from cfg without re-emitting."""
        self.reduce_motion_check.blockSignals(True)
        self.reduce_motion_check.setChecked(bool(on))
        self.reduce_motion_check.blockSignals(False)

    def set_light_mode(self, on: bool) -> None:
        """Set the light-mode checkbox from cfg without re-emitting."""
        self.light_mode_check.blockSignals(True)
        self.light_mode_check.setChecked(bool(on))
        self.light_mode_check.blockSignals(False)

    def set_session_minutes(self, focus: int, brk: int) -> None:
        """Set the Pomodoro focus/break spinboxes from cfg without re-emitting."""
        for spin, val in ((self.focus_spin, focus), (self.break_spin, brk)):
            spin.blockSignals(True)
            spin.setValue(int(val))
            spin.blockSignals(False)

    def refresh_admin_hint(self) -> None:
        """Update the 'Admin: yes/no' hint and hide the restart button when elevated."""
        is_admin = False
        if firewall is not None:
            try:
                is_admin = bool(firewall.is_admin())
            except Exception:
                is_admin = False
        self.admin_hint.setText("Admin: yes" if is_admin else "Admin: no")
        # No point offering a relaunch when we are already elevated.
        self.restart_admin_btn.setVisible(not is_admin)

    # ------------------------------------------------------------------ #
    def _on_sensitivity(self, button) -> None:
        """Convert the selected radio button to its internal value and emit a signal."""
        value = self._sens_values.get(button, "normal")
        self.sensitivity_changed.emit(value)

    def _on_domains(self) -> None:
        """Any edit (re)starts the debounce timer instead of emitting immediately.

        Each keystroke restarts the single-shot timer, so domains_changed is emitted
        only once the user has paused typing for the debounce interval. This keeps the
        backend's hosts-file write off the per-keystroke path."""
        self._domains_timer.start()

    def _emit_domains(self) -> None:
        """Debounce fired: pass out the full text of the domain list."""
        self.domains_changed.emit(self.domains_edit.toPlainText())
