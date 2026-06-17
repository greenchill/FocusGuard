# -*- coding: utf-8 -*-
"""
DetectionWorker — camera thread (ported from FocusGuard gui/worker.py to PyQt6).

Thin by design: it owns the camera and the frame cadence; ALL detection logic
lives in vision.detector.FocusDetector. Results are emitted to the GUI as Qt signals.

The only difference from the original FocusGuard is PySide6 Signal -> PyQt6 pyqtSignal
and core.* imports -> vision.*. Everything else is identical (cv2, cadence, stop latch).
"""
import time

import cv2
from PyQt6.QtCore import QThread, pyqtSignal as Signal

from vision.paths import IS_WINDOWS
from vision.detection import VoteWindow
from vision.detector import FocusDetector


class DetectionWorker(QThread):
    frame_ready = Signal(object)      # small BGR preview with phone boxes
    metrics = Signal(dict)            # per-frame reading for the dashboard
    status_text = Signal(str)
    error = Signal(str)
    calibrated = Signal(dict)         # once when calibration completes

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        # stop is a one-way latch: respected even if requested while models
        # are loading (a plain running flag would be overwritten in run()).
        self._stop_requested = False
        self._detect_enabled = True
        self._want_calibration = False

    # ---- external control ----------------------------------------------- #
    def set_detection_enabled(self, on):
        self._detect_enabled = bool(on)

    def start_calibration(self):
        self._want_calibration = True

    def stop(self):
        self._stop_requested = True

    # ---- thread body ----------------------------------------------------- #
    def run(self):
        cfg = self.cfg
        if self._stop_requested:
            return
        try:
            detector = FocusDetector(cfg, status_cb=self.status_text.emit)
        except Exception as e:
            self.error.emit(f"Failed to load models: {e}")
            return
        if self._stop_requested:                    # stop arrived while models were loading
            detector.close()
            return

        cam_api = cv2.CAP_DSHOW if IS_WINDOWS else 0
        cap = cv2.VideoCapture(int(cfg.get("camera_index", 0)), cam_api)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        if not cap.isOpened():
            detector.close()
            self.error.emit(f"Camera #{cfg.get('camera_index', 0)} did not open. "
                            f"Change camera_index in Settings.")
            return
        if self._stop_requested:                    # stop arrived while opening the camera
            cap.release()
            detector.close()
            return

        phc = cfg.get("phone", {})
        votes = VoteWindow(phc.get("vote_window", 8), phc.get("vote_min", 3))
        n = max(1, int(cfg.get("process_every_n_frames", 1)))
        phone_n = max(1, int(phc.get("every_n_frames", 2)))
        pose_n = max(1, int(cfg.get("posture", {}).get("every_n_frames", 4)))

        # the laptop is opened at a different angle every time -> by default a fresh
        # 3-second calibration on each launch (config: calibrate_on_start)
        if cfg.get("calibrate_on_start", True) and not detector.calibrating:
            detector.begin_calibration()
        if detector.calibrating:
            self.status_text.emit("Calibrating: sit normally and look at the screen...")

        idx = 0
        fps = 0.0
        t_prev = time.time()
        phone_voted = False

        while not self._stop_requested:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.03)
                continue
            if cfg.get("mirror", True):
                frame = cv2.flip(frame, 1)

            now = time.time()
            idx += 1

            if self._want_calibration:
                self._want_calibration = False
                detector.begin_calibration()

            reading = None
            do = self._detect_enabled and (idx % n == 0)
            if do:
                try:
                    reading = detector.analyze(
                        frame,
                        do_phone=(idx % (phone_n * n) == 0),
                        do_pose=(idx % (pose_n * n) == 0))
                except Exception:
                    if self._stop_requested:
                        break
                    reading = None

            if reading is not None and reading.get("phone_ran"):
                phone_voted = votes.push(reading["phone_present"])
            if detector.just_calibrated is not None:
                self.calibrated.emit(detector.just_calibrated)
                detector.just_calibrated = None

            # FPS (smoothed)
            dt = now - t_prev
            t_prev = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)

            # preview with phone boxes
            preview = frame.copy()
            boxes = (reading or {}).get("phone_boxes", []) if reading else []
            for (x, y, bw, bh, sc) in boxes:
                cv2.rectangle(preview, (x, y), (x + bw, y + bh), (0, 0, 255), 2)
                cv2.putText(preview, f"{sc:.2f}", (x, max(0, y - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            self.frame_ready.emit(cv2.resize(preview, (320, 180)))

            rd = reading or {}
            self.metrics.emit({
                "now": now,
                "fps": fps,
                "detect_on": self._detect_enabled,
                "calibrating": rd.get("calibrating", detector.calibrating),
                "calib_progress": rd.get("calib_progress", 0),
                "calib_total": rd.get("calib_total", detector.calib_total),
                "face_present": rd.get("face_present", False),
                "gaze_state": rd.get("gaze_state", "-"),
                "looking_away": rd.get("looking_away", False),
                "head_down": rd.get("head_down", False),
                "yaw": rd.get("yaw", 0.0),
                "pitch": rd.get("pitch", 0.0),
                "distance_cm": rd.get("distance_cm"),
                "posture_bad": rd.get("posture_bad", False),
                "posture_why": rd.get("posture_why", ""),
                "phone": phone_voted,
                "phone_score": rd.get("phone_score", 0.0),
                "phone_reason": rd.get("phone_reason", ""),
            })

        cap.release()
        detector.close()
