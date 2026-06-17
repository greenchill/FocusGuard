# -*- coding: utf-8 -*-
"""
main_window.py — MainWindow: the FocusGuard application shell.

Structure:
- A narrow LEFT SIDEBAR with vector icon buttons (icons.py, no emoji) + tooltips:
  Dashboard / Stats / Settings. The buttons switch pages in a QStackedWidget;
  the active button is highlighted and its icon recolored to the accent.
- A QStackedWidget with 3 pages.
- A bottom paw button (and the Ctrl+Alt+P hotkey) send the pet to the desktop or
  bring it home (toggle_pet) — regardless of whether the app is focused.

Pet location coordination:
- The pet lives EITHER in the dashboard's Pet card (home) OR on the desktop as a
  floating window. A focus session sends it to the desktop (game.session_started)
  and ends bring it home (game.session_ended); the desktop cat's right-click
  "Send home" (go_home) also brings it back. The window feeds its frame geometry to
  the pet engine so the cat can perch on the app's top edge.
"""

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QTimer, QRect, QEvent
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QPushButton,
    QLabel, QFrame,
)

from theme import COLORS
from widget_dashboard import DashboardWidget
from widget_stats import StatsWidget
from widget_settings import SettingsWidget
from pet_engine import PetWindow
from camera_controller import CameraController, list_cameras
from game_controller import GameController
import vision.config as vision_config
import firewall
import elevation
import icons
import windows_perch


class MainWindow(QMainWindow):
    """Main window: sidebar + page stack + hotkey integration."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FocusGuard")
        self.resize(940, 624)
        self.setMinimumSize(820, 560)

        # Root container: [sidebar | content].
        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # =============================== SIDEBAR =============================== #
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(64)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(8, 14, 8, 14)
        side_layout.setSpacing(6)

        # =============================== PAGES =============================== #
        self.stack = QStackedWidget()
        self.stack.setObjectName("Stack")

        self.dashboard = DashboardWidget()
        self.stats = StatsWidget()
        self.settings = SettingsWidget()

        self.stack.addWidget(self.dashboard)  # index 0
        self.stack.addWidget(self.stats)      # index 1
        self.stack.addWidget(self.settings)   # index 2

        # Page-switch nav buttons (vector icons, no emoji). _nav_buttons is indexed
        # by PAGE index; the active page's icon recolors to the accent (_switch_page).
        self._nav_buttons = [None, None, None]
        self._nav_icon_names = ["home", "chart", "settings"]

        def _make_nav(icon_name, tip, index):
            btn = QPushButton()
            btn.setObjectName("NavButton")
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(44)
            btn.setIconSize(QSize(22, 22))
            btn.setIcon(icons.icon(icon_name, COLORS["muted"], 22))
            btn.clicked.connect(lambda _c, i=index: self._switch_page(i))
            self._nav_buttons[index] = btn
            return btn

        # Top of the sidebar: Dashboard, Stats, then the pet paw toggle.
        side_layout.addWidget(_make_nav("home", "Dashboard", 0))
        side_layout.addWidget(_make_nav("chart", "Stats", 1))

        self.pet_toggle_btn = QPushButton()
        self.pet_toggle_btn.setObjectName("NavButton")
        self.pet_toggle_btn.setToolTip("Send the cat to the desktop / bring it home (Ctrl+Alt+P)")
        self.pet_toggle_btn.setCheckable(True)
        self.pet_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pet_toggle_btn.setFixedHeight(44)
        self.pet_toggle_btn.setIconSize(QSize(22, 22))
        self.pet_toggle_btn.setIcon(icons.icon("paw", COLORS["muted"], 22))
        self.pet_toggle_btn.clicked.connect(self.toggle_pet)
        side_layout.addWidget(self.pet_toggle_btn)

        side_layout.addStretch(1)

        # Settings lives at the BOTTOM of the sidebar.
        side_layout.addWidget(_make_nav("settings", "Settings", 2))

        # Subtle author credit pinned to the very bottom of the sidebar.
        self._credit = QLabel("David\nKitunov")
        self._credit.setObjectName("Credit")
        self._credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._credit.setToolTip("FocusGuard — by David Kitunov")
        self._credit.setStyleSheet(
            f"#Credit {{ color: {COLORS['muted']}; font-size: 8px; letter-spacing: 0.3px; }}")
        side_layout.addWidget(self._credit)

        root.addWidget(self.sidebar)
        root.addWidget(self.stack, stretch=1)

        # Activate the first page.
        self._nav_buttons[0].setChecked(True)
        self.stack.setCurrentIndex(0)

        # =============================== WIRING =============================== #
        # Floating pet window (16-pose pet_engine engine) — prewarmed hidden at startup
        # (sharing the in-app cat's sprite sheet) so the first drag-out is instant.
        self._floating_pet = None
        self._floating_pet_started = False
        # Where the pet currently lives: False = in the app's Pet card (home),
        # True = perched on the desktop as a floating window.
        self._pet_on_desktop = False

        # =============================== CAMERA =============================== #
        # The controller owns the camera thread (DetectionWorker) and wires it to
        # the dashboard and the floating cat. The constructor does NOT open the
        # camera — only worker.run() does, after start_camera() (see app.py / showEvent).
        self.camera = CameraController(self.dashboard, status_cb=self._log_status)

        # =========================== GAME CONTROLLER =========================== #
        # The gamification brain: owns Session/Combo/PetState/Stats/ChimePlayer and
        # spins the focus loop at 1 Hz. The constructor opens nothing heavy.
        # Shares cfg with the camera so presets/goal write into a single dict.
        self.game = GameController(
            dashboard=self.dashboard,
            timer_widget=self.dashboard.timer,
            stats_widget=self.stats,
            petroom=None,                  # Pet Room was removed; unlocks are a no-op
            camera_controller=self.camera,
            get_floating_pet=lambda: self._floating_pet,
            cfg=self.camera.cfg,
        )
        # Route the timer buttons (Start/Pause/Stop) to the controller.
        self.dashboard.timer.set_controller(self.game)
        # When a focus session starts, the cat hops onto the desktop and leaves the
        # app's Pet card; when the session ends it comes back home.
        self.game.session_started.connect(self._show_pet_on_desktop)
        self.game.session_ended.connect(self._send_pet_home)
        # Editable daily goal on the Stats page -> the game (persists + updates bar).
        self.stats.daily_goal_changed.connect(self.game.set_daily_goal)
        # Grab the cat in the Pet card and drag it straight onto the desktop.
        self.dashboard.pet.dragged_out.connect(self._on_pet_dragged_out)
        self.dashboard.pet.drag_out_move.connect(self._on_pet_drag_move)
        self.dashboard.pet.drag_out_drop.connect(self._on_pet_drag_drop)

        # Settings -> camera + controller (write into the shared cfg and save).
        self.settings.use_camera_toggled.connect(self._on_use_camera_toggled)
        self.settings.calibrate_requested.connect(self._on_calibrate)
        self.settings.camera_changed.connect(self._on_camera_changed)
        self.settings.sensitivity_changed.connect(self._on_sensitivity_changed)
        self.settings.volume_changed.connect(self.game.set_volume)
        # Site blocking (hosts file) — safety-critical, so the backend lives here.
        self.settings.blocking_toggled.connect(self._on_blocking_toggled)
        self.settings.domains_changed.connect(self._on_domains_changed)
        self.settings.restart_admin_requested.connect(self._on_restart_admin)
        # Quick-win UX wiring: Pause/Snooze + Mute + Reduce-motion.
        # The camera-card Pause pauses the WHOLE session when one is live (see handler),
        # so the camera, clock and brown noise all stop together (truthful 'paused').
        self.dashboard.pause_toggled.connect(self._on_dashboard_pause)
        # Keep the camera-card Pause button label in sync whenever the camera is
        # suspended/resumed by EITHER pause source (so they never desync).
        self.camera.suspended_changed.connect(self.dashboard.set_pause_state)
        # Brown-noise toggle from the main screen AND from Settings (kept in sync).
        self.dashboard.brown_noise_toggled.connect(self._on_brown_noise_toggled)
        self.settings.brown_noise_toggled.connect(self._on_brown_noise_toggled)
        self.settings.mute_toggled.connect(self._on_mute_toggled)
        self.settings.reduce_motion_toggled.connect(self._on_reduce_motion_toggled)
        self.settings.light_mode_toggled.connect(self._on_light_mode_toggled)
        # Custom Pomodoro durations -> cfg['session'] (used on the next session start).
        self.settings.focus_minutes_changed.connect(self._on_focus_minutes)
        self.settings.break_minutes_changed.connect(self._on_break_minutes)
        # Dragging the idle ring-dial also sets the focus/break length.
        self.dashboard.timer.duration_dialed.connect(self._on_duration_dialed)
        # Reflect the saved firewall.enabled state in the checkbox without re-emitting.
        self.settings.set_blocking_enabled(
            bool(self.camera.cfg.get("firewall", {}).get("enabled", False)))
        # Reflect the saved volume (cfg stores 0..1; the slider is 0..100).
        self.settings.set_volume(int(round(float(self.camera.cfg.get("sound_volume", 0.35)) * 100)))
        # Reflect saved mute + reduce-motion, and apply reduce-motion immediately.
        muted = not bool(self.camera.cfg.get("sound_enabled", True))
        self.settings.set_muted(muted)
        reduce_motion = bool(self.camera.cfg.get("reduce_motion", False))
        self.settings.set_reduce_motion(reduce_motion)
        self.dashboard.set_reduce_motion(reduce_motion)
        # Reflect saved Light mode (performance) without re-emitting.
        self.settings.set_light_mode(bool(self.camera.cfg.get("light_mode", False)))
        # Reflect the saved 'Use camera' master toggle (pure-timer vs. webcam mode).
        _use_cam = bool(self.camera.cfg.get("use_camera", True))
        self.settings.set_use_camera(_use_cam)
        # The camera-card Pause button is only meaningful with the camera on.
        self.dashboard.pause_btn.setEnabled(_use_cam)
        # Reflect the saved brown-noise preference on both the dashboard + Settings.
        _brown = bool(self.camera.cfg.get("brown_noise", False))
        self.dashboard.set_brown_noise(_brown)
        self.settings.set_brown_noise(_brown)
        # Reflect saved Pomodoro durations + show the focus length on the idle ring.
        _scfg = self.camera.cfg.get("session", {})
        _focus_min = int(_scfg.get("focus_minutes", 50))
        _break_min = int(_scfg.get("break_minutes", 10))
        self.settings.set_session_minutes(_focus_min, _break_min)
        self.dashboard.timer.set_duration(_focus_min * 60)
        self.dashboard.timer.set_durations(_focus_min, _break_min)
        # Fill the camera dropdown with placeholder indices 0..3.
        self._populate_camera_combo()

        self._camera_started = False  # guard against starting the camera thread twice

        # STARTUP CLEANUP: if a previous run crashed while sites were blocked, the
        # hosts file may still carry our block even though blocking is now disabled in
        # config. Clear that stale block so the user is never silently cut off. Guarded
        # by is_admin() so we never touch hosts (or fail) without rights, and only acts
        # when there is actually a block to remove.
        self._cleanup_stale_block()

        # Prewarm the floating desktop pet (hidden) right after launch — it reuses the
        # in-app cat's sprite sheet, so this is cheap and makes the first drag-out smooth
        # instead of stalling on the sprite load.
        QTimer.singleShot(0, self._prewarm_floating_pet)

    # ------------------------------------------------------------------ #
    def _switch_page(self, index: int) -> None:
        """Switch the page; highlight the active sidebar button + recolor its icon."""
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            active = (i == index)
            btn.setChecked(active)
            color = COLORS["accent_soft"] if active else COLORS["muted"]
            btn.setIcon(icons.icon(self._nav_icon_names[i], color, 22))

    # ------------------------------------------------------------------ #
    def toggle_pet(self) -> None:
        """Toggle the pet between the desktop and the app (Ctrl+Alt+P / paw button).

        If the cat is perched on the desktop, send it home into the app card; if it
        is home, send it out onto the desktop. Runs on the GUI thread (hotkey signal
        is queued), so touching widgets here is safe."""
        if self._pet_on_desktop:
            self._send_pet_home()
        else:
            self._show_pet_on_desktop()

    def _prewarm_floating_pet(self) -> None:
        """Create the floating pet (reusing the in-app cat's already-loaded sprite sheet)
        but keep it HIDDEN, so the first drag-out is instant instead of stalling ~2-3s on
        the sprite-sheet processing. Safe to call once, early."""
        if self._floating_pet is not None:
            return
        try:
            from widget_pet import shared_sheet
            sheet = shared_sheet()
        except Exception:
            sheet = None
        self._floating_pet = PetWindow(sprites=sheet)
        self._floating_pet.go_home.connect(self._send_pet_home)
        self._floating_pet.dropped.connect(self._on_pet_dropped)
        self.camera.set_floating_pet(self._floating_pet)

    def _ensure_floating_pet(self) -> None:
        """Show the floating desktop pet (creating it if prewarm didn't), WITHOUT touching
        the in-app card. We keep the reference so distraction reactions can call its
        engine.trigger_react() (see CameraController), and feed it the window geometry so
        it can perch on the app's top edge."""
        if self._floating_pet is None:
            self._prewarm_floating_pet()
        fp = self._floating_pet
        if not self._floating_pet_started:
            fp.start()                              # show() + start the game loop (once)
            self._floating_pet_started = True
        elif not fp.isVisible():
            fp.show()
            fp.raise_()
        # Poll the desktop windows so the cat's perches stay in sync as windows
        # move/open/close/cover each other (we get no events for other apps).
        if not hasattr(self, "_perch_timer"):
            self._perch_timer = QTimer(self)
            self._perch_timer.setInterval(300)
            self._perch_timer.timeout.connect(self._update_pet_platform)
        self._perch_timer.start()
        self._update_pet_platform()

    def _finalize_on_desktop(self) -> None:
        """Mark the pet as living on the desktop and hide the in-app card."""
        self._pet_on_desktop = True
        self.dashboard.set_pet_present(False)       # leave the in-app Pet card
        self._update_pet_toggle_state()

    def _show_pet_on_desktop(self) -> None:
        """Send the pet onto the desktop and hide it from the app card."""
        self._ensure_floating_pet()
        self._finalize_on_desktop()

    def _send_pet_home(self) -> None:
        """Bring the pet back into the app: hide the floating window, show the card."""
        if getattr(self, "_perch_timer", None) is not None:
            self._perch_timer.stop()
        if self._floating_pet is not None:
            self._floating_pet.hide()
        self._pet_on_desktop = False
        self.dashboard.set_pet_present(True)
        self._update_pet_toggle_state()

    def _update_pet_toggle_state(self) -> None:
        """Reflect the pet location on the paw button (checked = on the desktop)."""
        self.pet_toggle_btn.setChecked(self._pet_on_desktop)
        color = COLORS["accent_soft"] if self._pet_on_desktop else COLORS["muted"]
        self.pet_toggle_btn.setIcon(icons.icon("paw", color, 22))

    def _on_pet_dragged_out(self, gpos) -> None:
        """User grabbed the in-app cat and started pulling it onto the desktop.

        We show the floating cat under the cursor and grab it, but we DO NOT hide
        the in-app card yet: the embedded widget still holds the mouse grab that is
        driving this gesture, and hiding it would cancel the grab (so the drag would
        never follow or drop). The card is hidden on drop (_on_pet_drag_drop)."""
        self._ensure_floating_pet()
        fp = self._floating_pet
        if fp is not None:
            try:
                # Place the cat at the cursor, then grab it so it hangs from the mouse.
                fp.engine.fx = float(gpos.x())
                fp.engine.fy = float(gpos.y())
                fp.engine.begin_drag(gpos)
            except Exception:
                pass

    def _on_pet_dropped(self, gpos) -> None:
        """The desktop cat was released: if dropped over the app's Pet card, send it
        home (the mirror of dragging it out of the card onto the desktop)."""
        if not self._pet_on_desktop:
            return
        try:
            if self.isVisible() and not self.isMinimized():
                zone = self.dashboard.pet_drop_zone_rect()
                if zone is not None and zone.contains(gpos):
                    self._send_pet_home()
        except Exception:
            pass

    def _on_pet_drag_move(self, gpos) -> None:
        """The cat is being dragged from the card across the desktop — follow the cursor."""
        if self._floating_pet is not None:
            try:
                self._floating_pet.engine.drag_to(gpos)
            except Exception:
                pass

    def _on_pet_drag_drop(self, gpos) -> None:
        """Dropped on the desktop: release with throw inertia (it falls / lands) and
        NOW finalize the handoff (hide the in-app card)."""
        if self._floating_pet is not None:
            try:
                self._floating_pet.engine.end_drag()
            except Exception:
                pass
        self._finalize_on_desktop()

    # Tiny lift so the cat's feet rest right ON the (accurate DWM) window top edge.
    # Was 8, which made it float above the edge now that we use the real visible
    # frame bounds; 2 keeps the paws on the line without sinking in.
    _LEDGE_LIFT = 2

    def _update_pet_platform(self) -> None:
        """Refresh the cat's perch ledges from ALL visible desktop windows.

        Uses windows_perch (Win32) to find the VISIBLE top-edge segments of every
        real app window respecting Z-order — so the cat can jump onto any window's
        title bar and stays there while it fits, but never perches on an edge hidden
        behind another window (and falls when its perch is covered / moved / closed /
        minimized). The cat's own windows are excluded. Only runs while the cat is
        out on the desktop; otherwise the engine just has the desktop floor."""
        fp = self._floating_pet
        if fp is None or not fp.isVisible():
            return
        try:
            dpr = float(fp.devicePixelRatioF()) or 1.0
        except Exception:
            dpr = 1.0
        exclude = []
        for wdw in (fp, getattr(fp, "_bubble", None), getattr(fp, "_time_pill", None)):
            try:
                if wdw is not None:
                    exclude.append(int(wdw.winId()))
            except Exception:
                pass
        try:
            ledges = windows_perch.visible_window_ledges(
                exclude_hwnds=exclude, dpr=dpr, lift=self._LEDGE_LIFT)
            fp.engine.set_platforms(ledges)
        except Exception:
            pass

    def moveEvent(self, event) -> None:
        self._update_pet_platform()
        super().moveEvent(event)

    def resizeEvent(self, event) -> None:
        self._update_pet_platform()
        super().resizeEvent(event)

    def changeEvent(self, event) -> None:
        # Re-evaluate the perch when the app is activated/deactivated or minimized/
        # restored, so the cat never keeps standing on a hidden/occluded window.
        if event.type() in (QEvent.Type.ActivationChange,
                             QEvent.Type.WindowStateChange):
            self._update_pet_platform()
        super().changeEvent(event)

    # ------------------------------------------------------------------ #
    def _populate_camera_combo(self) -> None:
        """Fill the camera dropdown with the REAL cameras detected on the system
        (DirectShow names via pygrabber, with an OpenCV-probe fallback)."""
        combo = self.settings.camera_combo
        combo.blockSignals(True)                    # don't fire camera_changed while filling
        combo.clear()
        try:
            cams = list_cameras()
        except Exception:
            cams = [(0, "Camera #0")]
        for index, name in cams:
            combo.addItem(f"{name}  (#{index})", index)   # userData = camera index
        # Select the camera index saved in cfg, if it is in the list.
        want = int(self.camera.cfg.get("camera_index", 0))
        sel = next((row for row, (idx, _n) in enumerate(cams) if idx == want), 0)
        combo.setCurrentIndex(sel)
        combo.blockSignals(False)

    def _on_calibrate(self) -> None:
        """Calibration button -> ask the worker to recompute the normal + cat line."""
        self.camera.start_calibration()
        self.dashboard.pet.say("Remembering your normal...", 2500)

    def _on_mute_toggled(self, muted: bool) -> None:
        """Mute/unmute all sounds and persist it (cfg['sound_enabled'])."""
        enabled = not bool(muted)
        self.camera.cfg["sound_enabled"] = enabled
        try:
            if getattr(self.game, "chime", None) is not None:
                self.game.chime.enabled = enabled
        except Exception:
            pass
        vision_config.save_config(self.camera.cfg)

    def _on_reduce_motion_toggled(self, on: bool) -> None:
        """Reduce-motion: steady chips + still cat; persist (cfg['reduce_motion'])."""
        on = bool(on)
        self.camera.cfg["reduce_motion"] = on
        self.dashboard.set_reduce_motion(on)
        vision_config.save_config(self.camera.cfg)

    def _on_light_mode_toggled(self, on: bool) -> None:
        """Light mode: lower the analysis cadence to save CPU/battery; persist + restart.

        ON  -> process every 2nd frame; phone every 4th; posture every 8th.
        OFF -> restore the defaults (1 / 2 / 4).
        The worker reads the cadence when it is created, so we restart a running
        camera to pick up the new values."""
        on = bool(on)
        cfg = self.camera.cfg
        if on:
            cfg["process_every_n_frames"] = 2
            cfg.setdefault("phone", {})["every_n_frames"] = 4
            cfg.setdefault("posture", {})["every_n_frames"] = 8
        else:
            cfg["process_every_n_frames"] = 1
            cfg.setdefault("phone", {})["every_n_frames"] = 2
            cfg.setdefault("posture", {})["every_n_frames"] = 4
        cfg["light_mode"] = on
        vision_config.save_config(cfg)
        # If the camera is running, restart it so the worker picks up the new cadence.
        if self.camera is not None and getattr(self.camera, "worker", None) is not None:
            self.camera.restart_with_camera(int(cfg.get("camera_index", 0)))

    def _on_focus_minutes(self, minutes: int) -> None:
        """Custom Pomodoro focus length: persist + reflect on the idle ring."""
        minutes = max(1, int(minutes))
        self.camera.cfg.setdefault("session", {})["focus_minutes"] = minutes
        vision_config.save_config(self.camera.cfg)
        # Update the idle ring only when no session is running (the live ring is
        # driven by the controller during a session).
        if not self.game.is_session_active():
            self.dashboard.timer.set_duration(minutes * 60)

    def _on_break_minutes(self, minutes: int) -> None:
        """Custom Pomodoro break length: persist for the next break phase."""
        minutes = max(1, int(minutes))
        self.camera.cfg.setdefault("session", {})["break_minutes"] = minutes
        vision_config.save_config(self.camera.cfg)
        # Keep the idle ring-dial's break value in sync with the spinbox.
        scfg = self.camera.cfg.get("session", {})
        self.dashboard.timer.set_durations(
            int(scfg.get("focus_minutes", 50)), minutes)

    def _on_duration_dialed(self, target: str, minutes: int) -> None:
        """The idle ring-dial was dragged: persist focus/break + sync the spinbox."""
        minutes = max(1, int(minutes))
        scfg = self.camera.cfg.setdefault("session", {})
        if target == "focus":
            scfg["focus_minutes"] = minutes
            spin = self.settings.focus_spin
        else:
            scfg["break_minutes"] = minutes
            spin = self.settings.break_spin
        spin.blockSignals(True)
        spin.setValue(minutes)
        spin.blockSignals(False)
        # Debounce the disk write: a drag emits many steps (like the domains field),
        # so persist once ~400 ms after the user stops dragging.
        if not hasattr(self, "_dial_save_timer"):
            self._dial_save_timer = QTimer(self)
            self._dial_save_timer.setSingleShot(True)
            self._dial_save_timer.setInterval(400)
            self._dial_save_timer.timeout.connect(
                lambda: vision_config.save_config(self.camera.cfg))
        self._dial_save_timer.start()

    def _on_sensitivity_changed(self, preset: str) -> None:
        """Sensitivity change: apply the preset to the camera and save cfg.

        The camera and controller share one cfg, so we write the thresholds through
        the camera (which also restarts the worker if needed), and the controller persists."""
        self.camera.apply_sensitivity_preset(preset)
        self.game.save_config()

    def _on_camera_changed(self, text: str) -> None:
        """Camera change in the list (currentTextChanged gives text) -> restart the worker.

        We take the index from the current item's userData (more reliable than parsing text).
        Only REOPEN the device when the camera is already running; otherwise just persist
        the chosen index — opening it here would bypass the first-run privacy consent gate
        (and pointlessly open a device the user has off in Settings)."""
        combo = self.settings.camera_combo
        index = combo.currentData()
        if index is None:
            index = combo.currentIndex()
        index = int(index)
        if getattr(self.camera, "worker", None) is not None:
            self.camera.restart_with_camera(index)
        else:
            # Not running yet (consent pending / camera off): just remember the choice.
            self.camera.cfg["camera_index"] = index
            vision_config.save_config(self.camera.cfg)

    def _log_status(self, message: str) -> None:
        """Single point for emitting camera status (for now — to the console)."""
        print(f"[FocusGuard] {message}")

    # ------------------------------------------------------------------ #
    #  Site blocking (hosts file) — wiring                               #
    # ------------------------------------------------------------------ #
    def _firewall_cfg(self) -> dict:
        """The shared cfg's firewall section (created if missing)."""
        return self.camera.cfg.setdefault("firewall", {})

    def _on_blocking_toggled(self, on: bool) -> None:
        """Hardcore site block checkbox: persist the choice and apply it safely.

        Turning ON without admin cannot edit hosts, so we surface a clear hint instead
        of silently doing nothing. Turning OFF while a block is live (and we are admin)
        clears it immediately."""
        self._firewall_cfg()["enabled"] = bool(on)
        try:
            vision_config.save_config(self.camera.cfg)
        except Exception:
            pass

        if on:
            if not firewall.is_admin():
                msg = "Site block needs admin — click Restart as administrator"
                self._log_status(msg)
                try:
                    self.dashboard.pet.say(msg, 4000)
                except Exception:
                    pass
            else:
                # If a focus session is already running, apply the block right now.
                if getattr(self, "game", None) is not None and \
                        getattr(self.game, "_running", False) and self.game.session is not None \
                        and self.game.session.is_focus():
                    self._safe_block()
        else:
            # Turning the feature off should never leave the user's sites blocked.
            try:
                if firewall.is_blocked() and firewall.is_admin():
                    firewall.unblock()
            except Exception:
                pass

    def _on_domains_changed(self, text: str) -> None:
        """Domains field edited: save the list, and re-apply the block if focusing now."""
        domains = [line.strip() for line in text.splitlines() if line.strip()]
        try:
            firewall.save_domains(domains)
        except Exception:
            pass
        # Re-apply only when a block is meaningful right now (focus + enabled + admin).
        try:
            if self._firewall_cfg().get("enabled", False) and firewall.is_admin() \
                    and getattr(self, "game", None) is not None \
                    and getattr(self.game, "_running", False) \
                    and self.game.session is not None and self.game.session.is_focus():
                self._safe_block()
        except Exception:
            pass

    def _on_restart_admin(self) -> None:
        """Relaunch elevated via UAC; if a relaunch was triggered, quit this instance."""
        try:
            triggered = elevation.relaunch_as_admin()
        except Exception as exc:
            self._log_status(f"Could not relaunch as administrator: {exc}")
            return
        if triggered:
            # The elevated copy is launching; close this one so we don't run twice.
            self.close()
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def _safe_block(self) -> None:
        """Apply firewall.block() defensively, surfacing any failure as a status line."""
        try:
            ok, msg = firewall.block()
            if not ok:
                self._log_status(f"Site block: {msg}")
        except Exception as exc:
            self._log_status(f"Site block error: {exc}")

    def _cleanup_stale_block(self) -> None:
        """On startup, drop a leftover block if the feature is now disabled (idempotent)."""
        try:
            if not self._firewall_cfg().get("enabled", False) \
                    and firewall.is_blocked() and firewall.is_admin():
                firewall.unblock()
        except Exception:
            pass

    def _unblock_on_exit(self) -> None:
        """Final safety: never leave the user's sites blocked after the app closes.

        Idempotent — safe to call from both closeEvent and app.aboutToQuit."""
        try:
            if firewall.is_blocked() and firewall.is_admin():
                firewall.unblock()
        except Exception:
            pass

    def start_camera(self) -> None:
        """Start the camera thread after a first-run privacy consent.

        Called from app.py AFTER window.show(). The webcam is analyzed 100%
        locally; we still ask for explicit consent before turning it on.

        Honors the 'Use camera' master toggle: in pure-timer mode the camera is never
        opened (no LED, no detection) and the app runs as a plain Pomodoro."""
        if self._camera_started:
            return
        if not bool(self.camera.cfg.get("use_camera", True)):
            # Pure-timer mode: don't open the camera; reflect 'off' on the card.
            self.dashboard.set_camera_online(False)
            self.game.set_camera_enabled(False)
            self.dashboard.pet.say("Camera off — pure Pomodoro timer.", 3000)
            return
        if not self._ensure_camera_consent():
            # Declined: run without the camera; re-ask on the next launch.
            self.dashboard.set_camera_online(False)
            self.dashboard.pet.say("Camera off — enable it anytime.", 3500)
            return
        # One-time welcome onboarding: after consent is granted, before the camera
        # starts. Gated by cfg['welcome_shown'] so it never blocks subsequent runs.
        if not bool(self.camera.cfg.get("welcome_shown", False)):
            self._show_welcome()
            self.camera.cfg["welcome_shown"] = True
            vision_config.save_config(self.camera.cfg)
        self._camera_started = True
        self.camera.start()

    def _ensure_camera_consent(self) -> bool:
        """First-run privacy consent gate (once). Returns True if the camera may open."""
        if bool(self.camera.cfg.get("consent_shown", False)):
            return True
        if self._ask_camera_consent():
            self.camera.cfg["consent_shown"] = True
            vision_config.save_config(self.camera.cfg)
            return True
        return False

    def _on_dashboard_pause(self, paused: bool) -> None:
        """Camera-card Pause button.

        During a live session it pauses/resumes the WHOLE Pomodoro (game.pause_session /
        resume_session — which already stop the clock, release the camera, and pause the
        brown noise), so the visible 'paused' state is truthful and the camera goes dark.
        With no session running it's a plain camera snooze (release/reopen the device).
        Routing both controls through one authoritative state keeps them from desyncing."""
        paused = bool(paused)
        if self.game.is_session_active():
            if paused and not self.game.is_paused():
                self.game.pause_session()
            elif not paused and self.game.is_paused():
                self.game.resume_session()
        else:
            self.camera.set_detection_paused(paused)

    def _on_brown_noise_toggled(self, on: bool) -> None:
        """Brown-noise toggle (from the dashboard OR Settings) — apply + keep both in sync."""
        on = bool(on)
        self.game.set_brown_noise_enabled(on)   # persists cfg + starts/stops playback
        self.dashboard.set_brown_noise(on)
        self.settings.set_brown_noise(on)

    def _on_use_camera_toggled(self, on: bool) -> None:
        """Settings 'Use camera' master toggle: switch between webcam and pure-timer mode."""
        on = bool(on)
        self.camera.cfg["use_camera"] = on
        vision_config.save_config(self.camera.cfg)
        self.game.set_camera_enabled(on)
        self.dashboard.pause_btn.setEnabled(on)   # camera-card pause is camera-only
        if not on:
            # Pure-timer mode: release the device + reflect 'off'.
            self.camera.set_camera_enabled(False)
            self.dashboard.pet.say("Camera off — pure Pomodoro timer.", 3000)
            return
        # Turning the camera on: honor the one-time consent, then open the device.
        if not self._ensure_camera_consent():
            # Declined: revert to timer mode and the checkbox.
            self.camera.cfg["use_camera"] = False
            vision_config.save_config(self.camera.cfg)
            self.game.set_camera_enabled(False)
            self.settings.set_use_camera(False)
            self.dashboard.pet.say("Camera off — enable it anytime.", 3000)
            return
        self._camera_started = True
        self.camera.set_camera_enabled(True)    # sets the master flag + opens the device
        # If a session is live, immediately reflect its focus/break detection state on the
        # freshly opened worker (otherwise chips/reactions stay stale until the next phase).
        self.game.sync_detection_to_session()

    def _style_dialog(self, box) -> None:
        """Force readable pastel-theme colors on a QMessageBox (the default dark/grey
        rendering on some Windows themes is low-contrast)."""
        box.setStyleSheet(
            f"QMessageBox {{ background-color: {COLORS['surface']}; }}"
            f"QMessageBox QLabel {{ color: {COLORS['text']}; font-size: 13px;"
            f" background: transparent; }}"
            f"QPushButton {{ background-color: {COLORS['elevated']}; color: {COLORS['text']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 8px;"
            f" padding: 6px 16px; min-width: 84px; }}"
            f"QPushButton:hover {{ background-color: {COLORS['accent_soft']}; }}"
            f"QPushButton:default {{ background-color: {COLORS['accent']};"
            f" color: {COLORS['ink']}; border: none; }}"
        )

    def _show_welcome(self) -> None:
        """First-run welcome: a brief, skippable explainer (single OK button).

        Testable + gated behind cfg['welcome_shown'] so tests never see a modal."""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("Welcome to FocusGuard")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Here's how it works:")
        box.setInformativeText(
            "• Your cat reacts when you grab your phone, look away, or slouch.\n"
            "• Start a focus session and the cat keeps you company while you work.\n"
            "• Optional brown noise can loop while you focus (toggle on the main screen).\n"
            "• Pause anytime — pausing turns the camera off too.\n"
            "• We'll calibrate now — sit normally and look at the screen for a "
            "few seconds.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._style_dialog(box)
        box.exec()

    def _ask_camera_consent(self) -> bool:
        """First-run privacy consent. Returns True if the user enables the camera."""
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("Camera & privacy")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("FocusGuard uses your webcam to help you focus.")
        box.setInformativeText(
            "Everything runs 100% locally on your computer:\n"
            "• video is analyzed in real time and never saved;\n"
            "• nothing is recorded, uploaded, or shared;\n"
            "• you can pause detection anytime, or turn the camera off.\n\n"
            "Enable the camera now?")
        enable_btn = box.addButton("Enable camera", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(enable_btn)
        self._style_dialog(box)
        box.exec()
        return box.clickedButton() is enable_btn

    # ------------------------------------------------------------------ #
    def connect_hotkey(self, hotkey_manager) -> None:
        """Connect the hotkey manager: toggled -> toggle_pet.

        Called from app.py after the window is created.
        """
        hotkey_manager.toggled.connect(self.toggle_pet)

    # ------------------------------------------------------------------ #
    def shutdown_camera(self) -> None:
        """Stop the camera: stop()+wait() releases the device (no zombie thread).

        Idempotent: can be called from both closeEvent and app.aboutToQuit."""
        if self.camera is not None:
            self.camera.shutdown()

    def shutdown_game(self) -> None:
        """Stop the 1 Hz clock and save the pet/stats.

        Idempotent: called from both closeEvent and app.aboutToQuit."""
        if getattr(self, "game", None) is not None:
            self.game.shutdown()

    def _confirm_quit(self) -> bool:
        """Ask the user to confirm quitting while a session is live. Returns True to quit.

        Kept tiny + separate so tests can monkeypatch it (no modal in tests)."""
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Quit FocusGuard?",
            "A focus session is running — quit anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        """Window close: save the game, release the camera, and shut down the floating cat.

        If a focus/break session is running, confirm first. Declining ignores the
        event and runs NO cleanup (the session keeps going). The always-unblock-on-exit
        guarantee still holds for the actual-quit path via app.aboutToQuit."""
        if getattr(self, "game", None) is not None and self.game.is_session_active():
            if not self._confirm_quit():
                event.ignore()
                return
        # ALWAYS clear any live hosts block first — the user must never be left with
        # their sites blocked after exit (also done in app.aboutToQuit; idempotent).
        self._unblock_on_exit()
        self.shutdown_game()
        self.shutdown_camera()
        if self._floating_pet is not None:
            try:
                self._floating_pet.engine.stop()
                self._floating_pet.close()
            except Exception:
                pass
        super().closeEvent(event)
