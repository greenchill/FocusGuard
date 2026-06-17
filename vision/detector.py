# -*- coding: utf-8 -*-
"""
FocusDetector - the single detection core for the front-facing laptop setup.

Pipeline per frame:
  face landmarks (+ blendshapes + head matrix)  ->  gaze verdict vs personal baseline
  pose landmarks (every Nth frame)              ->  posture verdict vs baseline
  YOLO phone + hand-near-face cue (every Nth)   ->  phone verdict

Everything is judged RELATIVE to a calibrated baseline (captured while the user sits
normally looking at the screen), which is what makes a single fixed camera accurate.
While calibrating, gaze/posture report "calibrating" and never alert; phone detection
stays active (it is the primary feature).

classify_gaze / classify_posture are pure functions so the logic is unit-testable
without a camera.
"""
import time
from collections import deque

import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .paths import MODELS_DIR
from .detection import (
    FACE_MODEL_URL, ensure_model, normalize_lighting,
    rotation_to_euler_deg, estimate_distance_cm,
)
from . import posture as posture_mod
from . import calibration as calib
from .phone_detector import PhoneDetector


def _f(d, key, default):
    """Defensive float read: fuzzy configs must never crash the detector."""
    try:
        return float(d.get(key, default))
    except (TypeError, ValueError):
        return float(default)


# --------------------------------------------------------------------------- #
#  Pure verdicts (unit-testable)
# --------------------------------------------------------------------------- #
def classify_gaze(yaw, pitch, eye_down, baseline, gcfg):
    """Returns (state, looking_away, head_down).

    state: "calibrating" | "on screen" | "away" | "down"
    All thresholds are deltas from the personal baseline.
    """
    if not baseline or not baseline.get("captured"):
        return "calibrating", False, False
    ry = yaw - _f(baseline, "yaw", 0.0)
    rp = pitch - _f(baseline, "pitch", 0.0)
    if gcfg.get("invert_pitch", False):
        rp = -rp
    ed_rel = 0.0
    if eye_down is not None:
        ed_rel = eye_down - _f(baseline, "eye_down", 0.0)

    down = rp > _f(gcfg, "down_deg", 9.0) or ed_rel > _f(gcfg, "eye_down_delta", 0.22)
    away = down or abs(ry) > _f(gcfg, "yaw_deg", 22.0) or rp < -_f(gcfg, "up_deg", 15.0)
    state = "down" if down else ("away" if away else "on screen")
    return state, away, down


def classify_posture(neck_incl, shoulder_tilt, distance_cm, head_down, baseline, pcfg):
    """Returns (bad, why). Conservative: needs a baseline, never fires while unsure."""
    if not baseline or not baseline.get("captured"):
        return False, ""
    reasons = []
    base_neck = baseline.get("neck_incl")
    if neck_incl is not None and base_neck is not None:
        if neck_incl - float(base_neck) > _f(pcfg, "neck_delta_deg", 12.0):
            reasons.append("neck")
    if shoulder_tilt is not None and shoulder_tilt > _f(pcfg, "shoulder_tilt_deg", 15.0):
        reasons.append("tilt")
    base_dist = baseline.get("distance_cm")
    if distance_cm and base_dist:
        if distance_cm < float(base_dist) * _f(pcfg, "lean_in_ratio", 0.75):
            reasons.append("lean-in")
    if head_down:
        reasons.append("head-down")
    return (len(reasons) > 0), "+".join(reasons)


def _blendshape(cats, name):
    for c in cats:
        if c.category_name == name:
            return float(c.score)
    return 0.0


# --------------------------------------------------------------------------- #
#  The detector
# --------------------------------------------------------------------------- #
class FocusDetector:
    def __init__(self, cfg, status_cb=None):
        self.cfg = cfg
        self.gcfg = cfg.get("gaze", {})
        self.pcfg = cfg.get("posture", {})
        self.status_cb = status_cb or (lambda s: None)
        self._ts = 0

        # face (always; gaze + phone hand-cue + distance need it)
        fp = ensure_model(FACE_MODEL_URL, f"{MODELS_DIR}/face_landmarker.task")
        self.face = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=fp),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True))

        self.pose = None
        if cfg.get("detect_posture", True):
            try:
                self.pose = posture_mod.create_pose_landmarker()
            except Exception as e:
                self.status_cb(f"Posture unavailable: {e}")

        self.phone = None
        if cfg.get("detect_phone", True):
            phc = cfg.get("phone", {})
            self.phone = PhoneDetector(
                backend=phc.get("backend", "auto"),
                conf=_f(phc, "score_threshold", 0.35),
                conf_low=_f(phc, "low_threshold", 0.18),
                imgsz=int(_f(phc, "imgsz", 640)),
                yolo_model=phc.get("yolo_model", "yolov8n.pt"),
                use_hand_cue=True,
                hand_face_dist=_f(phc, "hand_face_dist", 2.0),
                roi_pass=bool(phc.get("roi_pass", True)))
            self.status_cb(f"Phone detector: {self.phone.backend}")

        # smoothing: lean-in compares a MEDIAN distance (IPD estimate is jittery),
        # and the posture verdict is a 2-of-3 majority so single frames can't flip it
        self._dist_hist = deque(maxlen=15)
        self._post_hist = deque(maxlen=3)

        self.baseline = calib.load_baseline()
        self.calibrating = not self.baseline.get("captured")
        self.calib_total = max(20, int(_f(cfg, "calibration_frames", 60)))
        self._calib_rows = []
        self.just_calibrated = None       # set once when calibration completes

        # cached last phone result (phone runs every Nth frame)
        self._phone_last = {"present": False, "score": 0.0, "boxes": [], "reason": ""}

    # ------------------------------------------------------------------ #
    def begin_calibration(self):
        self.baseline = dict(calib.EMPTY)
        self.calibrating = True
        self._calib_rows = []
        self.status_cb("Calibrating: sit normally and look at the screen...")

    def _finish_calibration(self):
        import statistics
        rows = self._calib_rows
        med = lambda xs: statistics.median(xs) if xs else None
        yaws = [r["yaw"] for r in rows]
        pitches = [r["pitch"] for r in rows]
        eyes = [r["eye_down"] for r in rows]
        necks = [r["neck"] for r in rows if r["neck"] is not None]
        dists = [r["dist"] for r in rows if r["dist"]]
        self.baseline = calib.save_baseline({
            "yaw": med(yaws) or 0.0,
            "pitch": med(pitches) or 0.0,
            "eye_down": med(eyes) or 0.0,
            "neck_incl": med(necks),
            "distance_cm": med(dists),
            "ts": time.time(),
        })
        self.calibrating = False
        self.just_calibrated = dict(self.baseline)
        self.status_cb(
            f"Calibrated: yaw {self.baseline['yaw']:+.0f}, pitch {self.baseline['pitch']:+.0f}"
            + (f", {self.baseline['distance_cm']:.0f} cm" if self.baseline.get("distance_cm") else ""))

    # ------------------------------------------------------------------ #
    def analyze(self, frame_bgr, do_phone=True, do_pose=True):
        """Run the pipeline on one BGR frame. Returns a reading dict. Never raises
        on odd frames - garbage in, calm 'nothing detected' out."""
        r = {
            "face_present": False, "yaw": 0.0, "pitch": 0.0, "eye_down": 0.0,
            "gaze_state": "calibrating" if self.calibrating else "no face",
            "looking_away": False, "head_down": False,
            "neck_incl": None, "shoulder_tilt": None,
            "posture_bad": False, "posture_why": "",
            "distance_cm": None,
            "phone_present": self._phone_last["present"],
            "phone_score": self._phone_last["score"],
            "phone_boxes": self._phone_last["boxes"],
            "phone_reason": self._phone_last["reason"],
            "phone_ran": False,
            "calibrating": self.calibrating,
            "calib_progress": len(self._calib_rows),
            "calib_total": self.calib_total,
        }
        try:
            frame_bgr = np.ascontiguousarray(frame_bgr)
            if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.dtype != np.uint8:
                return r
            h, w = frame_bgr.shape[:2]
            if h < 32 or w < 32:
                return r
        except Exception:
            return r

        proc = frame_bgr
        if self.cfg.get("normalize_lighting", True):
            try:
                proc = normalize_lighting(frame_bgr)
            except Exception:
                proc = frame_bgr

        self._ts += 33
        face_center = None
        face_w = None
        try:
            rgb = cv2.cvtColor(proc, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            fr = self.face.detect_for_video(mp_img, self._ts)
        except Exception:
            return r

        if fr.face_landmarks:
            r["face_present"] = True
            lm = fr.face_landmarks[0]
            xs = [p.x for p in lm]; ys = [p.y for p in lm]
            face_center = (sum(xs) / len(xs), sum(ys) / len(ys))
            face_w = max(xs) - min(xs)
            try:
                r["distance_cm"] = estimate_distance_cm(lm, w, self.gcfg)
            except Exception:
                pass
            if fr.facial_transformation_matrixes:
                yaw, pitch, _ = rotation_to_euler_deg(fr.facial_transformation_matrixes[0])
                r["yaw"], r["pitch"] = yaw, pitch
            if fr.face_blendshapes:
                cats = fr.face_blendshapes[0]
                r["eye_down"] = (_blendshape(cats, "eyeLookDownLeft")
                                 + _blendshape(cats, "eyeLookDownRight")) / 2.0

        # ---- pose (neck/shoulders); forced on while calibrating ---- #
        neck = tilt = None
        if self.pose is not None and (do_pose or self.calibrating):
            try:
                pr = self.pose.detect_for_video(mp_img, self._ts)
                if pr.pose_landmarks:
                    pl = pr.pose_landmarks[0]
                    if posture_mod.shoulders_visible(pl, _f(self.pcfg, "min_visibility", 0.5)):
                        neck = posture_mod.neck_inclination_deg(pl)
                        tilt = posture_mod.shoulder_tilt_deg(pl)
            except Exception:
                pass
        r["neck_incl"], r["shoulder_tilt"] = neck, tilt

        # ---- calibration collection ---- #
        if self.calibrating and r["face_present"] and fr.facial_transformation_matrixes:
            self._calib_rows.append({
                "yaw": r["yaw"], "pitch": r["pitch"], "eye_down": r["eye_down"],
                "neck": neck, "dist": r["distance_cm"],
            })
            r["calib_progress"] = len(self._calib_rows)
            if len(self._calib_rows) >= self.calib_total:
                self._finish_calibration()
                r["calibrating"] = False

        # ---- gaze verdict ---- #
        if r["face_present"]:
            state, away, down = classify_gaze(
                r["yaw"], r["pitch"], r["eye_down"], self.baseline, self.gcfg)
            r["gaze_state"], r["looking_away"], r["head_down"] = state, away, down
        else:
            if self.calibrating:
                r["gaze_state"], r["looking_away"] = "calibrating", False
            else:
                r["gaze_state"], r["looking_away"] = "no face", True

        # ---- posture verdict (median distance + 2-of-3 majority) ---- #
        if r["distance_cm"]:
            self._dist_hist.append(r["distance_cm"])
        if not self.calibrating:
            import statistics
            smooth_dist = statistics.median(self._dist_hist) if self._dist_hist else r["distance_cm"]
            bad, why = classify_posture(
                neck, tilt, smooth_dist, r["head_down"], self.baseline, self.pcfg)
            if do_pose or r["head_down"]:
                self._post_hist.append((bad, why))
            if self._post_hist:
                bads = [b for b, _ in self._post_hist]
                if sum(bads) * 2 > len(bads):           # majority says bad
                    r["posture_bad"] = True
                    r["posture_why"] = next((w for b, w in reversed(self._post_hist) if b), why)
                else:
                    r["posture_bad"], r["posture_why"] = False, ""

        # ---- phone (context: a user looking down/away often holds a phone low) ---- #
        if self.phone is not None and do_phone:
            context_down = r["head_down"] or r["gaze_state"] == "away"
            try:
                pres = self.phone.detect(frame_bgr, face_center=face_center,
                                         face_width=face_w, ts_ms=self._ts,
                                         context_down=context_down)
                self._phone_last = {
                    "present": pres["present"], "score": pres["score"],
                    "boxes": pres["boxes"], "reason": pres.get("reason", ""),
                }
                r["phone_present"] = pres["present"]
                r["phone_score"] = pres["score"]
                r["phone_boxes"] = pres["boxes"]
                r["phone_reason"] = pres.get("reason", "")
                r["phone_ran"] = True
            except Exception:
                pass

        return r

    def close(self):
        for obj in (self.face, self.pose, self.phone):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
