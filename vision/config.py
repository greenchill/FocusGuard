# -*- coding: utf-8 -*-
"""Defaults, deep merge and load/save for config.json.

v4 design: the app targets ONE scenario done well - the user sits facing the laptop
camera. All gaze/posture thresholds are RELATIVE to a personal baseline captured by a
short calibration (sit normally, look at the screen). No multi-monitor modes, no
screen-geometry math, no app blocking.
"""
import os
import json

from .paths import CONFIG_PATH

DEFAULTS = {
    "camera_index": 0,
    "mirror": True,

    # Master switch for the webcam focus-tracking. When False the app runs as a
    # pure Pomodoro timer (no camera opened, no LED, no detection) and still awards
    # focus time for the elapsed session. Toggleable in Settings ("Use camera").
    "use_camera": True,

    "detect_phone": True,
    "detect_away": True,
    "detect_posture": True,

    "sound_enabled": True,
    "brown_noise": False,               # loop brown noise during focus (off during breaks)
    "normalize_lighting": True,         # CLAHE for face/pose in poor light (phone gets raw)
    "process_every_n_frames": 1,
    "alert_cooldown_seconds": 4.0,
    "calibration_frames": 60,           # ~2-3 s of valid face frames
    "calibrate_on_start": True,         # laptops open at different angles every time
    "sensitivity": "normal",            # relaxed | normal | strict (see core/game.py)

    "phone": {
        # Default to the bundled MediaPipe EfficientDet detector (no torch/ultralytics)
        # so the shipped app stays compact. "auto"/"yolo" still work in a dev venv that
        # has ultralytics installed; the detector falls back to EfficientDet otherwise.
        "backend": "efficientdet",      # auto | yolo | efficientdet
        "yolo_model": "yolov8n.pt",
        "imgsz": 640,
        "score_threshold": 0.35,        # clear phone
        "low_threshold": 0.18,          # accepted when a hand is near the face
        "hand_face_dist": 2.0,          # in face-widths
        "roi_pass": True,               # zoom pass on the lap zone when looking down
        "every_n_frames": 2,
        "vote_window": 8,
        "vote_min": 3,
        "grace_seconds": 1.0,
    },

    "gaze": {
        # all degrees are deviation FROM YOUR calibrated baseline
        "yaw_deg": 22.0,                # turned head left/right
        "down_deg": 9.0,                # chin drop (measured: down adds ~+10..14)
        "up_deg": 15.0,                 # looking above the screen
        "eye_down_delta": 0.22,         # eyeLookDown blendshape rise vs baseline
        "grace_seconds": 3.0,
        "invert_pitch": False,
        # distance estimation (also used by posture lean-in)
        "real_ipd_mm": 63.0,
        "camera_hfov_deg": 60.0,
    },

    "posture": {
        "neck_delta_deg": 12.0,         # neck inclination rise vs baseline
        "shoulder_tilt_deg": 15.0,      # absolute sideways lean
        "lean_in_ratio": 0.65,          # face closer than 65% of MEDIAN baseline distance
        "grace_seconds": 6.0,
        "every_n_frames": 4,
        "min_visibility": 0.5,
    },

    "session": {
        "mode": "pomodoro",             # pomodoro | timer | off
        "focus_minutes": 50,
        "break_minutes": 10,
        "cycles": 0,                    # 0 = endless
        "timer_minutes": 25,
        "pause_detection_on_break": True,
        "sound_on_transitions": True,
        "daily_goal_minutes": 120,
    },

    "firewall": {
        "enabled": False,               # websites via hosts; needs admin
        "block_during_focus": True,
        "unblock_during_break": True,
        "flush_dns": True,
    },

    "pet": {
        "enabled": True,
        "name": "Buddy",
        "style": "pixel",
        "skin": "orange",               # orange | gray | black | white
        "scale": 9,                     # pixels per sprite cell
        "survivability": 5,             # 1..10
        "hotkey": "<ctrl>+<alt>+p",
        "knock_enabled": True,
        "knock_sensitivity": 5,
        "start_hidden": False,
        "corner": "bottom-right",
    },
}


def deep_merge(base, user):
    """Recursively merge user into a copy of base (unknown keys are kept too)."""
    out = dict(base)
    for k, v in user.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(base[k], v)
        else:
            out[k] = v
    return out


# back-compat alias used by tests
_deep_merge = deep_merge


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))   # deep copy
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg = deep_merge(cfg, user)
        except Exception as e:
            print(f"[FocusGuard] Could not read config.json ({e}); using defaults.")
    return cfg


def save_config(cfg):
    """Save the config atomically (used by the GUI when applying settings)."""
    try:
        from .paths import atomic_write_json
        atomic_write_json(CONFIG_PATH, cfg)
        return True
    except Exception as e:
        print(f"[FocusGuard] Could not save config.json: {e}")
        return False
