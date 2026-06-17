# -*- coding: utf-8 -*-
"""
Shared detection primitives: model downloads, alert sounds, triggers, head-pose math,
distance estimation and lighting normalization. Pure logic, no camera or GUI.
"""
import os
import sys
import math
import time
import threading
import urllib.request

import numpy as np
import cv2

from .paths import IS_WINDOWS

if IS_WINDOWS:
    import winsound

# --------------------------------------------------------------------------- #
#  Models
# --------------------------------------------------------------------------- #
OBJ_MODEL_URLS = {
    "efficientdet_lite0": "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite",
    "efficientdet_lite2": "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite2/float32/latest/efficientdet_lite2.tflite",
}
FACE_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
POSE_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
HAND_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"

PHONE_LABELS = {"cell phone", "cellphone", "cell_phone", "mobile phone", "phone"}

# Sound patterns: list of (freq_hz, duration_ms); freq<=0 means pause.
PHONE_PATTERN = [(1100, 160), (0, 70), (1100, 160)]
AWAY_PATTERN = [(780, 220), (0, 60), (560, 280)]
POSTURE_PATTERN = [(620, 180), (0, 50), (620, 180), (0, 50), (520, 240)]
BREAK_START_PATTERN = [(880, 200), (660, 200), (0, 80), (440, 320)]
BREAK_END_PATTERN = [(440, 150), (0, 40), (660, 150), (0, 40), (880, 220)]
REWARD_PATTERN = [(523, 160), (659, 160), (784, 160), (1047, 420)]
SUMMON_PATTERN = [(784, 120), (988, 120), (1319, 200)]


def ensure_model(url, path):
    """Download a model once; afterwards it loads from disk."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print(f"[FocusGuard] Downloading model: {os.path.basename(path)} ...")
    tmp = path + ".part"
    last = [-1]

    def hook(blocks, block_size, total):
        if total > 0:
            pct = min(100, int(blocks * block_size * 100 / total))
            if pct != last[0]:
                last[0] = pct
                print(f"\r    {pct}%", end="", flush=True)

    try:
        urllib.request.urlretrieve(url, tmp, hook)
        os.replace(tmp, path)
        print("\r    done.      ")
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        print()
        raise RuntimeError(
            f"Could not download the model.\nURL: {url}\nError: {e}\n"
            "Check your internet connection (needed only on first run).")
    return path


# --------------------------------------------------------------------------- #
#  Sound
# --------------------------------------------------------------------------- #
class AlertPlayer:
    """Plays a beep pattern on a background thread; never overlaps itself."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._busy = False
        self._lock = threading.Lock()

    def _run(self, pattern):
        try:
            for freq, dur in pattern:
                if freq <= 0:
                    time.sleep(dur / 1000.0)
                elif IS_WINDOWS:
                    winsound.Beep(int(max(37, min(32767, freq))), int(dur))
                else:
                    sys.stdout.write("\a")
                    sys.stdout.flush()
                    time.sleep(dur / 1000.0)
        finally:
            with self._lock:
                self._busy = False

    def play(self, pattern, force=False):
        if not self.enabled:
            return
        with self._lock:
            if self._busy and not force:
                return
            self._busy = True
        threading.Thread(target=self._run, args=(pattern,), daemon=True).start()


# --------------------------------------------------------------------------- #
#  Triggers
# --------------------------------------------------------------------------- #
class Trigger:
    """Fires when a condition holds continuously >= grace seconds; repeats every
    cooldown seconds while it keeps holding."""

    def __init__(self, grace, cooldown):
        self.grace = grace
        self.cooldown = cooldown
        self.active_since = None
        self.last_alert = 0.0

    def update(self, condition, now):
        if not condition:
            self.active_since = None
            return False
        if self.active_since is None:
            self.active_since = now
        if (now - self.active_since) >= self.grace and (now - self.last_alert) >= self.cooldown:
            self.last_alert = now
            return True
        return False


class VoteWindow:
    """Sliding boolean window: True when >= min_votes of the last `size` are True."""

    def __init__(self, size, min_votes):
        self.size = max(1, int(size))
        self.min_votes = max(1, int(min_votes))
        self.buf = []

    def push(self, value):
        self.buf.append(bool(value))
        if len(self.buf) > self.size:
            self.buf.pop(0)
        return sum(self.buf) >= self.min_votes


# --------------------------------------------------------------------------- #
#  Head pose / distance
# --------------------------------------------------------------------------- #
def rotation_to_euler_deg(matrix):
    """(yaw, pitch, roll) in degrees from a 4x4 facial transformation matrix."""
    R = np.asarray(matrix, dtype=np.float64)[:3, :3]
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    if sy > 1e-6:
        pitch = math.degrees(math.atan2(R[2, 1], R[2, 2]))
        yaw = math.degrees(math.atan2(-R[2, 0], sy))
        roll = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    else:
        pitch = math.degrees(math.atan2(-R[1, 2], R[1, 1]))
        yaw = math.degrees(math.atan2(-R[2, 0], sy))
        roll = 0.0
    return yaw, pitch, roll


def estimate_distance_cm(landmarks, frame_w, gaze_cfg):
    """Distance to the face from the inter-pupil distance in pixels.

    distance = real_IPD * focal_px / ipd_px, focal_px derived from the camera FOV.
    If gaze_cfg provides viewing_distance_cm > 0, that fixed value wins.
    """
    fixed = 0.0
    try:
        fixed = float(gaze_cfg.get("viewing_distance_cm", 0) or 0)
    except (TypeError, ValueError):
        pass
    if fixed > 0:
        return fixed
    try:
        n = len(landmarks)
        if n >= 478:           # iris landmarks available
            li, ri = landmarks[468], landmarks[473]
        else:                  # fallback: outer eye corners
            li, ri = landmarks[33], landmarks[263]
        dx = (li.x - ri.x) * frame_w
        dy = (li.y - ri.y) * frame_w
        ipd_px = math.hypot(dx, dy)
        if ipd_px < 1:
            return 60.0
        hfov = math.radians(float(gaze_cfg.get("camera_hfov_deg", 60.0)))
        focal_px = (frame_w / 2.0) / math.tan(hfov / 2.0)
        real_ipd_cm = float(gaze_cfg.get("real_ipd_mm", 63.0)) / 10.0
        dist = real_ipd_cm * focal_px / ipd_px
        return float(min(200.0, max(25.0, dist)))
    except Exception:
        return 60.0


# --------------------------------------------------------------------------- #
#  Lighting
# --------------------------------------------------------------------------- #
_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def normalize_lighting(bgr):
    """Equalize brightness via CLAHE on the L channel (less light-dependent)."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = _clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
