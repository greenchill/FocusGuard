# -*- coding: utf-8 -*-
"""
pet_engine.py — PHYSICS + BEHAVIOR of the FocusGuard desktop cat.

This module is self-contained and runs directly (`python pet_engine.py`):
it shows a floating cat on top of all windows that walks/runs/hunts,
falls (with a LAND landing), sleeps, grooms/plays/stretches, reacts
to a "distraction" with a SURPRISED -> SULK chain, gets petted on click (PETTED),
and which you can drag with the mouse (HELD) and "throw" with inertia.

Architecture (3 layers, as in the research):
    * SpriteSheet — one-time loading/processing of the cat_sheet.png sprite sheet
      (chroma-key the pink background via Pillow -> auto-crop -> a single
      scale for all frames -> QPixmap). If the file is missing/corrupt it returns
      None, and the engine draws a PROCEDURAL cat via QPainter.
    * PetEngine(QObject) — the simulation core: FSM + physics. Stores the "feet anchor"
      (feet anchor, bottom-center), velocities vx/vy, and the screen work area.
      Drives a QTimer game loop (~60 FPS) with real dt (QElapsedTimer),
      emits frame_updated(QPoint topLeft, QPixmap) and state_changed(object).
    * PetWindow(QWidget) — the view: a frameless transparent always-on-top window,
      click-through via the sprite mask, mouse dragging, and a right-click menu.

The physics concepts are borrowed from libshijima (anchor = feet; gravity with
terminal velocity; anti-tunneling via subticks) and Kirby/Shimeji-ee
(weighted FSM transitions, throw inertia from mouse history).

Comments are in English, identifiers are in English.
"""

from __future__ import annotations

import enum
import math
import os
import random
import sys
import time

from PyQt6.QtCore import (
    Qt, QObject, QTimer, QElapsedTimer, QPoint, QRect, QRectF, pyqtSignal,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QBrush, QPen, QTransform, QGuiApplication,
    QAction, QFont, QFontMetrics, QPainterPath, QBitmap,
)
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QMenu

# Theme colors/fonts for the speech bubble. Imported defensively so pet_engine
# still runs standalone (`python pet_engine.py`) even if theme.py is unavailable.
try:
    from theme import COLORS as _THEME_COLORS, FONTS as _THEME_FONTS
except Exception:  # pragma: no cover - standalone fallback
    _THEME_COLORS = {"accent": "#A855F7", "elevated": "#2E2E44",
                     "text": "#ECEFF4", "surface": "#262638"}
    _THEME_FONTS = {"body": "Segoe UI"}

# Cat one-liners for the desktop pet's speech bubble (click = petting,
# distraction = startled). Imported defensively for the same reason.
try:
    from vision.phrases import say_for as _say_for
except Exception:  # pragma: no cover - standalone fallback
    def _say_for(event, **kw):
        _FALLBACK = {
            "petting": ["Purr~", "Mrp!", "Pet me more!", "*happy purring*"],
            "phone": ["Phone down!", "Eyes on the screen!"],
            "away": ["Back to the screen!", "Hey, focus!"],
            "posture": ["Sit up straight!", "Posture check!"],
        }
        return random.choice(_FALLBACK.get(event, ["Mrr?"]))

# The Windows console defaults to cp1252 and crashes on non-ASCII in print().
# We switch stdout/stderr to UTF-8 (if possible), and log() additionally
# silences any encoding errors — friendly messages should never
# bring the application down.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


def log(message: str) -> None:
    """A safe print: never raises UnicodeEncodeError."""
    try:
        print(message)
    except Exception:
        try:
            sys.stdout.buffer.write(
                (message + "\n").encode("utf-8", "replace"))
        except Exception:
            pass


# Pillow is the preferred path for image processing. numpy on this machine
# is BROKEN (DLL), so it must not be imported. If PIL is unavailable, we
# run on a pure Qt fallback (the procedural cat).
try:
    from PIL import Image  # noqa: F401
    _HAS_PIL = True
except Exception:  # pragma: no cover - depends on the environment
    _HAS_PIL = False


# =========================================================================== #
#  CONFIG BLOCK. All "magic numbers" gathered here for convenient tuning.     #
# =========================================================================== #

# --- Sprite sheet --------------------------------------------------------- #
SHEET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "assets", "cat_sheet.png")
COLS = 4                 # columns (frames per row)
ROWS = 16                # rows (one row per sprite-sheet pose)
TARGET_HEIGHT = 150      # target height of the tallest frame (px)
CHROMA_TOLERANCE = 60    # Euclidean-distance threshold to background color (0..255)
# Alpha threshold for the window CLICK-THROUGH mask (not the displayed sprite): a
# pixel counts as "cat" in the mask if its alpha is at least this. Kept low so the
# soft anti-aliased edge stays inside the mask (the smooth sprite is shown as-is);
# building the mask from a hard threshold avoids QPixmap.mask()'s dithered stipple,
# which is what looked "see-through" on the live translucent window.
MASK_ALPHA_CUTOFF = 24

# Sprite-sheet row -> ROW INDEX in the new 4-column x 16-row sheet.
# Cats in directional rows (WALK/RUN/HUNT) FACE RIGHT.
# 0 SIT, 1 WALK, 2 RUN, 3 FALL(on its back), 4 HELD(dangling/standing, lifted),
# 5 LAND(crouch+dust), 6 SLEEP(curled up+Z), 7 STRETCH(yawn/stretch),
# 8 GROOM(licking itself), 9 PETTED(squints from the petting), 10 LOVE(hearts),
# 11 SULK(pouting/offended), 12 SURPRISED('!'), 13 PLAY(blue yarn ball),
# 14 HUNT(sneaking after the mouse), 15 MEOW(mouth open).
STATE_ROWS = {
    "SIT":       0,
    "WALK":      1,
    "RUN":       2,
    "FALL":      3,
    "HELD":      4,
    "LAND":      5,
    "SLEEP":     6,
    "STRETCH":   7,
    "GROOM":     8,
    "PETTED":    9,
    "LOVE":      10,
    "SULK":      11,
    "SURPRISED": 12,
    "PLAY":      13,
    "HUNT":      14,
    "MEOW":      15,
}

# ANIMATION frames per second for each state (flip-through speed).
# Keys are PetState names (.value). _advance_animation uses .get with a
# sensible default, so a missing key never crashes.
STATE_FPS = {
    "SIT":        3,
    "IDLE":       3,
    "WALK":       8,
    "RUN":        12,
    "FALL":       6,
    "DRAG":       6,
    "HELD":       6,
    "LAND":       10,
    "SLEEP":      2,
    "STRETCH":    6,
    "GROOM":      6,
    "PETTED":     5,
    "LOVE":       7,
    "SULK":       4,
    "SURPRISED":  9,
    "PLAY":       8,
    "HUNT":       6,
    "MEOW":       6,
}

# Per-state FRAME SELECTION. Some rows contain a frame that reads as unnatural;
# we list the frame indices to KEEP (in order). SIT frame 2 is a one-eye WINK
# (frame 0 = wave, frames 1 & 3 = a natural both-eye blink) — excluding it means
# the idle cat only ever blinks with BOTH eyes, never winks. Other rows are left
# untouched (no entry => keep all four frames in their natural order).
STATE_FRAME_ORDER = {
    "SIT": [0, 1, 3],
    # The 4th FALL cell in the sheet is a stray DOUBLE cat (two small upright cats),
    # not part of the on-back fall tumble — it flashed for a split second right before
    # landing ("the doubled cat"). Keep only the three real tumble frames.
    "FALL": [0, 1, 2],
}

# Per-state EXTRA SCALE, applied on top of the single uniform sheet scale. The
# HELD/DRAG art is a narrow, vertically-stretched "dangling" cat: under the
# common height normalization its body/head render noticeably SMALLER than the
# sitting cat, so the pet appears to shrink the moment you pick it up. A modest
# upscale brings the lifted cat back to the sitting cat's visual size.
STATE_EXTRA_SCALE = {
    # The held ("scruff") cat reads a touch small when you pick it up — small bump.
    # FALL stays NATIVE (an upscale looked oversized); LAND/PLAY are no longer used.
    "HELD": 1.45,
    # HUNT (the cat sneaking after the mouse) is a very wide, stretched pose that
    # renders bigger than the sitting cat — shrink it down.
    "HUNT": 0.82,
}

# --- Game loop / physics -------------------------------------------------- #
FPS = 60                         # engine tick rate
TICK_MS = int(1000 / FPS)        # ~16 ms
DT_CLAMP = 1.0 / 20.0            # max real dt (s): protection against lag spikes

FLOOR_EPS = 1.0                  # "feet on the floor" tolerance (px); see _floor_y()
# FASTER FALL (the user asked to "speed up the fall"). The previous gravity
# (0.9*FPS) was too weak — the cat barely slid down. Now a fall from the top
# of the screen takes about 0.5-0.8 s (not a teleport): at g~48*FPS a fall of
# ~900 px lasts ~0.6 s before terminal velocity. Anti-tunneling (subticks +
# per-frame clamp) and the floor snap are preserved.
GRAVITY = 48.0 * FPS             # free-fall acceleration (px/s^2)
TERMINAL_VELOCITY = 30.0 * FPS   # terminal fall velocity (px/s)
MAX_STEP_RATIO = 0.9             # max per-frame displacement as a fraction of sprite height
PHYSICS_SUBTICKS = 4             # subticks per frame (anti-tunneling as in libshijima)

WALK_SPEED = 2.0 * FPS           # horizontal walk speed (px/s)
RUN_SPEED = 4.0 * FPS            # horizontal run speed (px/s)
HUNT_SPEED = 1.2 * FPS           # horizontal sneaking-step speed (px/s)
WALL_BOUNCE = 0.0                # wall bounce for walking (0 => just turn around)
FLOOR_BOUNCE = 0.2               # floor bounce after a throw (0..1) — settles faster
THROW_SCALE = 1.0                # throw inertia multiplier
THROW_MAX_SPEED = 30.0 * FPS     # throw speed cap (px/s)

# --- Behavior (FSM) ------------------------------------------------------- #
IDLE_MIN_S, IDLE_MAX_S = 2.5, 6.0     # how long to stay in IDLE before deciding
WALK_MIN_S, WALK_MAX_S = 1.5, 4.0     # walk duration
RUN_MIN_S, RUN_MAX_S = 1.0, 2.5       # run duration
HUNT_MIN_S, HUNT_MAX_S = 2.0, 4.0     # sneaking-hunt duration
SLEEP_AFTER_IDLE_S = 14.0             # after this many idle seconds -> SLEEP
SLEEP_MIN_S, SLEEP_MAX_S = 8.0, 20.0  # sleep duration

# Durations of the stationary "grounded" mini-scenes (vx=vy=0 on the floor).
GROUNDED_MIN_S, GROUNDED_MAX_S = 1.5, 3.5  # GROOM/PLAY/STRETCH/MEOW/LOVE/PETTED/SULK
LAND_DURATION_S = 0.4                 # short landing (crouch+dust) -> IDLE
PETTED_DURATION_S = 2.0               # petting on click (squints) -> IDLE

# Distraction reaction chain (external trigger_react from the YOLO module):
# SURPRISED ('!') -> SULK (pouting) -> IDLE. Default durations (s).
SURPRISED_DURATION_S = 0.6            # short '!' startle
SULK_DURATION_S = 2.5                 # offended/pouting
REACT_DURATION_MS = int(SULK_DURATION_S * 1000)  # compatibility with the old API

# Weighted transition probabilities out of IDLE (like Frequency in shimeji-ee).
# Normalized automatically. LOVE/PETTED/SURPRISED/SULK/HELD/LAND are NOT
# included here — they are triggered by interactions/physics, not at random.
IDLE_CHOICE_WEIGHTS = {
    "WALK":    36,
    "RUN":     16,
    "HUNT":    10,
    "GROOM":   8,
    "STRETCH": 6,
    "MEOW":    6,
    "IDLE":    12,
}

# Colors of the procedural fallback cat (when the sprite sheet is missing/broken).
FALLBACK_FUR = QColor(235, 140, 60)
FALLBACK_FUR_DARK = QColor(200, 110, 40)
FALLBACK_OUTLINE = QColor(60, 40, 25)
FALLBACK_EYE = QColor(40, 30, 20)


# =========================================================================== #
#  FSM STATES                                                                 #
# =========================================================================== #
class PetState(enum.Enum):
    """Logical states of the cat (see research: enum-in-code FSM).

    The old aggressive REACT state has been removed: the distraction reaction
    is now expressed by the chain SURPRISED -> SULK -> IDLE (see trigger_react).
    """
    IDLE = "IDLE"
    WALK = "WALK"
    RUN = "RUN"
    FALL = "FALL"
    DRAG = "DRAG"
    HELD = "HELD"
    LAND = "LAND"
    SLEEP = "SLEEP"
    STRETCH = "STRETCH"
    GROOM = "GROOM"
    PETTED = "PETTED"
    LOVE = "LOVE"
    SULK = "SULK"
    SURPRISED = "SURPRISED"
    PLAY = "PLAY"
    HUNT = "HUNT"
    MEOW = "MEOW"


# Which sprite-sheet ROW each state uses when drawing.
# DRAG (the cat held by the mouse) now uses the HELD art (dangling/swinging),
# NOT FALL (on its back). Every PetState maps to a valid row name — no
# KeyError/missing frames (see also the .get default in SpriteSheet).
STATE_TO_SHEET = {
    PetState.IDLE:      "SIT",
    PetState.WALK:      "WALK",
    PetState.RUN:       "RUN",
    PetState.FALL:      "FALL",
    PetState.DRAG:      "HELD",
    PetState.HELD:      "HELD",
    PetState.LAND:      "LAND",
    PetState.SLEEP:     "SLEEP",
    PetState.STRETCH:   "STRETCH",
    PetState.GROOM:     "GROOM",
    PetState.PETTED:    "PETTED",
    PetState.LOVE:      "LOVE",
    PetState.SULK:      "SULK",
    PetState.SURPRISED: "SURPRISED",
    PetState.PLAY:      "PLAY",
    PetState.HUNT:      "HUNT",
    PetState.MEOW:      "MEOW",
}


# =========================================================================== #
#  SPRITE SHEET                                                                #
# =========================================================================== #
class SpriteSheet:
    """Loading, chroma-key, slicing, trimming and scaling of the sprite sheet.

    The main public method is frames(state, facing) -> list[QPixmap].
    If the file is missing/broken or Pillow is absent, all frames will be None
    (more precisely, the dict ends up empty), and the engine switches to the
    procedural fallback.
    """

    def __init__(self, path: str = SHEET_PATH):
        self.path = path
        self.loaded = False
        # {state_name: [QPixmap, ...]} — NATURAL frames, as in the new sheet:
        # directional poses (WALK/RUN/HUNT) face RIGHT. The name _frames_left
        # is historical; the load pipeline writes here specifically (leave it).
        self._frames_left: dict[str, list[QPixmap]] = {}
        # Mirrored frames (facing LEFT) — cached on demand.
        self._frames_right: dict[str, list[QPixmap]] = {}
        # Keep the raw bytes buffers alive for the whole lifetime of the QImage
        # (the QImage(bytes,...) constructor does NOT copy the buffer — see research).
        self._byte_buffers: list[bytes] = []

        try:
            self._load()
        except Exception as exc:  # any processing error => fallback
            log(f"[SpriteSheet] Failed to process the sprite sheet: {exc}")
            self.loaded = False

    # --------------------------------------------------------------------- #
    def is_valid(self) -> bool:
        """True if the frames loaded successfully and are available."""
        return self.loaded and bool(self._frames_left)

    # --------------------------------------------------------------------- #
    def frames(self, state: PetState, facing: int = 1) -> list[QPixmap] | None:
        """Return the list of frames for a state and direction.

        The new art faces RIGHT, so the mirroring convention is flipped:
            facing == +1 (right) => natural frames WITHOUT a mirror (as in the sheet);
            facing == -1 (left)  => MIRROR (WALK/RUN/HUNT and the rest).
        Returns None if the frames are unavailable (=> procedural fallback).
        """
        if not self.is_valid():
            return None
        sheet_key = STATE_TO_SHEET.get(state, "SIT")
        if sheet_key not in self._frames_left:
            # Safety net: if a row somehow failed to load, hand back SIT,
            # so _current_pixmap always gets a valid frame.
            sheet_key = "SIT" if "SIT" in self._frames_left else None
            if sheet_key is None:
                return None
        if facing >= 0:
            # Facing right — hand back the natural frames as is.
            return self._frames_left[sheet_key]
        # Facing left — lazily build the mirrored frames and cache them.
        if sheet_key not in self._frames_right:
            self._frames_right[sheet_key] = [
                self._mirror(p) for p in self._frames_left[sheet_key]
            ]
        return self._frames_right[sheet_key]

    # --------------------------------------------------------------------- #
    @staticmethod
    def _mirror(pix: QPixmap) -> QPixmap:
        """Horizontal mirror (forward-compatible: QTransform)."""
        return pix.transformed(QTransform().scale(-1, 1),
                               Qt.TransformationMode.SmoothTransformation)

    # --------------------------------------------------------------------- #
    @staticmethod
    def _row_edges(total: int, n: int) -> list[int]:
        """Row/column edges via float rounding so the tiles cover the WHOLE
        size without drift (see research: round(i*total/n))."""
        return [round(i * total / n) for i in range(n + 1)]

    # --------------------------------------------------------------------- #
    @staticmethod
    def _detect_row_edges_from_counts(counts, total, n):
        """ROW edges from the "pink" gaps, rather than dividing by /n.

        On real art the rows rarely sit exactly at total/n: the cat may hang
        BELOW the cell boundary, and its bottom lands in the TOP of the
        neighboring frame (a strip from the neighbor is visible — exactly what
        the user complained about). Solution: from the count of opaque pixels
        in each row we find continuous BANDS of content (rows), take the (n-1)
        WIDEST gaps between them as separators and cut at their midpoints.
        We return n+1 edges or None (then we fall back to even division).
        Small isolated elements (zzz on the sleeper, sparks on the hisser)
        produce tiny gaps and do NOT count among the wide separators.
        """
        if total <= 0 or n <= 1 or len(counts) < total:
            return None
        mx = max(counts)
        if mx <= 0:
            return None
        thr = mx * 0.04  # a row is "content" if >4% of the peak value
        bands = []
        start = -1
        for y in range(total):
            if counts[y] > thr:
                if start < 0:
                    start = y
            elif start >= 0:
                bands.append((start, y - 1))
                start = -1
        if start >= 0:
            bands.append((start, total - 1))
        if len(bands) < n:
            return None  # fewer rows than expected — do not risk it, fall back
        gaps = [(bands[i][1] + 1, bands[i + 1][0] - 1)
                for i in range(len(bands) - 1)]
        if len(gaps) < n - 1:
            return None
        # The (n-1) WIDEST gaps = the real row separators.
        widest = sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:n - 1]
        edges = [0]
        for a, b in sorted(widest):
            edges.append((a + b) // 2)
        edges.append(total)
        for i in range(len(edges) - 1):
            if edges[i] >= edges[i + 1]:
                return None  # the edges must strictly increase
        return edges

    @staticmethod
    def _row_counts_pil(img, kr, kg, kb, tol_sq, step=8):
        """Count of opaque (non-background) pixels in each row (PIL)."""
        w, h = img.size
        px = img.load()
        counts = [0] * h
        for y in range(h):
            c = 0
            for x in range(0, w, step):
                p = px[x, y]
                if len(p) == 4 and p[3] <= 16:
                    continue  # already transparent
                dr = p[0] - kr
                dg = p[1] - kg
                db = p[2] - kb
                if dr * dr + dg * dg + db * db > tol_sq:
                    c += 1
            counts[y] = c
        return counts

    @staticmethod
    def _row_counts_qt(src, kr, kg, kb, tol_sq, step=8):
        """Count of opaque (non-background) pixels in each row (Qt)."""
        w, h = src.width(), src.height()
        counts = [0] * h
        for y in range(h):
            c = 0
            for x in range(0, w, step):
                col = src.pixelColor(x, y)
                if col.alpha() <= 16:
                    continue
                dr = col.red() - kr
                dg = col.green() - kg
                db = col.blue() - kb
                if dr * dr + dg * dg + db * db > tol_sq:
                    c += 1
            counts[y] = c
        return counts

    # --------------------------------------------------------------------- #
    def _load(self) -> None:
        """The full sprite-sheet processing pipeline."""
        if not os.path.isfile(self.path):
            log("[SpriteSheet] Sprite sheet not found: " + self.path)
            log("  -> Save the cat art to assets\\cat_sheet.png "
                "(4 columns x 5 rows, pink background).")
            log("  -> For now the built-in drawn cat is used.")
            self.loaded = False
            return

        if _HAS_PIL:
            ok = self._load_with_pil()
        else:
            ok = self._load_with_qt()
        if ok:
            self._apply_frame_order()
        self.loaded = ok

    # --------------------------------------------------------------------- #
    @staticmethod
    def _seal_interior(pix: QPixmap) -> QPixmap:
        """Seal the SMALL interior holes (eye pupils the chroma-key punched through) and
        snap the semi-transparent interior to opaque -- WITHOUT touching real silhouette
        gaps (the loop between the tail and the body), which must stay see-through.

        Earlier this made EVERY enclosed-transparent region opaque, which also filled
        the tail<->body gap with magenta key-residue -> a flickering purple blob. We now
        size each enclosed region: only the SMALL ones (eye-sized) are inpainted + sealed;
        large gaps are left transparent and soft. The silhouette EDGE (transparent that
        reaches the frame border) also stays soft. numpy, guarded (standalone keeps the
        soft sprite)."""
        try:
            import os
            import numpy as np
            from collections import deque
            img = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            w, h = img.width(), img.height()
            if w < 4 or h < 4:
                return pix
            bpl = img.bytesPerLine()
            ptr = img.bits()
            ptr.setsize(h * bpl)
            arr = np.frombuffer(ptr, np.uint8).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)
            a = arr[:, :, 3]

            # --- De-purple: the bright magenta key (237,48,238) leaves a DARK purple
            # residue halo around the whole silhouette / pupils / the tail<->body pocket
            # that the distance key never removed (too far from the bright key). It reads
            # as a flickering purple line. A purple pixel has BOTH R and B clearly above
            # G; no real cat colour does (orange B<G, cream/white balanced, pink nose has
            # B~=G, green eyes G high). Recolour those pixels from their nearest non-purple
            # neighbours and KEEP their alpha, so the soft edge stays but loses the tint.
            r0 = arr[:, :, 0].astype(np.int16)
            g0 = arr[:, :, 1].astype(np.int16)
            b0 = arr[:, :, 2].astype(np.int16)
            magenta = (a > 0) & ((r0 - g0) > 30) & ((b0 - g0) > 30)
            if magenta.any():
                rgb = arr[:, :, :3].astype(np.float32)
                known = (~magenta) & (a >= 40)       # trustworthy cat-colour pixels
                tofill = magenta.copy()
                for _ in range(60):
                    if not tofill.any():
                        break
                    uk = np.zeros((h, w), bool); uk[1:] = known[:-1]
                    dk = np.zeros((h, w), bool); dk[:-1] = known[1:]
                    lk = np.zeros((h, w), bool); lk[:, 1:] = known[:, :-1]
                    rk = np.zeros((h, w), bool); rk[:, :-1] = known[:, 1:]
                    ur = np.zeros((h, w, 3), np.float32); ur[1:] = rgb[:-1]
                    dr = np.zeros((h, w, 3), np.float32); dr[:-1] = rgb[1:]
                    lr = np.zeros((h, w, 3), np.float32); lr[:, 1:] = rgb[:, :-1]
                    rr = np.zeros((h, w, 3), np.float32); rr[:, :-1] = rgb[:, 1:]
                    cnt = (uk.astype(np.float32) + dk + lk + rk)
                    ssum = (np.where(uk[..., None], ur, 0) + np.where(dk[..., None], dr, 0)
                            + np.where(lk[..., None], lr, 0) + np.where(rk[..., None], rr, 0))
                    f = tofill & (cnt > 0)
                    rgb[f] = ssum[f] / cnt[f][..., None]
                    known |= f
                    tofill &= ~f
                arr[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
                # Any purple pixel the recolour never reached is isolated noise far
                # from the body (faint pink dust around a throw pose) — drop it.
                if tofill.any():
                    arr[:, :, 3] = np.where(tofill, 0, a)

            T = 128
            opaque = a >= T
            transparent = ~opaque
            if not transparent.any():
                return pix

            # "Has an opaque pixel before me" in each of the 4 directions (cumulative
            # OR). A pixel enclosed by opaque on all 4 sides is INTERIOR.
            above = np.zeros((h, w), bool); above[1:] = np.logical_or.accumulate(opaque[:-1], axis=0)
            below = np.zeros((h, w), bool); below[:-1] = np.logical_or.accumulate(opaque[1:][::-1], axis=0)[::-1]
            left = np.zeros((h, w), bool); left[:, 1:] = np.logical_or.accumulate(opaque[:, :-1], axis=1)
            right = np.zeros((h, w), bool); right[:, :-1] = np.logical_or.accumulate(opaque[:, 1:][:, ::-1], axis=1)[:, ::-1]
            interior = above & below & left & right
            candidates = transparent & interior          # enclosed see-through pixels

            # Label the enclosed see-through pixels into connected components and keep
            # only the SMALL ones (eyes). Large enclosed regions are real gaps (the
            # tail<->body loop) and MUST stay transparent, or they fill with key residue.
            small_holes = np.zeros((h, w), bool)
            if candidates.any():
                max_hole = max(90, int(w * h * 0.006))
                visited = np.zeros((h, w), bool)
                ys, xs = np.where(candidates)
                sizes = []
                for sy, sx in zip(ys.tolist(), xs.tolist()):
                    if visited[sy, sx]:
                        continue
                    comp = []
                    dq = deque(((sy, sx),))
                    visited[sy, sx] = True
                    while dq:
                        cy, cx = dq.popleft()
                        comp.append((cy, cx))
                        for dy in (-1, 0, 1):
                            for dx in (-1, 0, 1):
                                if dy == 0 and dx == 0:
                                    continue
                                ny, nx = cy + dy, cx + dx
                                if 0 <= ny < h and 0 <= nx < w and candidates[ny, nx] and not visited[ny, nx]:
                                    visited[ny, nx] = True
                                    dq.append((ny, nx))
                    sizes.append(len(comp))
                    if len(comp) <= max_hole:
                        for cy, cx in comp:
                            small_holes[cy, cx] = True
                if os.environ.get("SEAL_DBG"):
                    print(f"[seal] {w}x{h} max_hole={max_hole} comps={sorted(sizes, reverse=True)}")

            # Inpaint the SMALL holes' colour from opaque neighbours (a few dilation
            # passes -- holes are tiny, so this converges fast).
            if small_holes.any():
                rgb = arr[:, :, :3].astype(np.float32)
                known = opaque.copy()
                tofill = small_holes.copy()
                for _ in range(40):
                    if not tofill.any():
                        break
                    uk = np.zeros((h, w), bool); uk[1:] = known[:-1]
                    dk = np.zeros((h, w), bool); dk[:-1] = known[1:]
                    lk = np.zeros((h, w), bool); lk[:, 1:] = known[:, :-1]
                    rk = np.zeros((h, w), bool); rk[:, :-1] = known[:, 1:]
                    ur = np.zeros((h, w, 3), np.float32); ur[1:] = rgb[:-1]
                    dr = np.zeros((h, w, 3), np.float32); dr[:-1] = rgb[1:]
                    lr = np.zeros((h, w, 3), np.float32); lr[:, 1:] = rgb[:, :-1]
                    rr = np.zeros((h, w, 3), np.float32); rr[:, :-1] = rgb[:, 1:]
                    cnt = (uk.astype(np.float32) + dk + lk + rk)
                    ssum = (np.where(uk[..., None], ur, 0) + np.where(dk[..., None], dr, 0)
                            + np.where(lk[..., None], lr, 0) + np.where(rk[..., None], rr, 0))
                    f = tofill & (cnt > 0)
                    rgb[f] = ssum[f] / cnt[f][..., None]
                    known |= f
                    tofill &= ~f
                arr[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)

            # Snap the interior SEMI-transparent pixels (dark eye / soft fill) to opaque,
            # but only where all four neighbours are themselves substantial (>24). A soft
            # pixel bordering a real gap has a near-empty neighbour -> it stays soft, so
            # the tail<->body gap keeps clean see-through edges (no purple halo).
            ai = a.astype(np.int16)
            up = np.full((h, w), 255, np.int16); up[1:] = ai[:-1]
            dn = np.full((h, w), 255, np.int16); dn[:-1] = ai[1:]
            lt = np.full((h, w), 255, np.int16); lt[:, 1:] = ai[:, :-1]
            rt = np.full((h, w), 255, np.int16); rt[:, :-1] = ai[:, 1:]
            nb = np.minimum(np.minimum(up, dn), np.minimum(lt, rt))
            interior_semi = interior & (ai > 0) & (ai < 255) & (nb > 24)

            arr[:, :, 3] = np.where(small_holes | interior_semi, 255, a)
            return QPixmap.fromImage(img.copy())
        except Exception:
            return pix

    # --------------------------------------------------------------------- #
    def _apply_frame_order(self) -> None:
        """Keep only the frames listed in STATE_FRAME_ORDER (e.g. drop the SIT wink).

        States with no entry keep all their frames in natural order. Out-of-range
        indices are skipped defensively, and an empty result is left untouched so a
        bad table can never blank out a pose."""
        for state_name, order in STATE_FRAME_ORDER.items():
            frames = self._frames_left.get(state_name)
            if not frames:
                continue
            kept = [frames[i] for i in order if 0 <= i < len(frames)]
            if kept:
                self._frames_left[state_name] = kept

    # --------------------------------------------------------------------- #
    def _load_with_pil(self) -> bool:
        """The Pillow path: precise chroma-key + alpha-based trim."""
        img = Image.open(self.path).convert("RGBA")
        sheet_w, sheet_h = img.size
        if sheet_w < COLS or sheet_h < ROWS:
            log("[SpriteSheet] Sprite sheet is too small.")
            return False

        # We take the background color NOT from a single pixel (0,0) but by
        # averaging the corners: that way noise/a JPEG artifact/a watermark in
        # a corner will not break the whole key.
        kr, kg, kb = self._sample_key_pil(img, sheet_w, sheet_h)
        tol_sq = CHROMA_TOLERANCE * CHROMA_TOLERANCE

        col_edges = self._row_edges(sheet_w, COLS)
        # Rows are detected from the real "pink" gaps (not by dividing /ROWS):
        # this is exactly what removes the strip from the neighboring row at
        # the top of a frame.
        row_edges = self._detect_row_edges_from_counts(
            self._row_counts_pil(img, kr, kg, kb, tol_sq), sheet_h, ROWS)
        rows_auto = row_edges is not None
        if not rows_auto:
            row_edges = self._row_edges(sheet_h, ROWS)

        # First we slice and chroma-key all frames in PIL, collecting them with
        # alpha, plus the maximum height after trimming (for a single scale).
        raw_cells: dict[str, list[Image.Image]] = {}
        max_h = 1
        for state_name, r in STATE_ROWS.items():
            y0, y1 = row_edges[r], row_edges[r + 1]
            row_cells: list[Image.Image] = []
            for c in range(COLS):
                x0, x1 = col_edges[c], col_edges[c + 1]
                cell = img.crop((x0, y0, x1, y1))
                cell = self._chroma_key_pil(cell, kr, kg, kb, tol_sq)
                bbox = cell.getbbox()  # tight box around the opaque pixels
                if bbox:
                    cell = cell.crop(bbox)
                    if cell.height > max_h:
                        max_h = cell.height
                else:
                    # Fully transparent frame: insert a 1x1 placeholder so it
                    # behaves the same as in the Qt path (_autotrim_qt) and does
                    # not enter the scale loop at the full cell size.
                    cell = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                row_cells.append(cell)
            raw_cells[state_name] = row_cells

        # A single scale for ALL frames (preserving relative proportions), plus an
        # optional per-state EXTRA scale (see STATE_EXTRA_SCALE) so a pose like HELD
        # is not visually smaller than the sitting cat.
        scale = TARGET_HEIGHT / float(max_h)
        for state_name, cells in raw_cells.items():
            extra = STATE_EXTRA_SCALE.get(state_name, 1.0)
            pixmaps: list[QPixmap] = []
            for cell in cells:
                if cell.width <= 0 or cell.height <= 0:
                    # Empty frame (fully transparent) — insert a 1x1 placeholder.
                    pixmaps.append(QPixmap(1, 1))
                    continue
                new_w = max(1, round(cell.width * scale * extra))
                new_h = max(1, round(cell.height * scale * extra))
                pix = self._pil_to_pixmap(cell)
                pix = pix.scaled(
                    new_w, new_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                pix = self._seal_interior(pix)
                pixmaps.append(pix)
            self._frames_left[state_name] = pixmaps

        log("[SpriteSheet] Sprite sheet loaded (PIL): "
            f"{sheet_w}x{sheet_h}, frames={COLS}x{ROWS}, "
            f"rows={'auto' if rows_auto else 'even'}={row_edges}.")
        return True

    # --------------------------------------------------------------------- #
    @staticmethod
    def _sample_key_pil(img, w: int, h: int) -> tuple[int, int, int]:
        """Robust background color: average 5x5 blocks in each of the 4 corners
        and pick the most common corner average (numpy-free, via getpixel)."""
        block = 5
        bx = min(block, max(1, w))
        by = min(block, max(1, h))
        corners = [
            (0, 0),
            (max(0, w - bx), 0),
            (0, max(0, h - by)),
            (max(0, w - bx), max(0, h - by)),
        ]
        averages: list[tuple[int, int, int]] = []
        for ox, oy in corners:
            sr = sg = sb = n = 0
            for yy in range(oy, min(oy + by, h)):
                for xx in range(ox, min(ox + bx, w)):
                    px = img.getpixel((xx, yy))
                    sr += px[0]
                    sg += px[1]
                    sb += px[2]
                    n += 1
            if n:
                averages.append((sr // n, sg // n, sb // n))
        if not averages:
            px = img.getpixel((0, 0))
            return px[0], px[1], px[2]
        # The most common corner color (background corners usually match; an
        # artifact corner ends up in the minority and gets discarded).
        best = max(averages, key=averages.count)
        return best

    # --------------------------------------------------------------------- #
    @staticmethod
    def _chroma_key_pil(cell, kr: int, kg: int, kb: int, tol_sq: int):
        """Make the background transparent by RGB Euclidean distance (numpy-free).

        We build NOT a binary but a SOFT mask with a transition band, to remove
        the pink halo on the anti-aliased edges of the sprite:
            * dist <= inner (CHROMA_TOLERANCE)        -> alpha 0   (pure background);
            * dist >= outer (CHROMA_TOLERANCE * 2)     -> alpha 255 (cat body);
            * in between                                -> linear ramp 0..255.
        We additionally do de-spill: for partially transparent pixels we pull
        the RGB away from the key color so leftover fringe carries no pink tint.

        Image.point/eval work per-channel and do NOT handle cross-channel
        distance, so we go through getdata() (a one-time operation — fine).
        """
        inner = math.sqrt(tol_sq)            # radius of "definitely background"
        outer = inner * 2.0                  # radius of "definitely body"
        span = max(1e-6, outer - inner)      # width of the transition band

        data = list(cell.getdata())
        out = bytearray(len(data) * 4)       # we will rewrite the whole RGBA
        for i, (r, g, b, _a) in enumerate(data):
            dr = r - kr
            dg = g - kg
            db = b - kb
            dist = math.sqrt(dr * dr + dg * dg + db * db)
            if dist <= inner:
                a = 0
            elif dist >= outer:
                a = 255
            else:
                # Linear transparency ramp in the transition band.
                a = int(round((dist - inner) / span * 255.0))
            j = i * 4
            if 0 < a < 255:
                # De-spill: pull the color away from the key proportionally to
                # how much "closer to the background" the pixel is (lower alpha).
                f = a / 255.0                # 0..1: 0 = almost background, 1 = almost body
                r = int(round(kr + (r - kr) / max(f, 0.25)))
                g = int(round(kg + (g - kg) / max(f, 0.25)))
                b = int(round(kb + (b - kb) / max(f, 0.25)))
                r = 0 if r < 0 else 255 if r > 255 else r
                g = 0 if g < 0 else 255 if g > 255 else g
                b = 0 if b < 0 else 255 if b > 255 else b
            out[j] = r
            out[j + 1] = g
            out[j + 2] = b
            out[j + 3] = a
        result = Image.frombytes("RGBA", cell.size, bytes(out))
        return result

    # --------------------------------------------------------------------- #
    def _pil_to_pixmap(self, cell) -> QPixmap:
        """PIL.Image('RGBA') -> QPixmap with the correct format and byte order."""
        if cell.mode != "RGBA":
            cell = cell.convert("RGBA")
        data = cell.tobytes("raw", "RGBA")
        # Keep the buffer alive: QImage does not copy it.
        self._byte_buffers.append(data)
        qimg = QImage(data, cell.width, cell.height, cell.width * 4,
                      QImage.Format.Format_RGBA8888)
        # .copy() — a deep copy, to own the memory independently of bytes.
        return QPixmap.fromImage(qimg.copy())

    # --------------------------------------------------------------------- #
    def _load_with_qt(self) -> bool:
        """Fallback path without Pillow: chroma-key manually over QImage pixels.

        Slower, but a one-time cost at startup. Used only if PIL does not import.
        """
        src = QImage(self.path)
        if src.isNull():
            log("[SpriteSheet] QImage could not open the file.")
            return False
        src = src.convertToFormat(QImage.Format.Format_RGBA8888)
        sheet_w, sheet_h = src.width(), src.height()
        if sheet_w < COLS or sheet_h < ROWS:
            return False

        # Robust key from the corners (as in the PIL path).
        kr, kg, kb = self._sample_key_qt(src, sheet_w, sheet_h)
        tol_sq = CHROMA_TOLERANCE * CHROMA_TOLERANCE

        col_edges = self._row_edges(sheet_w, COLS)
        # Rows from the real gaps (as in the PIL path), otherwise even.
        row_edges = self._detect_row_edges_from_counts(
            self._row_counts_qt(src, kr, kg, kb, tol_sq), sheet_h, ROWS)
        rows_auto = row_edges is not None
        if not rows_auto:
            row_edges = self._row_edges(sheet_h, ROWS)

        raw_cells: dict[str, list[QImage]] = {}
        max_h = 1
        for state_name, r in STATE_ROWS.items():
            y0, y1 = row_edges[r], row_edges[r + 1]
            row_cells: list[QImage] = []
            for c in range(COLS):
                x0, x1 = col_edges[c], col_edges[c + 1]
                cell = src.copy(QRect(x0, y0, x1 - x0, y1 - y0))
                cell = self._chroma_key_qt(cell, kr, kg, kb, tol_sq)
                cell = self._autotrim_qt(cell)
                if cell.height() > max_h:
                    max_h = cell.height()
                row_cells.append(cell)
            raw_cells[state_name] = row_cells

        scale = TARGET_HEIGHT / float(max_h)
        for state_name, cells in raw_cells.items():
            extra = STATE_EXTRA_SCALE.get(state_name, 1.0)
            pixmaps: list[QPixmap] = []
            for cell in cells:
                if cell.isNull() or cell.width() <= 0 or cell.height() <= 0:
                    pixmaps.append(QPixmap(1, 1))
                    continue
                new_w = max(1, round(cell.width() * scale * extra))
                new_h = max(1, round(cell.height() * scale * extra))
                pix = QPixmap.fromImage(cell).scaled(
                    new_w, new_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                pix = self._seal_interior(pix)
                pixmaps.append(pix)
            self._frames_left[state_name] = pixmaps

        log("[SpriteSheet] Sprite sheet loaded (Qt fallback): "
            f"{sheet_w}x{sheet_h}, "
            f"rows={'auto' if rows_auto else 'even'}={row_edges}.")
        return True

    # --------------------------------------------------------------------- #
    @staticmethod
    def _sample_key_qt(src: QImage, w: int, h: int) -> tuple[int, int, int]:
        """Robust background color for the Qt path: average 5x5 blocks in the
        4 corners, pick the most common one (see _sample_key_pil)."""
        block = 5
        bx = min(block, max(1, w))
        by = min(block, max(1, h))
        corners = [
            (0, 0),
            (max(0, w - bx), 0),
            (0, max(0, h - by)),
            (max(0, w - bx), max(0, h - by)),
        ]
        averages: list[tuple[int, int, int]] = []
        for ox, oy in corners:
            sr = sg = sb = n = 0
            for yy in range(oy, min(oy + by, h)):
                for xx in range(ox, min(ox + bx, w)):
                    col = src.pixelColor(xx, yy)
                    sr += col.red()
                    sg += col.green()
                    sb += col.blue()
                    n += 1
            if n:
                averages.append((sr // n, sg // n, sb // n))
        if not averages:
            col = src.pixelColor(0, 0)
            return col.red(), col.green(), col.blue()
        return max(averages, key=averages.count)

    # --------------------------------------------------------------------- #
    @staticmethod
    def _chroma_key_qt(cell: QImage, kr: int, kg: int, kb: int,
                       tol_sq: int) -> QImage:
        """Chroma-key over QImage pixels (fallback without PIL).

        As in the PIL path, we build a SOFT mask with a transition band
        (inner..outer) + de-spill, to remove the pink halo on the anti-aliased
        edges, rather than just binary-zeroing the exact background color.
        """
        cell = cell.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = cell.width(), cell.height()
        inner = math.sqrt(tol_sq)
        outer = inner * 2.0
        span = max(1e-6, outer - inner)
        for y in range(h):
            for x in range(w):
                col = cell.pixelColor(x, y)
                r, g, b = col.red(), col.green(), col.blue()
                dr = r - kr
                dg = g - kg
                db = b - kb
                dist = math.sqrt(dr * dr + dg * dg + db * db)
                if dist <= inner:
                    cell.setPixelColor(x, y, QColor(0, 0, 0, 0))
                elif dist >= outer:
                    continue  # cat body — leave it as is (alpha=255)
                else:
                    a = int(round((dist - inner) / span * 255.0))
                    # De-spill the color away from the key.
                    f = a / 255.0
                    r = int(round(kr + (r - kr) / max(f, 0.25)))
                    g = int(round(kg + (g - kg) / max(f, 0.25)))
                    b = int(round(kb + (b - kb) / max(f, 0.25)))
                    r = 0 if r < 0 else 255 if r > 255 else r
                    g = 0 if g < 0 else 255 if g > 255 else g
                    b = 0 if b < 0 else 255 if b > 255 else b
                    cell.setPixelColor(x, y, QColor(r, g, b, a))
        return cell

    # --------------------------------------------------------------------- #
    @staticmethod
    def _autotrim_qt(cell: QImage) -> QImage:
        """Trim the transparent margins of a QImage by the alpha box."""
        w, h = cell.width(), cell.height()
        min_x, min_y, max_x, max_y = w, h, -1, -1
        for y in range(h):
            for x in range(w):
                if cell.pixelColor(x, y).alpha() > 0:
                    if x < min_x:
                        min_x = x
                    if x > max_x:
                        max_x = x
                    if y < min_y:
                        min_y = y
                    if y > max_y:
                        max_y = y
        if max_x < 0:  # fully transparent frame
            return QImage(1, 1, QImage.Format.Format_RGBA8888)
        return cell.copy(QRect(min_x, min_y,
                               max_x - min_x + 1, max_y - min_y + 1))


# =========================================================================== #
#  PET ENGINE                                                                  #
# =========================================================================== #
class PetEngine(QObject):
    """The simulation core: a behavior state machine + fall/walk physics.

    The position is stored as the FEET ANCHOR (feet anchor): fx — the center on
    X, fy — the floor-contact point on Y (the bottom of the sprite). This way a
    pose change of a different height neither "sinks" nor "suspends" the cat
    (see research: anchor = feet).

    Signals:
        frame_updated(QPoint topLeft, QPixmap) — every tick: where to place the
            window (top-left) and which frame to show.
        state_changed(object) — on an FSM state change (a PetState is passed).
    """

    frame_updated = pyqtSignal(QPoint, QPixmap)
    state_changed = pyqtSignal(object)

    def __init__(self, sprites: SpriteSheet | None = None, parent=None):
        super().__init__(parent)
        self.sprites = sprites if sprites is not None else SpriteSheet()

        # --- Position / physics (in screen pixels and px/s) -------------- #
        self.fx = 200.0          # X of the feet center
        self.fy = 200.0          # Y of the floor contact (bottom of the sprite)
        self.vx = 0.0            # horizontal velocity (px/s)
        self.vy = 0.0            # vertical velocity (px/s)
        self.facing = -1         # -1 left, +1 right

        # --- Screen work area (without the taskbar) ---------------------- #
        self.work_area = self._compute_workarea(QPoint(int(self.fx), int(self.fy)))
        # Initially place the cat on the floor at the center of the work area.
        self.fx = self.work_area.center().x()
        self.fy = float(self.work_area.bottom())

        # --- Surfaces the cat can stand on ------------------------------- #
        # The GROUND (work-area bottom) always supports the cat at any x. In
        # addition the host app can register LEDGES via set_platforms() — e.g. the
        # FocusGuard window's top edge — so the cat can land on and walk along the
        # top of the window. _rest_y is the y of the surface it currently rests on.
        self.platforms: list[QRect] = []
        self._rest_y = float(self.work_area.bottom())

        # --- FSM ---------------------------------------------------------- #
        self.state = PetState.IDLE
        self._state_time = 0.0       # how many seconds in the current state
        self._state_duration = self._rand(IDLE_MIN_S, IDLE_MAX_S)
        self._idle_accum = 0.0       # idle accumulator -> SLEEP

        # --- Animation ---------------------------------------------------- #
        self._frame_index = 0
        self._frame_accum = 0.0      # animation time accumulator (s)

        # --- Dragging / throw --------------------------------------------- #
        self._drag_offset = QPoint(0, 0)        # cursor - topLeft at grab time
        self._prev_state_before_drag = PetState.IDLE
        # History of recent mouse positions: (x, y, t) for the throw inertia.
        self._mouse_history: list[tuple[float, float, float]] = []

        # --- External reaction trigger (thread-safe) --------------------- #
        # trigger_react() only RAISES a flag; the actual transition happens on
        # the GUI thread in _tick(). This way it is safe to poke from the YOLO
        # thread (preferably via a queued signal/slot). The reaction is the
        # chain SURPRISED -> SULK -> IDLE.
        self._react_pending = False
        self._react_pending_ms = REACT_DURATION_MS

        # Petting flag (click/tap on the cat). Also deferred — consumed on a
        # grounded non-DRAG tick, so a click mid-flight/while grabbed is not
        # swallowed and does not poke the GUI from a foreign context.
        self._pet_pending = False

        # Calm mode: when on, the cat just sits and blinks (no wandering / running /
        # hunting / auto-sleep). Toggled from the right-click menu.
        self._calm = False

        # --- Game loop ---------------------------------------------------- #
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()

        # Current frame size (to recompute topLeft) — updated every tick.
        self._cur_w = 80
        self._cur_h = 80

    # --------------------------------------------------------------------- #
    #  PUBLIC API                                                           #
    # --------------------------------------------------------------------- #
    def start(self) -> None:
        """Start the game loop."""
        self._clock.start()
        self._enter_state(PetState.IDLE)
        self._timer.start()

    def stop(self) -> None:
        """Stop the game loop."""
        self._timer.stop()

    def resume(self) -> None:
        """Resume the loop after stop() WITHOUT resetting the FSM/pose.

        Used when the desktop pet window is re-shown: we keep the current state and
        just restart the tick clock (start() would snap the cat back to IDLE)."""
        if not self._timer.isActive():
            self._clock.restart()
            self._timer.start()

    def set_workarea(self, rect: QRect) -> None:
        """Set the work area explicitly (e.g. on an external monitor change)."""
        if rect is not None and rect.isValid():
            self.work_area = rect

    def set_platforms(self, rects) -> None:
        """Register stand-on LEDGES (their TOP edge supports the cat), in screen px.

        The host window calls this with its frame geometry so the cat can land on
        and walk along the window's top edge ("perch on the app"). Pass [] to clear.
        Thread note: call on the GUI thread (it only stores the list)."""
        self.platforms = [r for r in (rects or [])
                          if r is not None and r.isValid()]

    def trigger_react(self, duration_ms: int = REACT_DURATION_MS) -> None:
        """External force-trigger of the distraction reaction (called by the YOLOv8 module).

        The method name is kept for compatibility (the YOLO module pokes it),
        but the reaction is now the chain SURPRISED ('!') -> SULK (pouting) ->
        IDLE, not the old aggressive REACT.

        THREAD SAFETY: the method does NOT touch the GUI directly — it only sets
        a flag. It is preferable to call it via a queued signal/slot (or
        QMetaObject.invokeMethod with Qt.ConnectionType.QueuedConnection), so the
        actual transition happens on the GUI thread inside _tick().

        The flag is NOT lost during DRAG or in flight (FALL): it stays armed and
        the reaction plays on the first grounded non-DRAG tick (i.e. after the
        cat is released and/or after landing). From SLEEP — it wakes the cat.

        duration_ms is treated as the duration of the SULK phase (for
        compatibility with the old API); the SURPRISED phase is always short
        (SURPRISED_DURATION_S).
        """
        self._react_pending = True
        self._react_pending_ms = max(200, int(duration_ms))

    def pet(self) -> None:
        """Petting on click/tap. If the cat is on the floor it squints with
        pleasure (PETTED ~2 s) -> IDLE; if it is airborne/held the flag waits
        for landing.

        Like trigger_react, it only RAISES a flag — the transition is done on
        the GUI thread in _tick(). PetWindow calls this on a TAP (a click
        without noticeable dragging), distinguishing it from a real DRAG.
        """
        self._pet_pending = True

    def set_calm(self, on: bool) -> None:
        """Calm mode on/off: the cat just sits and blinks (no wandering / running /
        auto-sleep). Settles it into IDLE right away unless it's held/falling."""
        self._calm = bool(on)
        if on and self.state not in (PetState.DRAG, PetState.HELD, PetState.FALL):
            self._enter_state(PetState.IDLE)

    def go_sleep(self) -> None:
        """Put the cat to sleep now (curled up). Ignored while held/falling."""
        if self.state not in (PetState.DRAG, PetState.HELD, PetState.FALL):
            self._enter_state(PetState.SLEEP)

    def begin_drag(self, global_pos: QPoint) -> None:
        """Begin dragging: remember the click offset relative to the FEET ANCHOR.

        We store the offset as (cursor - feet_anchor), not (cursor - top_left):
        the anchor does not depend on the frame size, so a width/height change of
        the DRAG pose relative to the dragged-in pose does not "jerk" the grab
        point by a frame.
        """
        self._prev_state_before_drag = self.state
        # dx,dy = cursor relative to the feet anchor (fx — center, fy — bottom).
        self._drag_offset = QPoint(int(round(global_pos.x() - self.fx)),
                                   int(round(global_pos.y() - self.fy)))
        self._mouse_history = [(float(global_pos.x()), float(global_pos.y()),
                                time.monotonic())]
        self.vx = 0.0
        self.vy = 0.0
        self._enter_state(PetState.DRAG)

    def drag_to(self, global_pos: QPoint) -> None:
        """Drag the cat by the mouse (feet anchor = cursor - grab offset)."""
        if self.state is not PetState.DRAG:
            return
        # We restore the feet anchor directly — without involving _cur_w/_cur_h,
        # so the grab point is stable across a frame-size change.
        self.fx = float(global_pos.x() - self._drag_offset.x())
        self.fy = float(global_pos.y() - self._drag_offset.y())
        # Accumulate history for the throw-inertia computation (cap 6 samples).
        self._mouse_history.append((float(global_pos.x()), float(global_pos.y()),
                                    time.monotonic()))
        if len(self._mouse_history) > 6:
            self._mouse_history.pop(0)
        # Recompute the work area for the monitor where the cursor is now.
        self._refresh_workarea()

    def end_drag(self) -> None:
        """Release the cat: compute the throw inertia and go into FALL."""
        if self.state is not PetState.DRAG:
            return
        self.vx, self.vy = self._compute_throw_velocity()
        self._enter_state(PetState.FALL)

    # --------------------------------------------------------------------- #
    #  WORK AREA / MONITORS                                                  #
    # --------------------------------------------------------------------- #
    @staticmethod
    def _compute_workarea(at: QPoint) -> QRect:
        """The available area (without the taskbar) of the screen under point at.

        screenAt() returns None in the gap between monitors / off-screen — then
        we fall back to primaryScreen (see research: multi-monitor gaps).
        """
        screen = QGuiApplication.screenAt(at)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            # No screens at all (offscreen platform) — a sensible default.
            return QRect(0, 0, 800, 600)
        return screen.availableGeometry()

    def _refresh_workarea(self) -> None:
        """Refresh the work area for the current feet position."""
        self.work_area = self._compute_workarea(
            QPoint(int(self.fx), int(self.fy)))

    # --------------------------------------------------------------------- #
    #  UTILITIES                                                             #
    # --------------------------------------------------------------------- #
    @staticmethod
    def _rand(a: float, b: float) -> float:
        return random.uniform(a, b)

    def _top_left(self) -> QPoint:
        """Window top-left from the feet anchor and the current frame size."""
        return QPoint(int(round(self.fx - self._cur_w / 2.0)),
                      int(round(self.fy - self._cur_h)))

    def _floor_y(self) -> float:
        """The single floor coordinate.

        In Qt QRect.bottom() == top+height-1 (the INCLUSIVE bottom row). We use
        it as the feet-contact point BOTH for the "floor is gone" check and for
        snapping to the floor — the same value, without an asymmetric gap.
        """
        return float(self.work_area.bottom())

    def _ground_y(self) -> float:
        """The bottom floor of the work area (always supports the cat, full width)."""
        return float(self.work_area.bottom())

    def _supported_at(self, x: float, surface_y: float) -> bool:
        """True if there is a real surface at (x, surface_y): the ground at any x,
        or a registered ledge whose top is ~surface_y and whose span contains x."""
        if surface_y >= self._ground_y() - 0.5:
            return True
        for p in self.platforms:
            if p.left() <= x <= p.right() and abs(p.top() - surface_y) <= 2.0:
                return True
        return False

    def _settle_or_fall(self) -> bool:
        """For grounded states: if the current support is gone (walked off a ledge
        or the floor moved), drop into FALL and return False; otherwise snap the
        feet to the resting surface and return True."""
        # If the cat is resting on the GROUND and the work area later SHRANK (taskbar
        # appeared / resolution drop), the live floor is now ABOVE the stale _rest_y.
        # Pull a grounded cat up to the new floor so it never gets pinned below the
        # taskbar (a ledge rest_y is < ground, so this never disturbs a perch).
        if self._rest_y > self._ground_y():
            self._rest_y = self._ground_y()
        if (not self._supported_at(self.fx, self._rest_y)
                or self.fy < self._rest_y - FLOOR_EPS):
            self._enter_state(PetState.FALL)
            return False
        self.fy = self._rest_y
        return True

    def _sync_frame_size(self) -> None:
        """Update _cur_w/_cur_h FROM THE CURRENT frame (before physics and anchoring).

        Fixes the 1-frame "pop"/jitter on a pose change of a different height and
        the miss of the X wall clamp on a frame-width change/mirroring: previously
        the sizes were updated only in _emit_frame (after physics), so the whole
        tick computed by the previous-frame size.
        """
        pix = self._current_pixmap()
        if pix is not None and not pix.isNull():
            self._cur_w = pix.width()
            self._cur_h = pix.height()

    def _compute_throw_velocity(self) -> tuple[float, float]:
        """Throw inertia from the last two mouse-history samples.

        Protection against division by 0 and a max-speed cap (see research).
        Returns (vx, vy) in px/s.
        """
        if len(self._mouse_history) < 2:
            return 0.0, 0.0
        x2, y2, t2 = self._mouse_history[-1]
        x1, y1, t1 = self._mouse_history[-2]
        dt = t2 - t1
        if dt <= 1e-4:
            return 0.0, 0.0
        vx = (x2 - x1) / dt * THROW_SCALE
        vy = (y2 - y1) / dt * THROW_SCALE
        # Speed cap, so a fast flick does not hurl the cat across three monitors.
        speed = math.hypot(vx, vy)
        if speed > THROW_MAX_SPEED:
            k = THROW_MAX_SPEED / speed
            vx *= k
            vy *= k
        return vx, vy

    # --------------------------------------------------------------------- #
    #  STATE MANAGEMENT (enter)                                              #
    # --------------------------------------------------------------------- #
    # Speed for each directional (moving) state.
    _MOVE_SPEED = {
        PetState.WALK: WALK_SPEED,
        PetState.RUN: RUN_SPEED,
        PetState.HUNT: HUNT_SPEED,
    }
    # Duration ranges of the directional states.
    _MOVE_DURATION = {
        PetState.WALK: (WALK_MIN_S, WALK_MAX_S),
        PetState.RUN: (RUN_MIN_S, RUN_MAX_S),
        PetState.HUNT: (HUNT_MIN_S, HUNT_MAX_S),
    }
    # Stationary grounded states (vx=vy=0, on the floor, by timer -> IDLE).
    _GROUNDED_STATES = frozenset({
        PetState.GROOM, PetState.PLAY, PetState.STRETCH, PetState.MEOW,
        PetState.LOVE, PetState.PETTED, PetState.SULK, PetState.LAND,
    })

    def _enter_state(self, new_state: PetState, duration_s: float | None = None) -> None:
        """Transition into a new state: reset timers/frame + on_enter logic."""
        self.state = new_state
        self._state_time = 0.0
        self._frame_index = 0
        self._frame_accum = 0.0

        if new_state is PetState.IDLE:
            self.vx = 0.0
            self.vy = 0.0
            self._state_duration = duration_s or self._rand(IDLE_MIN_S, IDLE_MAX_S)

        elif new_state in self._MOVE_SPEED:
            # WALK / RUN / HUNT: horizontal movement, turn around at the walls.
            self.vy = 0.0
            self._idle_accum = 0.0
            self.facing = random.choice((-1, 1))
            self.vx = self.facing * self._MOVE_SPEED[new_state]
            lo, hi = self._MOVE_DURATION[new_state]
            self._state_duration = duration_s or self._rand(lo, hi)

        elif new_state is PetState.FALL:
            self._idle_accum = 0.0
            # vx/vy are already set (a throw) or zero (stepping off the edge) — leave them.

        elif new_state in (PetState.DRAG, PetState.HELD):
            self.vx = 0.0
            self.vy = 0.0

        elif new_state is PetState.SURPRISED:
            # Short '!' startle; then _update auto-transitions into SULK.
            self.vx = 0.0
            self.vy = 0.0
            self._idle_accum = 0.0
            self._state_duration = duration_s or SURPRISED_DURATION_S

        elif new_state in self._GROUNDED_STATES:
            # Stationary scenes on the floor. LAND and PETTED have their own
            # durations, the rest use the common random range.
            self.vx = 0.0
            self.vy = 0.0
            self._idle_accum = 0.0
            if duration_s is not None:
                self._state_duration = duration_s
            elif new_state is PetState.LAND:
                self._state_duration = LAND_DURATION_S
            elif new_state is PetState.PETTED:
                self._state_duration = PETTED_DURATION_S
            else:
                self._state_duration = self._rand(GROUNDED_MIN_S, GROUNDED_MAX_S)

        elif new_state is PetState.SLEEP:
            self.vx = 0.0
            self.vy = 0.0
            self._state_duration = duration_s or self._rand(SLEEP_MIN_S, SLEEP_MAX_S)

        self.state_changed.emit(new_state)

    # --------------------------------------------------------------------- #
    #  MAIN TICK                                                            #
    # --------------------------------------------------------------------- #
    def _tick(self) -> None:
        """One game-loop frame: dt -> deferred reaction -> FSM -> render."""
        # Real dt with protection against lag spikes.
        dt = self._clock.restart() / 1000.0
        if dt <= 0.0:
            dt = TICK_MS / 1000.0
        if dt > DT_CLAMP:
            dt = DT_CLAMP

        self._state_time += dt

        # We compute the CURRENT frame size AT THE START of the tick (not after
        # emit), so all physics/clamps/feet anchor use the current, not the
        # previous-frame height/width. Otherwise, on the tick after a pose change
        # of a different height the window is placed with the wrong size (a
        # 1-frame "pop"), and the X wall clamp misses by the half-width difference.
        self._sync_frame_size()

        # Deferred external triggers (handled on the GUI thread).
        # We do NOT lose them during DRAG or in flight (FALL): the flag stays
        # armed and fires on the first grounded/non-DRAG tick. This way an
        # alarm/petting raised while the cat is held or flying still plays.
        # The distraction reaction has priority over petting.
        grounded = self.fy >= self._floor_y() - 1.0
        deferrable = (self.state is PetState.DRAG
                      or (self.state is PetState.FALL and not grounded))
        if self._react_pending and not deferrable:
            # The chain SURPRISED -> SULK -> IDLE. We take the SULK duration from
            # _react_pending_ms (for compatibility with the old API). We also
            # clear the petting flag: the reaction matters more and overrides it.
            self._react_pending = False
            self._pet_pending = False
            self._enter_state(PetState.SURPRISED, duration_s=SURPRISED_DURATION_S)
        elif self._pet_pending and not deferrable:
            # Click/tap on the cat — petting (squints) -> IDLE.
            self._pet_pending = False
            self._enter_state(PetState.PETTED)

        # Update behavior/physics for the state.
        self._update_state(dt)

        # Advance the animation frame by the state FPS.
        self._advance_animation(dt)

        # Render.
        self._emit_frame()

    # --------------------------------------------------------------------- #
    #  PER-STATE UPDATE (update)                                             #
    # --------------------------------------------------------------------- #
    def _update_state(self, dt: float) -> None:
        st = self.state
        if st is PetState.IDLE:
            self._update_idle(dt)
        elif st in (PetState.WALK, PetState.RUN, PetState.HUNT):
            self._update_move(dt)
        elif st is PetState.FALL:
            self._update_fall(dt)
        elif st in (PetState.DRAG, PetState.HELD):
            pass  # the position is driven by the mouse via drag_to()
        elif st is PetState.SURPRISED:
            self._update_surprised(dt)
        elif st is PetState.SLEEP:
            self._update_sleep(dt)
        elif st in self._GROUNDED_STATES:
            self._update_grounded(dt)
        else:
            # Safety net: an unknown/new state behaves like IDLE.
            self._update_grounded(dt)

    # A name in IDLE_CHOICE_WEIGHTS -> the PetState we leave IDLE for.
    _IDLE_CHOICE_TO_STATE = {
        "WALK": PetState.WALK,
        "RUN": PetState.RUN,
        "HUNT": PetState.HUNT,
        "GROOM": PetState.GROOM,
        "STRETCH": PetState.STRETCH,
        "MEOW": PetState.MEOW,
    }

    # ---- IDLE ----------------------------------------------------------- #
    def _update_idle(self, dt: float) -> None:
        """Sitting on the floor. If the ground is gone from under the feet — we
        fall. On the timer we weighted-pick the next activity (WALK/RUN/HUNT/
        GROOM/PLAY/STRETCH/MEOW or stay in IDLE); after long inactivity -> SLEEP.

        LOVE/PETTED/SURPRISED/SULK/HELD/LAND are NOT chosen here — they are
        triggered by interactions (click/alarm) and physics (landing)."""
        # The cat should stand on the floor; if it is above the floor (e.g. the
        # monitor changed) — let it fall. The comparison and snap use the SAME
        # floor coordinate (_floor_y) — without an asymmetric gap, otherwise the
        # cat could "sag" a couple of pixels below the taskbar after a work-area shift.
        if not self._settle_or_fall():
            return

        # Calm mode: just sit and blink — no wandering, no auto-sleep.
        if self._calm:
            if self._state_time >= self._state_duration:
                self._state_time = 0.0
                self._state_duration = self._rand(IDLE_MIN_S, IDLE_MAX_S)
            return

        self._idle_accum += dt
        if self._idle_accum >= SLEEP_AFTER_IDLE_S:
            self._enter_state(PetState.SLEEP)
            return

        if self._state_time >= self._state_duration:
            choice = self._weighted_choice(IDLE_CHOICE_WEIGHTS)
            next_state = self._IDLE_CHOICE_TO_STATE.get(choice)
            if next_state is not None:
                self._enter_state(next_state)
            else:
                # Stay in IDLE, but recharge the decision timer.
                self._state_time = 0.0
                self._state_duration = self._rand(IDLE_MIN_S, IDLE_MAX_S)

    # ---- WALK / RUN / HUNT (moving) ------------------------------------- #
    def _update_move(self, dt: float) -> None:
        """Common update for WALK/RUN/HUNT: walk along the floor, turn around at
        the walls (updating facing => mirror the frame), by timer -> IDLE."""
        self.fx += self.vx * dt

        # Turn around at the screen walls only while on the GROUND. On a ledge the
        # cat is allowed to walk off the edge (support check below makes it fall).
        on_ground = self._rest_y >= self._ground_y() - 0.5
        if on_ground:
            left = self.work_area.left() + self._cur_w / 2.0
            right = self.work_area.right() - self._cur_w / 2.0
            if self.fx <= left:
                self.fx = left
                self.facing = 1
                self.vx = abs(self.vx) * self.facing
            elif self.fx >= right:
                self.fx = right
                self.facing = -1
                self.vx = abs(self.vx) * self.facing

        # Stick to the resting surface; if support is gone (walked off a ledge or
        # the floor moved) — fall.
        if not self._settle_or_fall():
            return

        if self._state_time >= self._state_duration:
            self._enter_state(PetState.IDLE)

    # ---- FALL ----------------------------------------------------------- #
    def _update_fall(self, dt: float) -> None:
        """Gravity with terminal velocity + anti-tunneling via subticks.

        We integrate over PHYSICS_SUBTICKS small steps and after EACH one check
        floor collisions, so a lag spike does not "tunnel" the cat through.

        Anti-tunneling: the main safeguard is the dt clamp (DT_CLAMP in _tick).
        Additionally we clamp the FULL per-frame displacement (not per sub-step)
        to max_step — so the clamp really limits the per-frame jump rather than
        being dead code with too large a threshold. We scale the per-frame clamp
        by the effective dt so the speed recovers on the following frames.
        """
        floor_y = self._floor_y()
        left = self.work_area.left() + self._cur_w / 2.0
        right = self.work_area.right() - self._cur_w / 2.0
        # The real per-frame displacement ceiling: no more than a fraction of sprite height.
        max_step = max(4.0, self._cur_h * MAX_STEP_RATIO)

        # Gravity is integrated over the FULL dt (the velocity), then we compute
        # the total per-frame displacement and, if needed, scale it so that
        # |dx|,|dy| <= max_step. The resulting displacement is split into subticks.
        self.vy += GRAVITY * dt
        if self.vy > TERMINAL_VELOCITY:
            self.vy = TERMINAL_VELOCITY

        frame_dx = self.vx * dt
        frame_dy = self.vy * dt
        # A single scale by the largest component — so the per-frame clamp does
        # not distort the throw direction.
        biggest = max(abs(frame_dx), abs(frame_dy))
        if biggest > max_step:
            k = max_step / biggest
            frame_dx *= k
            frame_dy *= k

        sub_dx = frame_dx / PHYSICS_SUBTICKS
        sub_dy = frame_dy / PHYSICS_SUBTICKS
        landed = False
        for _ in range(PHYSICS_SUBTICKS):
            prev_fy = self.fy
            self.fx += sub_dx
            self.fy += sub_dy

            # Walls: clamp + bounce/turn on X.
            if self.fx <= left:
                self.fx = left
                self.vx = -self.vx * 0.8
                self.facing = 1
                sub_dx = self.vx * dt / PHYSICS_SUBTICKS
            elif self.fx >= right:
                self.fx = right
                self.vx = -self.vx * 0.8
                self.facing = -1
                sub_dx = self.vx * dt / PHYSICS_SUBTICKS

            # Landing: among the surfaces this sub-step crossed while descending —
            # any registered LEDGE top within the cat's x-span, plus the GROUND —
            # the first one hit is the highest (smallest y). Check EVERY sub-step.
            land_y = None
            if self.vy > 0:
                for p in self.platforms:
                    top = float(p.top())
                    if (p.left() <= self.fx <= p.right()
                            and top < floor_y
                            and prev_fy <= top <= self.fy):
                        if land_y is None or top < land_y:
                            land_y = top
            if self.fy >= floor_y and (land_y is None or floor_y < land_y):
                land_y = floor_y
            if land_y is not None:
                self.fy = land_y
                self._rest_y = land_y
                on_ground = land_y >= floor_y - 0.5
                # Bounce only on the ground; a ledge catch just settles.
                if on_ground and FLOOR_BOUNCE > 0.0 and self.vy > 1.5 * FPS:
                    self.vy = -self.vy * FLOOR_BOUNCE
                    # Recompute the vertical sub-step from the NEW (upward) velocity,
                    # mirroring the wall branches — otherwise the stale downward
                    # sub_dy on the next sub-step cancels the bounce immediately.
                    sub_dy = self.vy * dt / PHYSICS_SUBTICKS
                else:
                    self.vy = 0.0
                    landed = True
                    break

        # A monitor change in flight is possible — refresh the work area.
        self._refresh_workarea()

        if landed:
            self.fy = self._rest_y
            # Land straight into IDLE — the brief LAND crouch sprite right before
            # landing looked glitchy, so it's skipped per user request.
            self._enter_state(PetState.IDLE)

    # ---- SURPRISED (distraction reaction, 1st link of the chain) -------- #
    def _update_surprised(self, dt: float) -> None:
        """Short '!' startle. When the duration elapses we auto-transition into
        SULK (pouting), which then goes into IDLE on its own (see
        _update_grounded). We enter SURPRISED only while on the floor (the
        trigger in _tick is deferred while the cat is held/flying). If support is
        gone (the monitor changed / the ledge moved) — we fall."""
        if not self._settle_or_fall():
            return
        if self._state_time >= self._state_duration:
            # 2nd link of the chain: pout for exactly _react_pending_ms (compat).
            self._enter_state(PetState.SULK,
                              duration_s=self._react_pending_ms / 1000.0)

    # ---- GROUNDED (stationary scenes on the floor) ---------------------- #
    def _update_grounded(self, dt: float) -> None:
        """Common update for GROOM/PLAY/STRETCH/MEOW/LOVE/PETTED/SULK/LAND:
        stand still (vx=vy=0), pressed to the floor; if the floor is gone — fall;
        by timer -> IDLE. SULK closes the distraction reaction chain into IDLE."""
        if not self._settle_or_fall():
            return
        if self._state_time >= self._state_duration:
            self._enter_state(PetState.IDLE)

    # ---- SLEEP ---------------------------------------------------------- #
    def _update_sleep(self, dt: float) -> None:
        """Sleeping curled up. On timeout we wake into IDLE."""
        if not self._settle_or_fall():
            return
        if self._state_time >= self._state_duration:
            self._enter_state(PetState.IDLE)

    # --------------------------------------------------------------------- #
    @staticmethod
    def _weighted_choice(weights: dict[str, float]) -> str:
        """Weighted random choice (normalized by the sum of the weights)."""
        total = sum(weights.values())
        if total <= 0:
            return next(iter(weights))
        roll = random.uniform(0, total)
        acc = 0.0
        for key, w in weights.items():
            acc += w
            if roll <= acc:
                return key
        return next(iter(weights))

    # --------------------------------------------------------------------- #
    #  ANIMATION / RENDER                                                   #
    # --------------------------------------------------------------------- #
    def _advance_animation(self, dt: float) -> None:
        """Advance the frame index by the FPS of the current state."""
        sheet_key = STATE_TO_SHEET.get(self.state, "SIT")
        fps = STATE_FPS.get(self.state.value, STATE_FPS.get(sheet_key, 4))
        if fps <= 0:
            return
        self._frame_accum += dt
        step = 1.0 / fps
        while self._frame_accum >= step:
            self._frame_accum -= step
            self._frame_index += 1

    def _current_pixmap(self) -> QPixmap:
        """The current frame: from the sprite sheet or the procedural fallback."""
        frames = None
        if self.sprites is not None and self.sprites.is_valid():
            frames = self.sprites.frames(self.state, self.facing)
        if frames:
            idx = self._frame_index % len(frames)
            return frames[idx]
        # Fallback — draw the procedural cat.
        return self._fallback_pixmap()

    def _emit_frame(self) -> None:
        """Build the frame, update the sizes and emit frame_updated."""
        pix = self._current_pixmap()
        if pix is None or pix.isNull():
            pix = self._fallback_pixmap()
        self._cur_w = pix.width()
        self._cur_h = pix.height()
        self.frame_updated.emit(self._top_left(), pix)

    # --------------------------------------------------------------------- #
    #  PROCEDURAL FALLBACK CAT (when the sprite sheet is missing/broken)    #
    # --------------------------------------------------------------------- #
    def _fallback_pixmap(self) -> QPixmap:
        """A simple orange cat via QPainter on a transparent background.

        The poses depend on the state (sitting / walking / on its back / hissing
        / sleeping), animated slightly by the frame index. We cache by
        (state, facing, phase) so we do not redraw every tick.
        """
        size = max(60, TARGET_HEIGHT)
        phase = self._frame_index % 4
        cache_key = (self.state, self.facing, phase)
        if not hasattr(self, "_fb_cache"):
            self._fb_cache: dict = {}
        cached = self._fb_cache.get(cache_key)
        if cached is not None:
            return cached

        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = size / 2.0
        body_w = size * 0.5
        body_h = size * 0.42
        # The base "ground" inside the frame — the bottom.
        ground = size * 0.92

        p.setPen(QPen(FALLBACK_OUTLINE, max(2.0, size * 0.02)))

        if self.state is PetState.SLEEP:
            # Curled up: an oval + a tail arc.
            p.setBrush(QBrush(FALLBACK_FUR))
            p.drawEllipse(int(cx - body_w * 0.7), int(ground - body_h * 0.9),
                          int(body_w * 1.4), int(body_h * 0.9))
            p.setBrush(QBrush(FALLBACK_FUR_DARK))
            p.drawEllipse(int(cx - body_w * 0.3), int(ground - body_h * 0.7),
                          int(body_w * 0.5), int(body_h * 0.45))
            # "z z z"
            p.setPen(QPen(FALLBACK_OUTLINE, 2))
            for i in range(3):
                zx = cx + body_w * 0.6 + i * size * 0.06
                zy = ground - body_h - i * size * 0.08
                p.drawText(int(zx), int(zy), "z")

        elif self.state in (PetState.FALL, PetState.DRAG, PetState.HELD):
            # On its back / dangling: an oval body + paws up (the phase wiggles
            # the paws). FALL — on its back; DRAG/HELD are visually close
            # (dangling) — for the fallback we use the same pose.
            p.setBrush(QBrush(FALLBACK_FUR))
            p.drawEllipse(int(cx - body_w * 0.7), int(ground - body_h),
                          int(body_w * 1.4), int(body_h))
            p.setBrush(QBrush(FALLBACK_FUR_DARK))
            wig = (phase - 1.5) * size * 0.04
            for sx in (-0.4, -0.15, 0.15, 0.4):
                px = cx + sx * body_w
                p.drawEllipse(int(px - size * 0.05),
                              int(ground - body_h - size * 0.12 + wig),
                              int(size * 0.1), int(size * 0.14))

        elif self.state in (PetState.SURPRISED, PetState.SULK):
            # Arched back + bristling fur (startle/offended). Above the cat we
            # draw '!' for SURPRISED and a gloomy mood (lowered brows) for SULK;
            # both poses reuse the old "alarmed" frame.
            p.setBrush(QBrush(FALLBACK_FUR))
            arch_y = ground - body_h * 1.2
            p.drawChord(int(cx - body_w * 0.8), int(arch_y),
                        int(body_w * 1.6), int(body_h * 1.6), 0, 180 * 16)
            p.setPen(QPen(FALLBACK_OUTLINE, 2))
            for i in range(6):
                fx_ = cx - body_w * 0.7 + i * body_w * 0.28
                p.drawLine(int(fx_), int(arch_y + body_h * 0.2),
                           int(fx_), int(arch_y - size * 0.08))
            # Dot eyes.
            p.setBrush(QBrush(FALLBACK_EYE))
            p.drawEllipse(int(cx - size * 0.12), int(arch_y + body_h * 0.3),
                          int(size * 0.05), int(size * 0.05))
            p.drawEllipse(int(cx + size * 0.07), int(arch_y + body_h * 0.3),
                          int(size * 0.05), int(size * 0.05))
            if self.state is PetState.SURPRISED:
                p.drawText(int(cx - size * 0.02), int(arch_y - size * 0.04), "!")

        else:
            # A sitting/standing cat, face along facing. Covers IDLE, the moving
            # states (WALK/RUN/HUNT) and the grounded scenes (GROOM/PLAY/STRETCH/
            # MEOW/LOVE/PETTED/LAND) — for the fallback a common sitting pose is enough.
            self._draw_fallback_sitting(p, cx, ground, body_w, body_h, size, phase)

        p.end()
        self._fb_cache[cache_key] = pix
        return pix

    def _draw_fallback_sitting(self, p: QPainter, cx: float, ground: float,
                               body_w: float, body_h: float, size: float,
                               phase: int) -> None:
        """Draw a sitting/walking cat (for IDLE/WALK)."""
        # Body.
        p.setBrush(QBrush(FALLBACK_FUR))
        body_top = ground - body_h
        p.drawEllipse(int(cx - body_w / 2), int(body_top),
                      int(body_w), int(body_h))
        # Head.
        head_r = size * 0.22
        head_cx = cx + self.facing * body_w * 0.1
        head_cy = body_top - head_r * 0.6
        p.drawEllipse(int(head_cx - head_r), int(head_cy - head_r),
                      int(head_r * 2), int(head_r * 2))
        # Ears (triangles).
        p.setBrush(QBrush(FALLBACK_FUR_DARK))
        ear = head_r * 0.7
        for sgn in (-1, 1):
            ex = head_cx + sgn * head_r * 0.6
            ey = head_cy - head_r * 0.7
            p.drawPolygon(*[
                QPoint(int(ex - ear * 0.5), int(ey + ear * 0.3)),
                QPoint(int(ex + ear * 0.5), int(ey + ear * 0.3)),
                QPoint(int(ex), int(ey - ear * 0.6)),
            ])
        # Eyes (looking along facing).
        p.setBrush(QBrush(FALLBACK_EYE))
        eye_dx = self.facing * head_r * 0.25
        for sgn in (-1, 1):
            ex = head_cx + eye_dx + sgn * head_r * 0.4
            p.drawEllipse(int(ex - size * 0.025),
                          int(head_cy - size * 0.02),
                          int(size * 0.05), int(size * 0.06))
        # Tail (wags with the phase; more noticeable for WALK).
        p.setPen(QPen(FALLBACK_FUR_DARK, max(3.0, size * 0.03)))
        tail_base_x = cx - self.facing * body_w * 0.45
        wig = (phase - 1.5) * size * 0.05
        p.drawLine(int(tail_base_x), int(ground - body_h * 0.3),
                   int(tail_base_x - self.facing * size * 0.18),
                   int(ground - body_h * 0.9 + wig))
        # Paws while moving — two short dashes with a phase shift.
        if self.state in (PetState.WALK, PetState.RUN, PetState.HUNT):
            p.setPen(QPen(FALLBACK_OUTLINE, max(3.0, size * 0.03)))
            step = (phase % 2) * size * 0.04
            for sgn, off in ((-1, step), (1, size * 0.04 - step)):
                lx = cx + sgn * body_w * 0.25
                p.drawLine(int(lx), int(ground - size * 0.04),
                           int(lx), int(ground + off * 0.0 + size * 0.0))
                p.drawLine(int(lx), int(ground - size * 0.02),
                           int(lx + self.facing * off), int(ground))


# =========================================================================== #
#  SPEECH BUBBLE (desktop pet)                                                #
# =========================================================================== #
class _SpeechBubble(QWidget):
    """A small frameless, click-through speech bubble shown above the desktop cat.

    It is a SEPARATE top-level window (not part of the alpha-masked cat window),
    so it can float above the sprite without affecting the cat's click-through.
    It never takes focus or steals clicks (WA_TransparentForMouseEvents), so the
    desktop stays fully usable while the cat talks."""

    _PAD = 10
    _MAX_TEXT_W = 240

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._text = ""
        self._font = QFont(_THEME_FONTS.get("body", "Segoe UI"), 10)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, msec: int = 2200) -> None:
        self._text = str(text or "")
        if not self._text:
            self.hide()
            return
        fm = QFontMetrics(self._font)
        self._draw_text = fm.elidedText(self._text, Qt.TextElideMode.ElideRight,
                                        self._MAX_TEXT_W)
        tw = min(fm.horizontalAdvance(self._draw_text), self._MAX_TEXT_W)
        w = tw + self._PAD * 2 + 4
        h = fm.height() + self._PAD * 2 + 8  # extra 8 px for the downward tail
        self.resize(w, h)
        self.show()
        self.raise_()
        self.update()
        self._timer.start(max(500, int(msec)))

    def paintEvent(self, event) -> None:
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        body_h = h - 8
        p.setPen(QPen(QColor(_THEME_COLORS["accent"]), 1))
        p.setBrush(QColor(_THEME_COLORS["elevated"]))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, body_h - 1), 9, 9)
        # Downward tail toward the cat.
        cx = w / 2.0
        path = QPainterPath()
        path.moveTo(cx - 7, body_h - 1)
        path.lineTo(cx + 7, body_h - 1)
        path.lineTo(cx, body_h + 7)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(path, QColor(_THEME_COLORS["elevated"]))
        p.setPen(QColor(_THEME_COLORS["text"]))
        p.setFont(self._font)
        p.drawText(QRectF(0, 0, w, body_h), Qt.AlignmentFlag.AlignCenter,
                   getattr(self, "_draw_text", self._text))
        p.end()


# =========================================================================== #
#  TIME PILL (desktop pet) — remaining session time near the cat               #
# =========================================================================== #
class _TimePill(QWidget):
    """A small, unobtrusive pill above the desktop cat showing the remaining time.

    Apple-Watch-ish stacked layout: the phase (FOCUS / BREAK) on top, the MM:SS
    countdown below. A separate click-through window like the speech bubble, so it
    never blocks the desktop or the cat."""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._phase = ""
        self._time = "--:--"
        self._f_small = QFont(_THEME_FONTS.get("body", "Segoe UI"), 7)
        self._f_small.setBold(True)
        self._f_big = QFont(_THEME_FONTS.get("body", "Segoe UI"), 14)
        self._f_big.setBold(True)
        self.resize(74, 44)

    def set_time(self, remaining_sec, phase: str) -> None:
        if remaining_sec is None:
            self.hide()
            return
        m, s = divmod(max(0, int(remaining_sec)), 60)
        self._time = f"{m:02d}:{s:02d}"
        self._phase = {"focus": "FOCUS", "break": "BREAK"}.get(
            phase, str(phase).upper())
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        # Accent for focus, a calmer success tint for the break.
        accent = (_THEME_COLORS.get("success", "#34D399") if self._phase == "BREAK"
                  else _THEME_COLORS.get("accent", "#A855F7"))
        p.setPen(QPen(QColor(accent), 1))
        p.setBrush(QColor(_THEME_COLORS.get("elevated", "#2E2E44")))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)
        # Phase (small, top) + time (big, bottom) — stacked.
        p.setPen(QColor(accent))
        p.setFont(self._f_small)
        p.drawText(QRectF(0, 4, w, 14), Qt.AlignmentFlag.AlignCenter, self._phase)
        p.setPen(QColor(_THEME_COLORS.get("text", "#ECEFF4")))
        p.setFont(self._f_big)
        p.drawText(QRectF(0, 16, w, h - 18), Qt.AlignmentFlag.AlignCenter, self._time)
        p.end()


# =========================================================================== #
#  PET WINDOW (the view)                                                      #
# =========================================================================== #
class PetWindow(QWidget):
    """A frameless transparent always-on-top window with the cat.

    It only renders what PetEngine.frame_updated sends, and handles the mouse
    (dragging / throw / right-click menu). It owns the engine itself and starts
    it in start().

    Signals:
        go_home — the user asked (right-click) to send the pet back into the app.
        dropped(QPoint) — a mouse drag of the cat was released at this global point
            (used by the host to drop the cat back into the app's Pet card).
    """

    go_home = pyqtSignal()
    dropped = pyqtSignal(QPoint)

    def __init__(self, parent=None, sprites=None):
        super().__init__(parent)
        # Window flags BEFORE show(): otherwise transparency won't turn on on Windows.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keeps the window out of the taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Showing the cat must NOT steal activation — otherwise showing it mid-drag
        # (when the user pulls it out of the app card) pulls focus away from the
        # window whose child still owns the in-flight mouse grab.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        # QLabel shows the current frame.
        self._label = QLabel(self)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label.move(0, 0)

        # Engine + connections. Reuse a pre-loaded sprite sheet when given (so the
        # first drag-out doesn't stall ~2-3s re-processing the sheet).
        self.engine = PetEngine(sprites=sprites, parent=self)
        self.engine.frame_updated.connect(self.on_frame)
        # Speak when the cat reacts: a phrase on petting (PETTED) and on a
        # distraction startle (SURPRISED). We listen to the real state change so
        # the bubble is always in sync with what the cat is actually doing.
        self.engine.state_changed.connect(self._on_state_changed)

        # Speech bubble (a separate click-through window floated above the cat).
        self._bubble = _SpeechBubble()
        # Remaining-time pill shown above the cat during a focus/break session.
        self._time_pill = _TimePill()

        # FIXED-SIZE CANVAS. The window is sized once to fit the LARGEST sprite and
        # never resized again: every frame is composed bottom-center into this fixed
        # canvas and the window only MOVES. Resizing a translucent always-on-top
        # window every frame (+ re-masking) leaves a one-frame ghost during a fast
        # fall — the "two cats before landing" artifact. A constant size + move
        # eliminates it. The click-through mask is refreshed only when the POSE
        # changes (tracked via _mask_dirty), not on every animation frame.
        self._canvas_w, self._canvas_h = self._compute_canvas_size()
        self.resize(self._canvas_w, self._canvas_h)
        self._label.resize(self._canvas_w, self._canvas_h)
        self._mask_dirty = True
        self._last_facing = None     # re-mask when the cat turns (mirrored silhouette)
        # Global geometry of the actually-drawn cat within the canvas (updated each
        # frame) so the speech bubble / time pill sit on the CAT, not the canvas edge.
        self._cat_cx = 0
        self._cat_top = 0
        self._cat_bottom = 0

        # Whether the simulation has been started at least once (so showEvent
        # RESUMES the loop instead of leaving a re-shown cat frozen).
        self._engine_running = False

        self._dragging = False
        # We distinguish a TAP (a click on the cat without noticeable movement)
        # from a DRAG. We remember the press point and whether a real drag began.
        self._press_pos = QPoint(0, 0)        # global point of the LMB press
        self._drag_started = False            # have we crossed the threshold -> real drag
        self._TAP_THRESHOLD = 6               # px: less than this is a tap, not a drag

    # --------------------------------------------------------------------- #
    def _compute_canvas_size(self) -> tuple[int, int]:
        """The fixed window size: big enough for the LARGEST sprite frame (+ pad).

        Falls back to a generous default if the sheet is invalid (procedural cat)."""
        mw, mh = 0, 0
        sprites = getattr(self.engine, "sprites", None)
        if sprites is not None and sprites.is_valid():
            for frames in sprites._frames_left.values():
                for p in frames:
                    if p.width() > mw:
                        mw = p.width()
                    if p.height() > mh:
                        mh = p.height()
        if mw <= 0 or mh <= 0:
            mw, mh = 320, 200       # procedural fallback is ~150 square
        return mw + 8, mh + 8

    # --------------------------------------------------------------------- #
    def start(self) -> None:
        """Show the window and start the simulation."""
        self._engine_running = True
        self.show()
        self.engine.start()

    # --------------------------------------------------------------------- #
    def on_frame(self, top_left: QPoint, pix: QPixmap) -> None:
        """Engine tick slot: compose the frame into the FIXED canvas and move.

        The window is never resized — the sprite is drawn bottom-center into a
        constant-size transparent canvas and the window is moved so that canvas
        bottom-center lands on the engine's feet anchor. This removes the fast-fall
        ghost. The click-through mask is refreshed only when the pose changes."""
        if pix is None or pix.isNull():
            return
        # Feet anchor (global) from the engine's emitted top-left + sprite size.
        feet_x = top_left.x() + pix.width() / 2.0
        feet_y = top_left.y() + pix.height()

        # Compose the sprite bottom-center into the fixed canvas.
        canvas = QPixmap(self._canvas_w, self._canvas_h)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        dx = (self._canvas_w - pix.width()) // 2
        dy = self._canvas_h - pix.height()
        painter.drawPixmap(dx, dy, pix)
        painter.end()
        self._label.setPixmap(canvas)

        # A facing flip (wall turn while walking / bouncing) mirrors the sprite
        # without a STATE change, so the silhouette moves — refresh the mask then too.
        facing = getattr(self.engine, "facing", 1)
        if facing != self._last_facing:
            self._last_facing = facing
            self._mask_dirty = True

        # Click-through mask: only recompute on a pose/facing change (not every
        # animation frame) — far less churn on the layered window, and a per-frame
        # width wobble within a pose doesn't matter for click-through.
        if self._mask_dirty:
            self._mask_dirty = False
            self._apply_clean_mask(canvas)

        # Move so the canvas bottom-center sits on the feet anchor.
        self.move(int(round(feet_x - self._canvas_w / 2.0)),
                  int(round(feet_y - self._canvas_h)))

        # Remember where the actual cat is (global) for the bubble / time pill.
        self._cat_cx = int(round(feet_x))
        self._cat_top = int(round(feet_y - pix.height()))
        self._cat_bottom = int(round(feet_y))

        # Keep the time pill + speech bubble glued to the cat as it moves.
        if self._time_pill.isVisible() or self._bubble.isVisible():
            self._reposition_overlays()

    # --------------------------------------------------------------------- #
    def _apply_clean_mask(self, canvas: QPixmap) -> None:
        """Install a NON-dithered click-through mask from the canvas alpha.

        QPixmap.mask() DITHERS the semi-transparent (anti-aliased edge) pixels into
        a stipple that reads as "see-through" on the live translucent window. We
        threshold the alpha to binary FIRST (so the alpha-mask has nothing to
        dither) — the DISPLAYED sprite keeps its smooth alpha untouched, only the
        click-through region is cleaned up. Falls back to the default mask if numpy
        is unavailable (standalone pet_engine)."""
        try:
            import numpy as np
            img = canvas.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            w, h = img.width(), img.height()
            if w > 0 and h > 0:
                bpl = img.bytesPerLine()
                ptr = img.bits()
                ptr.setsize(h * bpl)
                arr = np.frombuffer(ptr, np.uint8).reshape(h, bpl)
                a = arr[:, 3:w * 4:4]
                arr[:, 3:w * 4:4] = np.where(a >= MASK_ALPHA_CUTOFF, 255, 0)
                self.setMask(QBitmap.fromImage(img.createAlphaMask()))
                return
        except Exception:
            pass
        mask = canvas.mask()
        if not mask.isNull():
            self.setMask(mask)
        else:
            self.clearMask()

    # --------------------------------------------------------------------- #
    def say(self, text: str, msec: int = 2200) -> None:
        """Show a speech bubble above the cat for msec ms (no-op for empty text)."""
        if not text:
            return
        self._bubble.show_text(text, msec)
        self._reposition_overlays()

    def set_time(self, remaining_sec, phase: str) -> None:
        """Show/update the remaining-time pill above the cat (None hides it)."""
        self._time_pill.set_time(remaining_sec, phase)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        """Stack the time pill + speech bubble as one column so they never overlap.

        Above the cat by default (nearest = pill, then bubble); if there isn't room
        above (cat near the top of the screen), flip the whole stack BELOW the cat."""
        cx, gap = self._cat_cx, 4
        pill, bub = self._time_pill, self._bubble
        pv, bv = pill.isVisible(), bub.isVisible()
        ph = pill.height() if pv else 0
        bh = bub.height() if bv else 0
        need = (ph + gap if pv else 0) + (bh + gap if bv else 0)
        if self._cat_top - need >= 0:
            # Above the cat: pill closest to the head, bubble above the pill.
            y = self._cat_top
            if pv:
                y -= gap + ph
                pill.move(cx - pill.width() // 2, y)
            if bv:
                y -= gap + bh
                bub.move(cx - bub.width() // 2, y)
        else:
            # Not enough room above -> stack below the cat instead.
            y = self._cat_bottom + gap
            if pv:
                pill.move(cx - pill.width() // 2, y)
                y += ph + gap
            if bv:
                bub.move(cx - bub.width() // 2, y)

    def _on_state_changed(self, state) -> None:
        """React verbally to the cat's own state changes (petting / distraction)
        and mark the click-through mask dirty so it refreshes for the new pose."""
        self._mask_dirty = True
        if state is PetState.PETTED:
            self.say(_say_for("petting"))
        elif state is PetState.SURPRISED:
            # The desktop cat doesn't know the distraction KIND, so pick a line
            # from the phone/away/posture pools at random.
            self.say(_say_for(random.choice(["phone", "away", "posture"])))

    # --------------------------------------------------------------------- #
    #  MOUSE                                                                 #
    # --------------------------------------------------------------------- #
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # We do NOT start a drag yet: we wait to see if the movement exceeds
            # the tap threshold. This way a single click on the cat becomes
            # petting (PETTED), not a "grab with a zero throw".
            self._dragging = True
            self._drag_started = False
            self._press_pos = event.globalPosition().toPoint()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            pos = event.globalPosition().toPoint()
            if not self._drag_started:
                # Crossed the threshold -> this is a real drag: only now do we
                # grab the cat (begin_drag fixes the offset from the current
                # position, not from the press position — without a jerk).
                delta = pos - self._press_pos
                if abs(delta.x()) + abs(delta.y()) >= self._TAP_THRESHOLD:
                    self._drag_started = True
                    self.engine.begin_drag(pos)
            else:
                self.engine.drag_to(pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            if self._drag_started:
                # It was a real drag -> release with throw inertia (as before), and
                # tell the host where it was dropped (so it can go home if dropped
                # over the app's Pet card).
                self._drag_started = False
                self.engine.end_drag()
                self.dropped.emit(event.globalPosition().toPoint())
            else:
                # There was almost no movement -> it's a TAP: pet the cat.
                self.engine.pet()
        super().mouseReleaseEvent(event)

    # --------------------------------------------------------------------- #
    def _show_context_menu(self, global_pos: QPoint) -> None:
        """Right-click menu: send the pet back into the app.

        The old 'Shoo' that quit the whole application was surprising; right-click
        now offers 'Send home to the app' (go_home -> the host hides this window and
        restores the in-app pet card). A single, unambiguous action so the cat can
        never end up hidden in both places."""
        menu = QMenu(self)
        act_home = QAction("Send home to the app", self)
        act_home.triggered.connect(self._go_home)
        menu.addAction(act_home)
        menu.addSeparator()
        if getattr(self.engine, "_calm", False):
            act_calm = QAction("Let it wander", self)
            act_calm.triggered.connect(lambda: self.engine.set_calm(False))
        else:
            act_calm = QAction("Calm down (just sit)", self)
            act_calm.triggered.connect(lambda: self.engine.set_calm(True))
        menu.addAction(act_calm)
        act_sleep = QAction("Sleep", self)
        act_sleep.triggered.connect(self.engine.go_sleep)
        menu.addAction(act_sleep)
        menu.exec(global_pos)

    def _go_home(self) -> None:
        """Send the pet back into the app: notify the host (it hides this window
        and shows the in-app card). The host owns the hide so the location state
        stays consistent."""
        self.go_home.emit()

    # --------------------------------------------------------------------- #
    def hideEvent(self, event) -> None:
        # Pause the simulation + hide the bubble/time pill while the cat is off the
        # desktop (home / hidden) — otherwise the 60 FPS engine keeps running on an
        # unseen window, wasting CPU and drifting the cat off-screen.
        for w in (self._bubble, self._time_pill):
            try:
                w.hide()
            except Exception:
                pass
        try:
            self.engine.stop()
        except Exception:
            pass
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        # Resume the loop when the cat comes back to the desktop, preserving its
        # current pose/position (resume() does NOT reset the FSM like start()).
        super().showEvent(event)
        if self._engine_running:
            try:
                self.engine.resume()
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        # Tear down the separate bubble / time-pill windows so they don't linger.
        for w in (self._bubble, self._time_pill):
            try:
                w.close()
            except Exception:
                pass
        super().closeEvent(event)


# =========================================================================== #
#  ENTRY POINT (demo)                                                         #
# =========================================================================== #
def main() -> int:
    app = QApplication(sys.argv)

    window = PetWindow()
    window.start()

    # Distraction-reaction demo: every ~9 seconds we play the chain
    # SURPRISED -> SULK -> IDLE. In the real application this is poked by the
    # YOLOv8 module via a queued signal/slot from its own thread.
    react_timer = QTimer()
    react_timer.setInterval(9000)
    react_timer.timeout.connect(lambda: window.engine.trigger_react())
    react_timer.start()

    # Petting-on-click demo: periodically we poke pet() (PETTED -> IDLE), as if
    # the user clicked on the cat.
    pet_timer = QTimer()
    pet_timer.setInterval(13000)
    pet_timer.timeout.connect(lambda: window.engine.pet())
    pet_timer.start()

    # A light parade of the new poses: we click through a few states once so the
    # whole new art is visible (demo only).
    showcase = [PetState.RUN, PetState.HUNT, PetState.GROOM, PetState.PLAY,
                PetState.STRETCH, PetState.MEOW, PetState.LOVE]

    def _showcase_next() -> None:
        if not showcase:
            return
        st = showcase.pop(0)
        # Do not interrupt dragging/flight — we show only on the floor.
        if window.engine.state not in (PetState.DRAG, PetState.HELD,
                                       PetState.FALL):
            window.engine._enter_state(st)

    showcase_timer = QTimer()
    showcase_timer.setInterval(3500)
    showcase_timer.timeout.connect(_showcase_next)
    showcase_timer.start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
