# -*- coding: utf-8 -*-
"""
camera_controller.py — the link between the camera (DetectionWorker) and the GUI.

Why a separate module: to avoid bloating MainWindow and to keep all the "dirty"
logic of the camera thread lifecycle, reaction debouncing and sensitivity presets
in one place. MainWindow merely creates the controller, hands it references to the
dashboard and (optionally) the floating cat, and calls start()/shutdown().

Key guarantees (they repeat the battle scars from FocusGuard):
- The constructor does NOT open the camera: DetectionWorker touches the device only
  inside run(). That is why the controller can be built without a live camera/display.
- On stop we call worker.stop() THEN worker.wait(...): otherwise a zombie thread
  holds the camera and the next start (or camera switch) won't open the device.
- frame_ready yields BGR numpy; QImage does NOT copy foreign memory, so we keep the
  frame buffer alive on self (self._frame_buf) until the QPixmap is built.
- Pet reactions are debounced via vision.detection.Trigger (grace + cooldown),
  so the cat doesn't twitch on every frame.

All slots run on the GUI thread: by default QThread signals arrive via a queue
(auto/queued connection) on the thread that owns the controller.
"""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

import vision.config as vision_config
from vision.detection import Trigger
from vision.game import apply_preset
from detection_worker import DetectionWorker

# How many seconds without fresh metrics before we consider focus stale (camera froze/died).
# Comfortably above the frame period, but conceptually below the game tick.
_FOCUS_STALE_SECONDS = 3.0


# --------------------------------------------------------------------------- #
#  Sensitivity presets                                                         #
# --------------------------------------------------------------------------- #
def apply_sensitivity(cfg: dict, preset: str) -> dict:
    """Apply the relaxed|normal|strict preset to the cfg thresholds (mutates and returns cfg).

    The idea: relaxed = everything "softer" (harder to catch a violation), strict = "harsher".
    normal resets the values to the vision.config.DEFAULTS, so repeated toggles don't
    accumulate multipliers. Values are clamped to sensible bounds.

    Multipliers:
      relaxed: gaze/tilt angles *1.3, eye_down_delta *1.3, neck/shoulder tilt *1.3,
               lean_in_ratio *0.85 (you have to lean in closer for it to trigger),
               phone score_threshold *1.15 (needs higher confidence).
      strict:  the same axes, but "tighter": angles/deltas/neck *0.75,
               lean_in_ratio *1.1, phone score_threshold *0.85.
    """
    defaults = vision_config.DEFAULTS

    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    # Multipliers per preset. normal => 1.0 everywhere (values return to defaults).
    if preset == "relaxed":
        m_angle, m_eye, m_neck, m_lean, m_phone = 1.3, 1.3, 1.3, 0.85, 1.15
    elif preset == "strict":
        m_angle, m_eye, m_neck, m_lean, m_phone = 0.75, 0.75, 0.75, 1.1, 0.85
    else:  # normal or unknown — defaults
        m_angle = m_eye = m_neck = m_lean = m_phone = 1.0

    cfg["sensitivity"] = preset if preset in ("relaxed", "normal", "strict") else "normal"

    gaze = cfg.setdefault("gaze", {})
    g_def = defaults["gaze"]
    gaze["yaw_deg"] = clamp(g_def["yaw_deg"] * m_angle, 8.0, 45.0)
    gaze["down_deg"] = clamp(g_def["down_deg"] * m_angle, 4.0, 30.0)
    gaze["up_deg"] = clamp(g_def["up_deg"] * m_angle, 6.0, 35.0)
    gaze["eye_down_delta"] = clamp(g_def["eye_down_delta"] * m_eye, 0.08, 0.5)

    posture = cfg.setdefault("posture", {})
    p_def = defaults["posture"]
    posture["neck_delta_deg"] = clamp(p_def["neck_delta_deg"] * m_neck, 5.0, 30.0)
    posture["shoulder_tilt_deg"] = clamp(p_def["shoulder_tilt_deg"] * m_neck, 6.0, 35.0)
    posture["lean_in_ratio"] = clamp(p_def["lean_in_ratio"] * m_lean, 0.4, 0.95)

    phone = cfg.setdefault("phone", {})
    ph_def = defaults["phone"]
    phone["score_threshold"] = clamp(ph_def["score_threshold"] * m_phone, 0.1, 0.9)

    return cfg


# --------------------------------------------------------------------------- #
#  Camera controller                                                           #
# --------------------------------------------------------------------------- #
def list_cameras():
    """Return a list of (index, name) for the cameras available on this machine.

    Prefers real DirectShow device names via pygrabber (instant, doesn't open the
    camera, no LED flicker). Falls back to probing a few OpenCV indices, then to a
    single default so the dropdown is never empty."""
    # 1) DirectShow names (Windows) — fast and human-readable.
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        if names:
            return [(i, n) for i, n in enumerate(names)]
    except Exception:
        pass
    # 2) Probe OpenCV indices (opens each briefly to test availability).
    try:
        import cv2
        api = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0
        found = []
        for i in range(5):
            cap = cv2.VideoCapture(i, api)
            if cap.isOpened():
                found.append((i, f"Camera #{i}"))
            cap.release()
        if found:
            return found
    except Exception:
        pass
    # 3) Last resort.
    return [(0, "Camera #0")]


class CameraController(QObject):
    """Owns DetectionWorker and wires its signals to the dashboard and the cat.

    Usage:
        ctrl = CameraController(dashboard, status_cb=print)
        ctrl.set_floating_pet(pet_window)   # optional, can be swapped at runtime
        ctrl.start()                        # starts the camera thread
        ...
        ctrl.shutdown()                     # stop + wait (releases the camera)
    """

    # Distraction signal to the outside (to GameController): kind ∈ {'phone','gaze','posture'}.
    # Emitted at the same spot where the pet reactions fire (after the Trigger debounce).
    distraction_alert = pyqtSignal(str)
    # Fires whenever the camera's temporary-suspended state flips (pause/resume from ANY
    # source). The host mirrors it onto the dashboard Pause button so the two pause
    # controls (camera-card snooze + Pomodoro pause) can never desync.
    suspended_changed = pyqtSignal(bool)

    def __init__(self, dashboard, status_cb=None, parent=None):
        super().__init__(parent)
        self.dashboard = dashboard
        # Callback for the status line/console (e.g. print or label.setText).
        self._status_cb = status_cb or (lambda _msg: None)

        # Load the config RIGHT AWAY, but DON'T touch the camera (worker.run() does that).
        self.cfg = vision_config.load_config()

        self.worker: DetectionWorker | None = None
        # Reference to the floating cat (pet_engine.PetWindow) — may be absent.
        self._floating_pet = None

        # Whether the person is focused RIGHT NOW per the latest metrics:
        # face in frame, no phone/gaze-away/bad posture and not calibrating.
        # GameController reads this every game tick to award XP/combo.
        self.focused = False

        # Whether game detection is enabled (GameController mutes it during a break).
        # It does NOT affect the metrics/chips themselves, but it SILENCES the game
        # loop: both is_focused() for awards and the reaction/distraction_alert path
        # (otherwise during a break a real phone/gaze would still reset the combo and
        # hurt the pet).
        self._detection_enabled = True

        # MASTER switch: when False the webcam is never opened (pure-timer mode). Set
        # from cfg['use_camera'] / the Settings "Use camera" toggle. start() is a no-op
        # while this is False, and turning it off releases the device.
        self._camera_enabled = bool(self.cfg.get("use_camera", True))
        # TEMPORARY off: the camera is released while the Pomodoro is paused/snoozed and
        # re-opened on resume. Distinct from _camera_enabled (a setting) so resume()
        # never re-opens a camera the user turned off in Settings.
        self._suspended = False

        # Timestamp of the latest metrics (wall-clock). Needed to guard against "stuck"
        # focus: if the camera thread dies/hangs after a focused frame, on_metrics
        # stops arriving while self.focused stays True. is_focused() treats focus as
        # stale if no fresh metrics have arrived for longer than _FOCUS_STALE_SECONDS.
        self._last_metrics_wall = 0.0

        # Buffer of the latest frame: kept alive until the QImage/QPixmap is built
        # (QImage(bytes,...) does NOT copy foreign memory — otherwise garbage/crash).
        self._frame_buf = None

        # Reaction-debounce triggers (one per kind). Rebuilt when the thresholds/
        # sensitivity change, so grace is taken from the fresh cfg.
        self._triggers = {}
        self._rebuild_triggers()

        self._camera_marked_online = False

    # ------------------------------------------------------------------ #
    #  Triggers                                                          #
    # ------------------------------------------------------------------ #
    def _rebuild_triggers(self) -> None:
        """Rebuild the Triggers from the current cfg (grace from sections, cooldown shared)."""
        cooldown = float(self.cfg.get("alert_cooldown_seconds", 4.0))
        phone_grace = float(self.cfg.get("phone", {}).get("grace_seconds", 1.0))
        gaze_grace = float(self.cfg.get("gaze", {}).get("grace_seconds", 3.0))
        posture_grace = float(self.cfg.get("posture", {}).get("grace_seconds", 6.0))
        self._triggers = {
            "phone": Trigger(phone_grace, cooldown),
            "gaze": Trigger(gaze_grace, cooldown),
            "posture": Trigger(posture_grace, cooldown),
        }

    # ------------------------------------------------------------------ #
    #  External references                                               #
    # ------------------------------------------------------------------ #
    def set_floating_pet(self, pet_window) -> None:
        """Remember the floating cat (or None). Reactions go to its engine."""
        self._floating_pet = pet_window

    def _route_say(self, text, msec=2500) -> None:
        """Say a phrase via the floating desktop cat if it's out, else the card cat."""
        try:
            fp = self._floating_pet
            if fp is not None and getattr(fp, "isVisible", lambda: False)():
                fp.say(text, msec)
                return
        except Exception:
            pass
        try:
            self.dashboard.pet.say(text, msec)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Camera thread lifecycle                                           #
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Create (if needed) and start the DetectionWorker.

        The camera is opened only inside worker.run() — calling this is safe even
        when there is no device (an error then arrives -> camera offline).

        No-op when the camera is disabled in Settings (_camera_enabled) or temporarily
        suspended (a paused/snoozed session), so those callers never reopen the device."""
        if not self._camera_enabled or self._suspended:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = DetectionWorker(self.cfg)
        self._connect_worker(self.worker)
        self._camera_marked_online = False
        self.worker.start()  # QThread.start -> run() on a separate thread

    # ------------------------------------------------------------------ #
    #  Master switch + temporary suspend (pause)                          #
    # ------------------------------------------------------------------ #
    def is_camera_enabled(self) -> bool:
        """Whether the webcam focus-tracking is enabled at all (the Settings toggle)."""
        return self._camera_enabled

    def set_camera_enabled(self, on: bool) -> None:
        """Master on/off for the webcam (Settings 'Use camera').

        Off: release the device immediately and mark the camera offline (pure-timer
        Pomodoro). On: clear the suspend latch and open the device right away."""
        on = bool(on)
        self.cfg["use_camera"] = on
        if on == self._camera_enabled:
            # Already in the requested master state; make the live device match. If a
            # pause left us suspended, clear it so 'on' actually reopens the device
            # (otherwise start() would refuse and the camera would stay stuck off).
            if on:
                self._set_suspended(False)
                self.start()
            return
        self._camera_enabled = on
        if on:
            self._set_suspended(False)
            self.start()
        else:
            self.shutdown()
            self.focused = False
            try:
                self.dashboard.set_camera_online(False)
            except Exception:
                pass
            self._camera_marked_online = False

    def _set_suspended(self, value: bool) -> None:
        """Set the suspended latch and notify listeners only on a real change."""
        value = bool(value)
        if value == self._suspended:
            return
        self._suspended = value
        try:
            self.suspended_changed.emit(value)
        except Exception:
            pass

    def suspend(self) -> None:
        """Temporarily release the camera (pause/snooze): the device/LED turns off.

        resume() re-opens it. A no-op when the camera is disabled in Settings."""
        if not self._camera_enabled or self._suspended:
            return
        self._set_suspended(True)
        self.shutdown()                 # stop()+wait() -> releases the device
        self.focused = False
        try:
            self.dashboard.set_camera_online(False)
        except Exception:
            pass
        self._camera_marked_online = False

    def resume(self) -> None:
        """Re-open the camera after a suspend() (session resumed/stopped).

        Idempotent and safe to call even if we were never suspended. Never reopens a
        camera the user disabled in Settings."""
        if not self._suspended:
            return
        self._set_suspended(False)
        if self._camera_enabled:
            self.start()

    def _connect_worker(self, worker: DetectionWorker) -> None:
        """Connect the worker's signals to the controller's slots (queued — other thread)."""
        worker.metrics.connect(self.on_metrics)
        worker.frame_ready.connect(self.on_frame)
        worker.status_text.connect(self.on_status)
        worker.error.connect(self.on_error)
        worker.calibrated.connect(self.on_calibrated)

    def shutdown(self) -> None:
        """Stop the camera: stop() THEN wait() until run() actually exits —
        otherwise a zombie thread holds the device (this race really exists in FocusGuard).

        stop() is a one-way latch, but run() checks it only at checkpoints BETWEEN
        blocking calls: FocusDetector(cfg) (downloading/loading the MediaPipe/
        EfficientDet models) and cv2.VideoCapture(..., CAP_DSHOW) on Windows easily
        take longer than 2 s. So we must NOT drop the worker reference on timeout:
        the loop waits in 2 s windows until wait() returns True. Since the latch is
        one-way, run() is guaranteed to return after the current block."""
        if self.worker is None:
            return
        worker = self.worker
        try:
            worker.stop()           # one-way latch — honored even while loading
            # wait(ms) -> True when run() exits; False on timeout. Dropping the
            # reference to a still-alive thread = zombie camera, so we spin until True.
            while not worker.wait(2000):
                self._status_cb("[camera] waiting for the camera thread to finish "
                                "(loading models/opening device)...")
        except RuntimeError:
            # The worker may have already been deleted by Qt — ignore.
            pass
        self.worker = None

    def restart_with_camera(self, index: int) -> None:
        """Switch camera: stop+wait for the old worker, new cfg, fresh start."""
        self.shutdown()
        self.cfg["camera_index"] = int(index)
        self._camera_marked_online = False
        self.start()

    def apply_sensitivity_preset(self, preset: str) -> None:
        """Apply the preset to cfg, rebuild the triggers and restart the worker.

        The thresholds are written by vision.game.apply_preset (the single source of
        truth for GUI and core): it puts ready-made gaze/posture/phone values +
        grace_seconds for the chosen preset into cfg. If the name is unknown — we
        keep the old values.
        """
        apply_preset(self.cfg, preset)
        self._rebuild_triggers()
        # The detector threshold is read when FocusDetector is created -> needs a restart.
        if self.worker is not None:
            self.shutdown()
            self.start()

    # ------------------------------------------------------------------ #
    #  Game interface (for GameController)                               #
    # ------------------------------------------------------------------ #
    def is_focused(self) -> bool:
        """Whether the person is focused now (per the latest metrics) AND game
        detection is enabled. False if the camera is offline/calibrating/on a break
        OR the metrics have "gone stale" (camera thread hung/died — otherwise we'd
        award endless focus off a dead camera)."""
        if not (self.focused and self._detection_enabled):
            return False
        import time as _time
        if (_time.time() - self._last_metrics_wall) > _FOCUS_STALE_SECONDS:
            return False
        return True

    def set_detection_enabled(self, enabled: bool) -> None:
        """Enable/disable counting focus for awards (GameController mutes it on a break).

        The camera thread and chips keep working — only the contribution to
        is_focused() is silenced, so XP/combo don't pile up during a break."""
        self._detection_enabled = bool(enabled)
        if not enabled:
            self.focused = False

    def set_detection_paused(self, paused: bool) -> None:
        """Camera-card Pause/Snooze: physically turn the camera OFF while paused (the
        webcam LED goes dark, chips go calm, nothing is flagged) and re-open on resume.

        Shares the single _suspended latch with the Pomodoro pause and broadcasts every
        change via suspended_changed, so the host keeps the dashboard Pause button in
        sync and the two controls can never desync (camera stuck on/off)."""
        if paused:
            self.suspend()
        else:
            self.resume()

    def start_calibration(self) -> None:
        """Ask the worker to recalibrate (if it is alive)."""
        if self.worker is not None:
            self.worker.start_calibration()

    # ------------------------------------------------------------------ #
    #  Worker signal slots (GUI thread)                                  #
    # ------------------------------------------------------------------ #
    def on_metrics(self, m: dict) -> None:
        """Per-frame metrics -> dashboard chips + (debounced) pet reactions."""
        # Ignore metrics still queued from before a pause (the worker is shutting down).
        if self._suspended:
            return
        # Freshness mark: metrics arrived right now (to guard is_focused() against
        # sticking when the camera hangs).
        import time as _time
        self._last_metrics_wall = _time.time()

        if not self._camera_marked_online:
            self._camera_marked_online = True
            self.dashboard.set_camera_online(True)

        calibrating = bool(m.get("calibrating"))

        # Chip states. gaze counts as "bad" only outside calibration.
        phone_bad = bool(m.get("phone"))
        gaze_bad = bool(m.get("looking_away")) and not calibrating
        posture_bad = bool(m.get("posture_bad"))

        # IMPORTANT: dashboard.set_detection with bad=True itself calls pet.react(...)
        # EVERY frame — that's the "freak-out". So we update the chip VISUALS directly
        # (_set_chip) and drive the cat's REACTIONS strictly through the debounce
        # (Trigger below). During calibration we keep the chips calm and don't fire
        # reactions — we only show the calibration status.
        if calibrating:
            self._set_chip("phone", False)
            self._set_chip("gaze", False)
            self._set_chip("posture", False)
            # During calibration we don't count focus (metrics aren't the norm yet).
            self.focused = False
            total = m.get("calib_total") or 0
            done = m.get("calib_progress") or 0
            self._route_say(f"Calibrating... {done}/{total}" if total else "Calibrating...")
            return

        self._set_chip("phone", phone_bad)
        self._set_chip("gaze", gaze_bad)
        self._set_chip("posture", posture_bad)

        # Current "focusedness": face in frame and not a single violation, outside
        # calibration. GameController reads is_focused() to award XP/combo.
        face_present = bool(m.get("face_present", True))
        self.focused = face_present and not (phone_bad or gaze_bad or posture_bad)

        # Game detection is disabled (a break with pause_detection_on_break):
        # the chips already updated visually, but we do NOT fire pet reactions and do
        # NOT emit distraction_alert — otherwise a real phone/gaze during a break
        # would reset the combo, hurt the pet and skew the stats. We keep the triggers
        # "pinned down" so accumulated condition doesn't fire right away on focus_start.
        if not self._detection_enabled:
            now = float(m.get("now") or 0.0)
            self._triggers["phone"].update(False, now)
            self._triggers["gaze"].update(False, now)
            self._triggers["posture"].update(False, now)
            return

        # Reaction debounce: Trigger.update(condition, now) fires only when the
        # condition holds for >= grace and the cooldown since the last reaction passed.
        now = float(m.get("now") or 0.0)
        fired = []
        if self._triggers["phone"].update(phone_bad, now):
            fired.append("phone")
        if self._triggers["gaze"].update(gaze_bad, now):
            fired.append("gaze")
        if self._triggers["posture"].update(posture_bad, now):
            fired.append("posture")

        for kind in fired:
            self._dispatch_reaction(kind)

    def _set_chip(self, name: str, bad: bool) -> None:
        """Update ONLY the detection chip visual (without the per-frame cat reaction).

        dashboard.set_detection additionally pokes pet.react on every bad frame
        (freak-out), so here we touch the chip itself + the public signal, and leave
        the cat's reaction to the _dispatch_reaction debounce."""
        chip = self.dashboard.chips.get(name)
        if chip is not None:
            chip.set_status(bad)
        self.dashboard.detection_changed.emit(name, bad)

    def _dispatch_reaction(self, kind: str) -> None:
        """Dispatch a reaction: to the floating cat (if present and visible) AND the embedded one.

        kind ∈ {'phone','gaze','posture'} — the internal detection name.
        For the embedded PetWidget we map into its phrase dictionary: gaze -> 'distract'.
        """
        # Floating cat (pet_engine): SURPRISED -> SULK chain. trigger_react is
        # thread-safe; we're on the GUI thread, so a direct call is fine.
        if self._floating_pet is not None and self._floating_pet.isVisible():
            try:
                self._floating_pet.engine.trigger_react()
            except Exception:
                pass

        # The embedded cat on the dashboard — always reacts with a phrase/mood.
        widget_kind = {"phone": "phone", "gaze": "distract", "posture": "posture"}.get(kind, "distract")
        self.dashboard.pet.react(widget_kind)

        # Game bookkeeping (combo reset/damage/sound/stats) — in GameController.
        # We emit exactly here, where the reaction actually fired (after the Trigger debounce).
        self.distraction_alert.emit(kind)

    def on_frame(self, bgr) -> None:
        """BGR numpy 320x180 -> live preview on the dashboard's camera card.

        BGR->RGB, QImage(Format_RGB888). We keep the frame memory on self
        (self._frame_buf), since QImage does NOT copy a foreign buffer."""
        # Once suspended (paused), drop any still-queued frames so the user's last image
        # never flashes back over the camera-off avatar.
        if self._suspended or bgr is None:
            return
        try:
            h, w = bgr.shape[:2]
            # BGR -> RGB by flipping the last channel (no cv2, to avoid pulling it in here).
            rgb = bgr[:, :, ::-1]
            # ascontiguousarray guarantees a dense buffer; we keep the reference alive.
            import numpy as np
            self._frame_buf = np.ascontiguousarray(rgb)
            bytes_per_line = self._frame_buf.strides[0]
            qimg = QImage(self._frame_buf.data, w, h, bytes_per_line,
                          QImage.Format.Format_RGB888)
            # .copy() — a deep copy, so the QPixmap doesn't depend on _frame_buf.
            pix = QPixmap.fromImage(qimg.copy())
            self.dashboard.set_camera_frame(pix)
        except Exception as exc:  # the preview isn't critical — don't crash the app
            self._status_cb(f"[camera] frame not rendered: {exc}")

    def on_status(self, text: str) -> None:
        """Status line from the detector (calibration, model loading, etc.)."""
        self._status_cb(text)

    def on_error(self, text: str) -> None:
        """Camera/model error: turn off the indicator and report it outward.

        IMPORTANT: we reset self.focused — otherwise after an error (camera offline)
        is_focused() could stay True off the last focused frame and the game tick
        would award "free" XP/combo off a dead camera."""
        self.focused = False
        self.dashboard.set_camera_online(False)
        self._camera_marked_online = False
        self._status_cb(f"[camera] ERROR: {text}")

    def on_calibrated(self, info: dict) -> None:
        """Calibration done — a short cat phrase."""
        self._route_say("Remembered my normal!", 2500)
