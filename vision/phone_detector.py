# -*- coding: utf-8 -*-
"""
Phone detector v3: YOLO + two context cues + a zoom pass for low-held phones.

Measured failure modes and their answers:
  * phone at the ear / edge-on  -> confidence drops to ~0.2-0.3
        => accept low-confidence hits when a HAND IS NEAR THE FACE.
  * phone held low at the chest/lap (doomscrolling pose) -> small, tilted, occluded
        => accept low-confidence hits when the user is LOOKING DOWN/AWAY, and if the
           full frame finds nothing in that context, run a second YOLO pass on the
           lower-center crop (a free 2x zoom right where held phones live).

accept_phone() is the pure decision rule (unit-testable).
Falls back to EfficientDet automatically when ultralytics is unavailable.
"""
import os
import math

import numpy as np

from .paths import MODELS_DIR
from .detection import OBJ_MODEL_URLS, PHONE_LABELS, HAND_MODEL_URL, ensure_model

COCO_CELL_PHONE = 67


def accept_phone(score, high, low, hand_near_face=False, context_down=False):
    """The acceptance rule: clear hit, or a weak hit backed by context."""
    if score >= high:
        return True, "clear"
    if score >= low and hand_near_face:
        return True, "hand+lowconf"
    if score >= low and context_down:
        return True, "down+lowconf"
    return False, ""


class PhoneDetector:
    def __init__(self, backend="auto", conf=0.35, conf_low=0.18, imgsz=640,
                 yolo_model="yolov8n.pt", use_hand_cue=True,
                 hand_face_dist=2.0, roi_pass=True):
        self.conf = float(conf)
        self.conf_low = float(conf_low)
        self.imgsz = max(160, int(imgsz))
        self.use_hand_cue = bool(use_hand_cue)
        self.hand_face_dist = float(hand_face_dist)
        self.roi_pass = bool(roi_pass)
        self.backend = None
        self._yolo = None
        self._eff = None
        self._hands = None

        if backend in ("auto", "yolo"):
            try:
                self._init_yolo(yolo_model)
                self.backend = "yolo"
            except Exception as e:
                print(f"[Phone] YOLO unavailable ({e}); falling back to EfficientDet.")
                if backend == "yolo":
                    raise
        if self.backend is None:
            self._init_efficientdet()
            self.backend = "efficientdet"

        if self.use_hand_cue:
            try:
                self._init_hands()
            except Exception as e:
                print(f"[Phone] Hand cue unavailable ({e}); detector-only mode.")
                self.use_hand_cue = False

    # ---- backends ------------------------------------------------------- #
    def _init_yolo(self, yolo_model):
        from ultralytics import YOLO
        self._yolo = YOLO(os.path.join(MODELS_DIR, yolo_model))
        self._yolo.predict(np.zeros((64, 64, 3), np.uint8), conf=self.conf_low,
                           imgsz=self.imgsz, classes=[COCO_CELL_PHONE], verbose=False)

    def _init_efficientdet(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        self._mp = mp
        op = ensure_model(OBJ_MODEL_URLS["efficientdet_lite0"],
                          os.path.join(MODELS_DIR, "efficientdet_lite0.tflite"))
        self._eff = vision.ObjectDetector.create_from_options(
            vision.ObjectDetectorOptions(
                base_options=mp_python.BaseOptions(model_asset_path=op),
                running_mode=vision.RunningMode.VIDEO,
                score_threshold=max(0.10, self.conf_low - 0.05), max_results=10))
        self._eff_ts = 0

    def _init_hands(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        self._mp = getattr(self, "_mp", mp)
        hp = ensure_model(HAND_MODEL_URL, os.path.join(MODELS_DIR, "hand_landmarker.task"))
        self._hands = vision.HandLandmarker.create_from_options(
            vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=hp),
                running_mode=vision.RunningMode.VIDEO, num_hands=2))
        self._hands_ts = 0

    # ---- raw passes ------------------------------------------------------ #
    def _yolo_boxes(self, bgr):
        r = self._yolo.predict(bgr, conf=self.conf_low, imgsz=self.imgsz,
                               classes=[COCO_CELL_PHONE], verbose=False)[0]
        boxes, best = [], 0.0
        for b in r.boxes:
            s = float(b.conf)
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            boxes.append((int(x1), int(y1), int(x2 - x1), int(y2 - y1), s))
            best = max(best, s)
        return best, boxes

    def _yolo_roi_boxes(self, bgr):
        """Zoom pass on the lower-center region where low-held phones live."""
        h, w = bgr.shape[:2]
        x0, x1 = int(w * 0.18), int(w * 0.82)
        y0, y1 = int(h * 0.35), h
        crop = bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return 0.0, []
        best, boxes = self._yolo_boxes(np.ascontiguousarray(crop))
        mapped = [(x + x0, y + y0, bw, bh, s) for (x, y, bw, bh, s) in boxes]
        return best, mapped

    def _eff_boxes(self, bgr, ts_ms):
        import cv2
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        if ts_ms is None:
            self._eff_ts += 33
            ts_ms = self._eff_ts
        res = self._eff.detect_for_video(mp_img, ts_ms)
        boxes, best = [], 0.0
        for d in res.detections:
            c = d.categories[0]
            if c.category_name and c.category_name.lower() in PHONE_LABELS:
                bb = d.bounding_box
                boxes.append((bb.origin_x, bb.origin_y, bb.width, bb.height, c.score))
                best = max(best, c.score)
        return best, boxes

    def _hand_near_face(self, bgr, face_center, face_width, ts_ms):
        if self._hands is None or face_center is None or not face_width:
            return False, 9.9
        import cv2
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        if ts_ms is None:
            self._hands_ts += 33
            ts_ms = self._hands_ts
        res = self._hands.detect_for_video(mp_img, ts_ms)
        cx, cy = face_center
        mind = 9.9
        for hl in res.hand_landmarks:
            hx = sum(p.x for p in hl) / len(hl)
            hy = sum(p.y for p in hl) / len(hl)
            mind = min(mind, math.hypot(hx - cx, hy - cy) / max(face_width, 1e-3))
        return (mind <= self.hand_face_dist), mind

    # ---- public ----------------------------------------------------------- #
    def detect(self, bgr, face_center=None, face_width=None, ts_ms=None,
               context_down=False):
        if self.backend == "yolo":
            best, boxes = self._yolo_boxes(bgr)
        else:
            best, boxes = self._eff_boxes(bgr, ts_ms)

        hand_near = False
        hand_dist = 9.9
        if self.use_hand_cue and face_center is not None:
            hand_near, hand_dist = self._hand_near_face(bgr, face_center, face_width, ts_ms)

        # zoom pass: nothing convincing yet, but the user looks down -> check the lap zone
        if (self.backend == "yolo" and self.roi_pass and context_down
                and best < self.conf):
            roi_best, roi_boxes = self._yolo_roi_boxes(bgr)
            if roi_best > best:
                best, boxes = roi_best, roi_boxes

        present, reason = accept_phone(best, self.conf, self.conf_low,
                                       hand_near, context_down)
        eff_thr = self.conf_low if (hand_near or context_down) else self.conf
        boxes = [b for b in boxes if b[4] >= eff_thr]
        return {
            "present": present, "score": best, "boxes": boxes,
            "hand_near_face": hand_near, "hand_dist": hand_dist,
            "reason": reason,
        }

    def close(self):
        for obj in (self._eff, self._hands):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
