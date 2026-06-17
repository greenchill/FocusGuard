# -*- coding: utf-8 -*-
"""
Posture monitoring on MediaPipe Pose, robust to a turned / angled body.

The user often sits at an angle (not square to the camera), so the old "ear-x minus
shoulder-x" forward-head metric breaks. Instead we use the NECK INCLINATION:
the angle of the line (shoulder-midpoint -> nose) away from vertical. Slouching /
tech-neck pushes the head forward and down, which increases this angle regardless of
left/right body rotation.

Because every body and camera angle differs, we compare against a per-person BASELINE
(captured while sitting upright) rather than an absolute threshold. Pure logic +
a thin detector wrapper.
"""
import os
import math

from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .paths import MODELS_DIR
from .detection import ensure_model, POSE_MODEL_URL

# MediaPipe Pose landmark indices (33 points)
NOSE = 0
L_EAR, R_EAR = 7, 8
L_SHOULDER, R_SHOULDER = 11, 12


def _mid(a, b):
    return (a.x + b.x) / 2.0, (a.y + b.y) / 2.0


def neck_inclination_deg(landmarks):
    """Angle (deg) of the shoulder-mid -> nose vector away from vertical.

    ~0 = head directly above shoulders (upright); larger = head forward/down.
    Rotation-robust: uses vertical reference, not left/right x-offset.
    """
    try:
        ls, rs = landmarks[L_SHOULDER], landmarks[R_SHOULDER]
        nose = landmarks[NOSE]
        mx, my = _mid(ls, rs)
        dx = nose.x - mx
        dy = nose.y - my           # image y grows downward; nose is above => dy<0
        # angle from the upward vertical axis
        ang = math.degrees(math.atan2(abs(dx), abs(dy) + 1e-6))
        return ang
    except Exception:
        return 0.0


def shoulder_tilt_deg(landmarks):
    """Acute angle of the shoulder line vs horizontal (0 = level, 90 = vertical)."""
    try:
        ls, rs = landmarks[L_SHOULDER], landmarks[R_SHOULDER]
        dy = abs(rs.y - ls.y)
        dx = abs(rs.x - ls.x)
        return abs(math.degrees(math.atan2(dy, dx)))
    except Exception:
        return 0.0


def forward_head_ratio(landmarks):
    """Legacy metric kept for reference/tests: |ear-x - shoulder-x| / shoulder width."""
    try:
        ls, rs = landmarks[L_SHOULDER], landmarks[R_SHOULDER]
        le, re = landmarks[L_EAR], landmarks[R_EAR]
        shoulder_w = abs(ls.x - rs.x)
        if shoulder_w < 1e-4:
            return 0.0
        left = abs(le.x - ls.x) / shoulder_w
        right = abs(re.x - rs.x) / shoulder_w
        return (left + right) / 2.0
    except Exception:
        return 0.0


def shoulders_visible(landmarks, min_vis=0.5):
    try:
        return (landmarks[L_SHOULDER].visibility >= min_vis and
                landmarks[R_SHOULDER].visibility >= min_vis and
                landmarks[NOSE].visibility >= min_vis)
    except Exception:
        return False


def is_bad_posture(landmarks, pcfg, baseline_incl=None):
    """True if posture looks bad. Rotation-robust + optional per-person baseline.

    Returns (bad, neck_incl_deg, shoulder_tilt_deg).
    - If baseline_incl is given, "bad" means neck angle exceeds baseline + delta.
    - Otherwise falls back to absolute thresholds.
    """
    if not shoulders_visible(landmarks, float(pcfg.get("min_visibility", 0.5))):
        return False, 0.0, 0.0
    incl = neck_inclination_deg(landmarks)
    tilt = shoulder_tilt_deg(landmarks)

    if baseline_incl is not None:
        delta = float(pcfg.get("neck_delta_deg", 12.0))
        bad = incl > (baseline_incl + delta)
    else:
        bad = incl > float(pcfg.get("neck_incl_deg", 32.0))
    # extreme shoulder tilt is always suspicious (leaning sideways)
    if tilt > float(pcfg.get("shoulder_tilt_deg", 18.0)):
        bad = True
    return bad, incl, tilt


def create_pose_landmarker():
    """Downloads (if needed) the Pose model and builds a VIDEO detector."""
    path = ensure_model(POSE_MODEL_URL, os.path.join(MODELS_DIR, "pose_landmarker_lite.task"))
    return vision.PoseLandmarker.create_from_options(
        vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=path),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ))
