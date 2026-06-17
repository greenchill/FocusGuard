# -*- coding: utf-8 -*-
"""Personal baseline for the front-facing setup.

The user sits normally and looks at the screen for a couple of seconds; we store the
median head yaw/pitch, eye-look-down level, neck inclination and viewing distance.
Every detector threshold is then a DELTA from this baseline, which makes the system
accurate for this person/camera without any manual tuning.
"""
import os
import json

from .paths import DATA_DIR, atomic_write_json

CALIB_PATH = os.path.join(DATA_DIR, "calibration.json")

EMPTY = {
    "captured": False,
    "yaw": 0.0,
    "pitch": 0.0,
    "eye_down": 0.0,
    "neck_incl": None,
    "distance_cm": None,
    "ts": 0.0,
}


def load_baseline():
    if os.path.exists(CALIB_PATH):
        try:
            with open(CALIB_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("captured") and "yaw" in d and "pitch" in d:
                out = dict(EMPTY)
                out.update(d)
                return out
        except Exception:
            pass
    return dict(EMPTY)


def save_baseline(baseline):
    d = dict(EMPTY)
    d.update(baseline)
    d["captured"] = True
    try:
        atomic_write_json(CALIB_PATH, d)
    except Exception as e:
        print(f"[Calibration] Could not save: {e}")
    return d


def clear_baseline():
    try:
        if os.path.exists(CALIB_PATH):
            os.remove(CALIB_PATH)
    except Exception:
        pass
    return dict(EMPTY)
