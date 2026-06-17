# -*- coding: utf-8 -*-
"""
Gamification: focus combo multiplier, level-unlocked pet accessories, and
user-friendly sensitivity presets (production-ready instead of magic numbers).
"""

# ---- combo: clean focus time multiplies XP ---------------------------------- #
COMBO_STEPS = [(1500.0, 3), (600.0, 2)]    # >=25 min clean -> x3, >=10 min -> x2


class Combo:
    """Uninterrupted focus builds a multiplier; any distraction resets it."""

    def __init__(self):
        self.clean_seconds = 0.0
        self.best = 1

    @property
    def multiplier(self):
        for thr, mult in COMBO_STEPS:
            if self.clean_seconds >= thr:
                return mult
        return 1

    def on_focus_tick(self, dt):
        """Returns the new multiplier if it just increased, else None."""
        before = self.multiplier
        self.clean_seconds += max(0.0, dt)
        now = self.multiplier
        if now > self.best:
            self.best = now
        return now if now > before else None

    def on_distraction(self):
        self.clean_seconds = 0.0

    def reset(self):
        self.clean_seconds = 0.0


# ---- accessories unlocked by pet level --------------------------------------- #
ACCESSORY_LEVELS = [(3, "bowtie"), (5, "collar"), (8, "crown"), (12, "hat")]


def accessories_for(level):
    return [name for lvl, name in ACCESSORY_LEVELS if level >= lvl]


def next_unlock(level):
    for lvl, name in ACCESSORY_LEVELS:
        if level < lvl:
            return lvl, name
    return None


# ---- sensitivity presets ------------------------------------------------------ #
PRESETS = {
    "relaxed": {
        "gaze": {"yaw_deg": 28.0, "down_deg": 12.0, "up_deg": 20.0,
                 "eye_down_delta": 0.28, "grace_seconds": 5.0},
        "posture": {"neck_delta_deg": 16.0, "shoulder_tilt_deg": 20.0,
                    "lean_in_ratio": 0.60, "grace_seconds": 10.0},
        "phone": {"score_threshold": 0.40, "low_threshold": 0.22},
    },
    "normal": {
        "gaze": {"yaw_deg": 22.0, "down_deg": 9.0, "up_deg": 15.0,
                 "eye_down_delta": 0.22, "grace_seconds": 3.0},
        "posture": {"neck_delta_deg": 12.0, "shoulder_tilt_deg": 15.0,
                    "lean_in_ratio": 0.65, "grace_seconds": 6.0},
        "phone": {"score_threshold": 0.35, "low_threshold": 0.18},
    },
    "strict": {
        "gaze": {"yaw_deg": 16.0, "down_deg": 7.0, "up_deg": 12.0,
                 "eye_down_delta": 0.18, "grace_seconds": 2.0},
        "posture": {"neck_delta_deg": 9.0, "shoulder_tilt_deg": 12.0,
                    "lean_in_ratio": 0.72, "grace_seconds": 4.0},
        "phone": {"score_threshold": 0.30, "low_threshold": 0.15},
    },
}


def apply_preset(cfg, name):
    """Writes a preset's thresholds into cfg (in place). Returns True if known."""
    p = PRESETS.get(name)
    if not p:
        return False
    for section, values in p.items():
        cfg.setdefault(section, {}).update(values)
    cfg["sensitivity"] = name
    return True
