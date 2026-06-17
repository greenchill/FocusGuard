# -*- coding: utf-8 -*-
"""
widget_dashboard.py — DashboardWidget: the application's main page.

Layout:
- TOP PANEL (gamification): XP bar + IconBadges (vector icons, no emoji): level
  (star), streak (flame), combo (bolt) — see IconBadge.
- CENTER: TimerWidget (ring timer).
- CAMERA ZONE (card on the right): green camera-status "dot" +
  three detection chips — 'Phone', 'Gaze', 'Posture'. Each chip has a slot
  set_status(name, bad): when bad=True the chip turns red and blinks, otherwise calm green.
- PET ZONE: embedded PetWidget. When a detection chip goes "bad" we call pet.react(...).

Signals/slots for the future backend:
    set_xp(current, maximum)        — update the XP bar.
    set_level(level)                — update the level badge.
    set_streak(days)                — update the streak counter.
    set_combo(multiplier)           — update the combo chip.
    set_detection(name, bad)        — update a detection chip ('phone'/'gaze'/'posture').
"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QProgressBar, QFrame,
    QGraphicsDropShadowEffect, QSizePolicy, QPushButton,
)
from PyQt6.QtGui import QColor, QPixmap

from theme import COLORS, SPACING
from widget_timer import TimerWidget
from widget_pet import PetWidget
import icons


# --------------------------------------------------------------------------- #
# HELPER: add a soft shadow to a card (QSS can't do box-shadow).               #
# --------------------------------------------------------------------------- #
def apply_card_shadow(widget: QWidget) -> None:
    """Attach a QGraphicsDropShadowEffect to a card (soft lifted-shadow look).

    IMPORTANT: one widget = one effect. We attach the shadow to the card itself,
    and leave margins around it in the layout so the blur doesn't get clipped.
    """
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(26)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(120, 105, 75, 55))   # soft warm shadow for the light theme
    widget.setGraphicsEffect(shadow)


def make_card(object_name: str = "Card") -> QFrame:
    """Create a rounded QFrame card (styles come from QSS by objectName)."""
    card = QFrame()
    card.setObjectName(object_name)
    apply_card_shadow(card)
    return card


# --------------------------------------------------------------------------- #
# DETECTION CHIP (Phone / Gaze / Posture) that blinks on a "bad" status.       #
# --------------------------------------------------------------------------- #
class DetectionChip(QFrame):
    """Detection-status chip. set_status(bad) toggles the calm/alarmed look + blinking."""

    def __init__(self, title: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Chip")
        self._title = title
        self._bad = False
        self._blink_on = False
        self._reduce_motion = False
        if tooltip:
            self.setToolTip(tooltip)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 6, 11, 6)
        layout.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setObjectName("ChipDot")
        self._label = QLabel(title)
        self._label.setObjectName("ChipLabel")
        layout.addWidget(self._dot)
        layout.addWidget(self._label)

        # Blink timer (only runs while in the "bad" state).
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(450)
        self._blink_timer.timeout.connect(self._toggle_blink)

        # Accessibility: don't rely on color alone. Seed the a11y text + dot glyph
        # for the initial "ok" state (set_status keeps both in sync afterwards).
        self._apply_a11y(False)
        self._refresh_style()

    # Colorblind-friendly glyphs: a distinct SHAPE per state, not just a color.
    _DOT_OK = "●"      # calm, round
    _DOT_BAD = "▲"     # alert, triangle (reads as "warning" without color)

    def _apply_a11y(self, bad: bool) -> None:
        """Set the dot glyph + screen-reader accessibleName/Description for the state.

        Color + shape + a11y text together make the chip robust for colorblind users
        and screen readers (never color-alone)."""
        self._dot.setText(self._DOT_BAD if bad else self._DOT_OK)
        state = "alert" if bad else "ok"
        self.setAccessibleName(f"{self._title}: {state}")
        self.setAccessibleDescription(
            f"{self._title} detection is {'flagged' if bad else 'clear'}.")

    def set_status(self, bad: bool) -> None:
        """Slot: bad=True -> chip turns red (blinks, or steady under reduce-motion);
        bad=False -> calm green. Also swaps the dot glyph + a11y text so the state
        is conveyed by shape and screen-reader name, not color alone."""
        self._bad = bad
        self._apply_a11y(bad)
        if bad:
            if self._reduce_motion:
                self._blink_timer.stop()
                self._blink_on = True      # steady red, no blinking
            elif not self._blink_timer.isActive():
                self._blink_timer.start()
        else:
            self._blink_timer.stop()
            self._blink_on = False
        self._refresh_style()

    def set_reduce_motion(self, on: bool) -> None:
        """Accessibility: stop blinking; show a steady color instead."""
        self._reduce_motion = bool(on)
        if self._bad:
            if on:
                self._blink_timer.stop()
                self._blink_on = True
            elif not self._blink_timer.isActive():
                self._blink_timer.start()
        self._refresh_style()

    def _toggle_blink(self) -> None:
        self._blink_on = not self._blink_on
        self._refresh_style()

    def _refresh_style(self) -> None:
        """Recolor the dot and border via inline style (blinking = swapping opacity)."""
        if self._bad:
            color = COLORS["danger"]
            # While blinking we "dim" the dot.
            dot_color = color if self._blink_on else COLORS["muted"]
            border = color
        else:
            color = COLORS["success"]
            dot_color = color
            border = COLORS["border"]
        self._dot.setStyleSheet(f"color: {dot_color}; font-size: 12px;")
        self.setStyleSheet(
            f"#Chip {{ background-color: {COLORS['surface']};"
            f" border: 1px solid {border}; border-radius: 12px; }}"
            f"#ChipLabel {{ color: {COLORS['text']}; }}"
        )


# --------------------------------------------------------------------------- #
# TOP BADGE (a rounded "pill" for level/streak/combo, with a vector icon).      #
# --------------------------------------------------------------------------- #
class IconBadge(QFrame):
    """Small rounded pill badge: an SVG icon + a value label (no emoji).

    Used for LEVEL (star), streak (flame) and combo (bolt). set_value() updates
    just the text; the icon stays put."""

    def __init__(self, icon_name: str, text: str, accent: str = "accent", parent=None):
        super().__init__(parent)
        self.setObjectName("Badge")
        self._accent = accent
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 5, 12, 5)
        lay.setSpacing(6)
        self._icon = QLabel()
        self._icon.setPixmap(icons.pixmap(icon_name, COLORS[accent], 15))
        self._icon.setFixedWidth(15)
        self._label = QLabel(text)
        self._label.setObjectName("BadgeText")
        lay.addWidget(self._icon)
        lay.addWidget(self._label)
        self._restyle()

    def set_value(self, text: str) -> None:
        self._label.setText(text)

    def _restyle(self) -> None:
        self.setStyleSheet(
            f"#Badge {{ background-color: {COLORS['elevated']};"
            f" border: 1px solid {COLORS[self._accent]}; border-radius: 12px; }}"
            f"#BadgeText {{ color: {COLORS[self._accent]}; font-weight: bold; }}"
        )


# --------------------------------------------------------------------------- #
# CAMERA PREVIEW (aspect-locked, fits the frame — no crop, no stretch).         #
# --------------------------------------------------------------------------- #
class CameraPreview(QLabel):
    """Live camera preview that FITs the 16:9 frame (full face, never cropped) onto a
    card-colored background.

    Two properties matter:
      * heightForWidth keeps a deterministic 16:9-ish height (clamped), so the box does
        NOT stretch or jump when the layout reflows (e.g. while calibrating) — the old
        Expanding height fluctuated and looked like the preview was resizing.
      * KeepAspectRatio (fit, not cover) shows the whole frame; any slack around it is
        the card color, so there are no visible letterbox bars.
    """

    _ASPECT = 9.0 / 16.0     # height / width of the camera frame (320x180)
    _MIN_H = 70
    _MAX_H = 190

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None
        self._off_pix = None       # camera-off placeholder avatar
        self._mode = "off"         # 'frame' = live feed, 'off' = avatar/placeholder
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumHeight(self._MIN_H)
        self.setMaximumHeight(self._MAX_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return max(self._MIN_H, min(self._MAX_H, int(round(w * self._ASPECT))))

    def set_off_image(self, pix: QPixmap) -> None:
        """Set the placeholder shown when the camera is off (a 'no camera' avatar)."""
        self._off_pix = pix

    def set_frame(self, pix: QPixmap) -> None:
        self._src = pix
        self._mode = "frame"
        self.setText("")
        self._rescale()

    def show_off(self) -> None:
        """Switch to the camera-off placeholder (the avatar, or text if none)."""
        self._src = None
        self._mode = "off"
        self._rescale()

    def clear_frame(self) -> None:
        self.show_off()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._mode == "frame":
            if self._src is None or self._src.isNull():
                return
            scaled = self._src.scaled(
                max(1, self.width()), max(1, self.height()),
                Qt.AspectRatioMode.KeepAspectRatio,       # FIT: whole frame, no crop
                Qt.TransformationMode.SmoothTransformation,
            )
            super().setPixmap(scaled)
            return
        # OFF mode: show the avatar centered at ~62% of the box height, else fall to text.
        if self._off_pix is not None and not self._off_pix.isNull():
            self.setText("")
            side = max(36, int(min(self.width(), self.height()) * 0.62))
            super().setPixmap(self._off_pix.scaled(
                side, side, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            super().setPixmap(QPixmap())
            self.setText("Camera off")


# --------------------------------------------------------------------------- #
# MAIN DASHBOARD WIDGET                                                        #
# --------------------------------------------------------------------------- #
class DashboardWidget(QWidget):
    """Main page: gamification + timer + camera + pet."""

    # Outward signals (in case the parent needs to know about events).
    detection_changed = pyqtSignal(str, bool)  # (name, bad)
    pause_toggled = pyqtSignal(bool)            # user paused/resumed detection
    brown_noise_toggled = pyqtSignal(bool)      # user toggled focus brown noise

    # Maps a detection name -> the pet's reaction type.
    _REACTION_MAP = {
        "phone": "phone",
        "gaze": "distract",
        "posture": "posture",
    }

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"])
        root.setSpacing(SPACING["md"])

        # NOTE: the XP / level / streak / combo top panel was removed per user
        # request (it added clutter). The gamification still runs in GameController;
        # set_xp/set_level/set_streak/set_combo below are now no-ops.

        # ===================== MIDDLE ROW: timer + camera/cat ===================== #
        middle = QHBoxLayout()
        middle.setSpacing(SPACING["md"])

        # --- Left big card: the timer (the page's hero). --- #
        timer_card = make_card()
        timer_layout = QVBoxLayout(timer_card)
        timer_layout.setContentsMargins(18, 16, 18, 16)
        timer_title = QLabel("Focus session")
        timer_title.setObjectName("CardTitle")
        timer_layout.addWidget(timer_title)
        self.timer = TimerWidget(duration_seconds=25 * 60)
        self.timer.set_mode("Pomodoro")
        # When the session finishes, the cat gives praise.
        self.timer.finished.connect(lambda: self.pet.react("praise"))
        self.timer.started.connect(lambda: self.pet.react("idle"))
        # Center the ring vertically in the card so it doesn't float at the top
        # with a big empty gap (a "bulky" look the user called out).
        timer_layout.addStretch(1)
        timer_layout.addWidget(self.timer)
        # Brown-noise toggle: a calm, optional focus companion (loops during focus,
        # pauses on breaks). Lives on the main screen right under the timer.
        noise_row = QHBoxLayout()
        noise_row.addStretch(1)
        self.brown_noise_btn = QPushButton("  Brown noise")
        self.brown_noise_btn.setObjectName("GhostButton")
        self.brown_noise_btn.setCheckable(True)
        self.brown_noise_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.brown_noise_btn.setIcon(icons.icon("volume", COLORS["muted"], 16))
        self.brown_noise_btn.setToolTip(
            "Loop calming brown noise while you focus — it pauses on breaks and "
            "continues after.")
        self.brown_noise_btn.toggled.connect(self._on_brown_noise_toggled)
        noise_row.addWidget(self.brown_noise_btn)
        noise_row.addStretch(1)
        timer_layout.addLayout(noise_row)
        timer_layout.addStretch(1)
        middle.addWidget(timer_card, stretch=2)

        # --- Right column: camera on top, cat on the bottom. --- #
        right_col = QVBoxLayout()
        right_col.setSpacing(SPACING["md"])

        # Camera card.
        camera_card = make_card()
        cam_layout = QVBoxLayout(camera_card)
        cam_layout.setContentsMargins(16, 13, 16, 13)
        cam_layout.setSpacing(9)

        cam_header = QHBoxLayout()
        self._cam_dot = QLabel()
        self._cam_dot.setPixmap(icons.dot(COLORS['success'], 11))
        cam_title = QLabel("Camera")
        cam_title.setObjectName("CardTitle")
        cam_header.addWidget(self._cam_dot)
        cam_header.addWidget(cam_title)
        cam_header.addStretch(1)
        # Pause/Snooze: stop detection so the cat stops watching and won't nag.
        self.pause_btn = QPushButton(" Pause")
        self.pause_btn.setIcon(icons.icon("pause", COLORS["muted"], 16))
        self.pause_btn.setObjectName("GhostButton")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.setToolTip("Pause detection — the cat stops watching and won't nag.")
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        cam_header.addWidget(self.pause_btn)
        cam_layout.addLayout(cam_header)

        # Live camera preview (BGR frames from DetectionWorker via set_camera_frame).
        # Aspect-locked + FIT-scaled so the whole face shows (no crop) and the box keeps a
        # stable height (no stretch while calibrating). Card-colored background so the fit
        # slack is invisible. Until the first frame arrives we show placeholder text.
        self._cam_preview = CameraPreview()
        self._cam_preview.setObjectName("CameraPreview")
        self._cam_preview.setStyleSheet(
            f"#CameraPreview {{ background-color: {COLORS['surface']};"
            f" color: {COLORS['muted']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 8px; }}"
        )
        # Camera-off placeholder: a soft 'no camera' avatar shown whenever the camera
        # is off/paused/offline (instead of a live feed).
        try:
            import os
            from vision.paths import ASSETS_DIR
            _off = QPixmap(os.path.join(ASSETS_DIR, "profilecameraoff.png"))
            if not _off.isNull():
                self._cam_preview.set_off_image(_off)
        except Exception:
            pass
        self._cam_preview.show_off()
        cam_layout.addWidget(self._cam_preview)

        # Three detection chips.
        self.chips = {
            "phone": DetectionChip(
                "Phone", "Lights up red when a phone is detected in view."),
            "gaze": DetectionChip(
                "Gaze", "Lights up red when you look away from the screen."),
            "posture": DetectionChip(
                "Posture", "Lights up red when your posture slumps (lean-in / neck / tilt)."),
        }
        for chip in self.chips.values():
            cam_layout.addWidget(chip)

        # "All local" trust badge: reassure that the webcam is processed locally
        # and nothing is recorded. A lock icon + subtle muted text under the chips.
        local_row = QHBoxLayout()
        local_row.setSpacing(6)
        local_row.addStretch(1)
        local_icon = QLabel()
        local_icon.setPixmap(icons.pixmap("lock", COLORS["muted"], 13))
        self._local_badge = QLabel("100% local · nothing is recorded")
        self._local_badge.setObjectName("Muted")
        self._local_badge.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        local_tip = ("Your webcam is analyzed on this device only — no video is saved, "
                     "uploaded, or shared.")
        local_icon.setToolTip(local_tip)
        self._local_badge.setToolTip(local_tip)
        local_row.addWidget(local_icon)
        local_row.addWidget(self._local_badge)
        local_row.addStretch(1)
        cam_layout.addLayout(local_row)

        # No trailing stretch: the flexible preview above absorbs the card's slack,
        # so the chips stay pinned directly under it (and never get overlapped).
        right_col.addWidget(camera_card, stretch=3)

        # Pet card.
        self.pet_card = pet_card = make_card()
        pet_layout = QVBoxLayout(pet_card)
        pet_layout.setContentsMargins(12, 8, 12, 8)
        pet_title = QLabel("Pet")
        pet_title.setObjectName("CardTitle")
        pet_layout.addWidget(pet_title)
        self.pet = PetWidget()
        self.pet.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pet_layout.addWidget(self.pet, stretch=1)
        # Shown instead of the cat while it's perched on the desktop during a
        # focus session (set_pet_present(False)). Right-click the desktop cat to
        # send it home.
        self._pet_away = QLabel("Your cat is on the desktop.\nRight-click it to send it home.")
        self._pet_away.setObjectName("Muted")
        self._pet_away.setWordWrap(True)
        self._pet_away.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pet_away.setVisible(False)
        pet_layout.addWidget(self._pet_away, stretch=1)
        right_col.addWidget(pet_card, stretch=2)

        middle.addLayout(right_col, stretch=1)
        root.addLayout(middle, stretch=1)

        # The cat's greeting line at startup.
        QTimer.singleShot(600, lambda: self.pet.say("Hi! Let's get focused!"))

    # ------------------------------------------------------------------ #
    # PUBLIC SLOTS FOR THE BACKEND                                        #
    # ------------------------------------------------------------------ #
    # The XP / level / streak / combo top panel was removed; these slots are kept
    # as no-ops so GameController can keep calling them without changes.
    def set_xp(self, current: int, maximum: int) -> None:
        pass

    def set_level(self, level: int) -> None:
        pass

    def set_streak(self, days: int) -> None:
        pass

    def set_combo(self, multiplier: int) -> None:
        pass

    def set_pet_present(self, present: bool) -> None:
        """Show the in-app cat (True) or the 'on the desktop' hint (False).

        Called by the host when the pet moves between the app card and the desktop
        so the cat never appears in two places at once."""
        self.pet.setVisible(bool(present))
        self._pet_away.setVisible(not present)

    def pet_drop_zone_rect(self):
        """Global-screen QRect of the Pet card — the 'drop here to send home' zone.

        Returns None if the card isn't on a real screen yet."""
        try:
            from PyQt6.QtCore import QRect
            tl = self.pet_card.mapToGlobal(self.pet_card.rect().topLeft())
            return QRect(tl, self.pet_card.size())
        except Exception:
            return None

    def set_detection(self, name: str, bad: bool) -> None:
        """Slot: update a detection chip and make the cat react.

        name ∈ {'phone','gaze','posture'}; bad=True — a violation was spotted.
        This is the key input for the backend computer-vision detector.
        """
        chip = self.chips.get(name)
        if chip is None:
            return
        chip.set_status(bad)
        self.detection_changed.emit(name, bad)
        if bad:
            self.pet.react(self._REACTION_MAP.get(name, "distract"))

    def _on_pause_toggled(self, paused: bool) -> None:
        """User paused/resumed detection from the camera card's Pause button."""
        if paused:
            self.pause_btn.setText(" Resume")
            self.pause_btn.setIcon(icons.icon("play", COLORS["muted"], 16))
        else:
            self.pause_btn.setText(" Pause")
            self.pause_btn.setIcon(icons.icon("pause", COLORS["muted"], 16))
        if paused:
            # Immediate calm feedback; the camera worker will also stop reporting.
            for chip in self.chips.values():
                chip.set_status(False)
            self.pet.say("Taking a break — paused.", 2000)
        self.pause_toggled.emit(paused)

    def _on_brown_noise_toggled(self, on: bool) -> None:
        """Main-screen brown-noise toggle -> recolor the icon + tell the backend."""
        on = bool(on)
        color = COLORS["accent"] if on else COLORS["muted"]
        self.brown_noise_btn.setIcon(icons.icon("volume", color, 16))
        self.brown_noise_toggled.emit(on)

    def set_brown_noise(self, on: bool) -> None:
        """Reflect the brown-noise preference on the button without re-emitting."""
        on = bool(on)
        self.brown_noise_btn.blockSignals(True)
        self.brown_noise_btn.setChecked(on)
        self.brown_noise_btn.blockSignals(False)
        color = COLORS["accent"] if on else COLORS["muted"]
        self.brown_noise_btn.setIcon(icons.icon("volume", color, 16))

    def set_reduce_motion(self, on: bool) -> None:
        """Accessibility slot: stop chip blinking + pet frame animation."""
        for chip in self.chips.values():
            chip.set_reduce_motion(on)
        self.pet.set_reduce_motion(on)

    def set_camera_online(self, online: bool) -> None:
        """Slot: toggle the camera indicator (green online / dimmed offline)."""
        color = COLORS["success"] if online else COLORS["muted"]
        self._cam_dot.setPixmap(icons.dot(color, 11))
        if not online:
            # Camera off/paused/dropped — show the 'no camera' avatar placeholder.
            self._cam_preview.show_off()

    def set_camera_frame(self, pixmap: QPixmap) -> None:
        """Slot: show a preview frame from the camera (FIT-scaled, whole frame visible).

        Takes an already-built QPixmap (the controller does BGR->RGB->QImage itself and
        keeps the numpy buffer alive, since QImage doesn't copy foreign memory). The
        aspect-locked CameraPreview rescales it on every resize too."""
        if pixmap is None or pixmap.isNull():
            return
        self._cam_preview.set_frame(pixmap)

    def set_pause_state(self, paused: bool) -> None:
        """Reflect the camera's paused state on the Pause button WITHOUT re-emitting.

        Driven by CameraController.suspended_changed so the camera-card Pause button and
        the Pomodoro pause stay in sync (whichever pauses/resumes the camera, the button
        shows the right label)."""
        paused = bool(paused)
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(paused)
        self.pause_btn.blockSignals(False)
        if paused:
            self.pause_btn.setText(" Resume")
            self.pause_btn.setIcon(icons.icon("play", COLORS["muted"], 16))
        else:
            self.pause_btn.setText(" Pause")
            self.pause_btn.setIcon(icons.icon("pause", COLORS["muted"], 16))
