# -*- coding: utf-8 -*-
"""
widget_pet.py — PetWidget: the embedded dashboard companion cat.

This is the SAME 16-pose sprite cat as the floating desktop pet: it draws frames
from the shared sprite sheet (assets/cat_sheet.png) via pet_engine.SpriteSheet,
so the cat looks identical everywhere in the app.

Unlike the floating pet (pet_engine.PetWindow), the embedded widget has NO
physics — it is a small, lightweight mood/state machine for a companion that
sits in the dashboard's pet card:
    * IDLE shows SIT (with occasional blinks/grooming for gentle life);
    * after a while idle it drifts to GROOM or SLEEP;
    * react(kind) plays short reaction scenes (SURPRISED->SULK / LOVE->IDLE);
    * say(text) pops a rounded speech bubble above the cat (auto-hides);
    * clicking the cat pets it (PETTED + floating hearts + the petted signal);
    * set_palette(fur_key) tints the cat toward a COLORS skin (Pet-Room).

Public API (unchanged contract — callers need no changes):
    react(kind)                — 'phone'|'distract'|'gaze'|'posture'|'praise'|
                                 'love'|'levelup'|'combo'|'idle' -> a reaction scene.
    say(text, msec=2500)       — show a speech bubble (positional msec supported).
    set_palette(fur_key)       — apply a Pet-Room skin (a key from COLORS).
    set_mood(mood)             — bias the idle pose (happy/neutral/sad/sleeping).
    petted (signal)            — emitted when the cat is clicked (petted).

Robustness: if the sprite sheet is missing/invalid the widget falls back to a
simple drawn cat so it never crashes. All timers stop on hide/close/destroy.
"""

import random

from PyQt6.QtCore import Qt, QTimer, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPainterPath, QPixmap, QBrush, QPen,
)
from PyQt6.QtWidgets import QWidget, QGraphicsColorizeEffect

from theme import COLORS, qcolor_rgba, FONTS

# The shared sprite cat. PetState + SpriteSheet are reused from the floating
# pet's engine, so both cats render from the exact same art and pose set.
from pet_engine import PetState, SpriteSheet


# --------------------------------------------------------------------------- #
# A lightweight, physics-free mood/state machine for the EMBEDDED companion.   #
# Each "scene" is a PetState plus how many milliseconds to hold it before the  #
# cat drifts back to IDLE.                                                     #
# --------------------------------------------------------------------------- #

# Animation speed (frames per second) per pose. Mirrors pet_engine's feel but
# is intentionally gentle for an embedded companion. .get() with a default keeps
# a missing key from ever crashing.
STATE_FPS = {
    PetState.IDLE:      3,
    PetState.GROOM:     6,
    PetState.SLEEP:     2,
    PetState.STRETCH:   6,
    PetState.PLAY:      8,
    PetState.MEOW:      6,
    PetState.LOVE:      7,
    PetState.PETTED:    5,
    PetState.SULK:      4,
    PetState.SURPRISED: 9,
}

# Reaction map: detector/game event -> an ordered list of (PetState, hold_ms)
# scenes to play before returning to IDLE.
NEGATIVE_SCENE = [(PetState.SURPRISED, 600), (PetState.SULK, 2500)]
POSITIVE_SCENE = [(PetState.LOVE, 1600), (PetState.PETTED, 900)]

REACTIONS = {
    # Distraction / posture events -> startle then sulk.
    "phone":    NEGATIVE_SCENE,
    "distract": NEGATIVE_SCENE,
    "gaze":     NEGATIVE_SCENE,
    "posture":  NEGATIVE_SCENE,
    # Praise / love / progress events -> happy.
    "praise":   POSITIVE_SCENE,
    "love":     POSITIVE_SCENE,
    "levelup":  POSITIVE_SCENE,
    "combo":    POSITIVE_SCENE,
    # 'idle' is a benign cue (e.g. a session start) — just sit calmly.
    "idle":     [(PetState.IDLE, 0)],
}

# Mood -> the pose the cat idles in (set_mood biases the resting scene).
MOOD_IDLE_STATE = {
    "happy":    PetState.IDLE,
    "neutral":  PetState.IDLE,
    "sad":      PetState.SULK,
    "sleeping": PetState.SLEEP,
}

# Per-pose DISPLAY scale for the embedded card cat (the widget normalizes every
# pose to its box, so wide curled poses look oversized). The sleeping (curled) cat
# is rendered smaller so it doesn't dominate the Pet card.
STATE_BOX_SCALE = {
    PetState.SLEEP: 0.68,
}

# Fallback procedural cat colors (only used if the sprite sheet is unavailable).
FALLBACK_FUR = QColor(235, 140, 60)
FALLBACK_FUR_DARK = QColor(200, 110, 40)
FALLBACK_OUTLINE = QColor(60, 40, 25)
FALLBACK_EYE = QColor(40, 30, 20)


# --------------------------------------------------------------------------- #
# SHARED SPRITE SHEET. Built once for the whole process and reused by every    #
# embedded PetWidget (the floating pet keeps its own instance — that's fine,   #
# the load is cheap relative to having two different cats).                    #
# --------------------------------------------------------------------------- #
_SHARED_SHEET: SpriteSheet | None = None


def _shared_sheet() -> SpriteSheet:
    """Return the process-wide SpriteSheet, building it on first use.

    Never raises: SpriteSheet swallows its own load errors and reports
    is_valid()==False, in which case the widget uses the procedural fallback.
    """
    global _SHARED_SHEET
    if _SHARED_SHEET is None:
        try:
            _SHARED_SHEET = SpriteSheet()
        except Exception:
            _SHARED_SHEET = None
    return _SHARED_SHEET


def shared_sheet() -> SpriteSheet:
    """Public accessor for the process-wide SpriteSheet (so the floating desktop pet
    can reuse the already-loaded sheet instead of paying the ~2-3s load again)."""
    return _shared_sheet()


class PetWidget(QWidget):
    """Embedded companion cat: the shared sprite cat + speech bubble + petting."""

    petted = pyqtSignal()           # emitted on click (the cat was petted)
    dragged_out = pyqtSignal(object)   # global QPoint: user started dragging the cat out
    drag_out_move = pyqtSignal(object) # global QPoint: drag continues
    drag_out_drop = pyqtSignal(object) # global QPoint: dropped (release)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Transparent background, so the cat sits nicely on any card backdrop.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(160, 200)

        # --- Sprite source + per-(state,facing) frame cache ---------------- #
        self._sheet = _shared_sheet()
        self._facing = 1                       # the embedded cat always faces right
        self._frame_cache: dict = {}           # (state, facing) -> [QPixmap]
        self._scaled_cache: dict = {}          # (state, facing, idx, w, h) -> QPixmap

        # --- Mood / state machine ----------------------------------------- #
        self._mood = "happy"                   # see MOOD_IDLE_STATE
        self._idle_state = PetState.IDLE       # the pose the cat rests in
        self._state = PetState.IDLE            # the pose currently shown
        self._frame_index = 0                  # current animation frame
        self._scene_queue: list = []           # remaining (state, hold_ms) scenes
        self._idle_elapsed_ms = 0              # how long we've sat idle
        self._drift_at_ms = self._next_drift_ms()

        # --- Speech bubble ------------------------------------------------- #
        self._bubble_text = ""                 # '' => hidden

        # --- Palette / skin ------------------------------------------------ #
        self._palette_override = None          # COLORS key chosen in Pet-Room
        self._colorize = None                  # QGraphicsColorizeEffect (lazy)

        # --- Petting hearts (particles) ----------------------------------- #
        self._hearts = []                      # list of {x, y, life}

        # --- Accessibility: reduce motion --------------------------------- #
        # When on, the cat holds a static pose (no frame animation, no idle
        # drift). Reaction pose CHANGES still happen (they convey meaning, not
        # motion noise), but the cat won't constantly shuffle frames.
        self._reduce_motion = False

        # --- Timers -------------------------------------------------------- #
        # Animation tick (~8 fps): advances frames and drives the mood machine.
        self._anim_ms = 125
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(self._anim_ms)

        # Auto-hide for the speech bubble.
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)

        # Hearts particle animation (only runs while hearts are alive).
        self._hearts_timer = QTimer(self)
        self._hearts_timer.timeout.connect(self._update_hearts)

        # Stop every timer when the widget goes away (no leftover timers).
        self.destroyed.connect(lambda *_: self._stop_timers())

    # ------------------------------------------------------------------ #
    # PUBLIC API                                                          #
    # ------------------------------------------------------------------ #
    def say(self, text: str, msec: int = 2500) -> None:
        """Show a speech bubble with text; it hides itself after msec ms.

        Callers pass msec positionally (e.g. say('Hi', 4000)); long text is
        clipped/elided in paintEvent so the bubble never crashes or overflows.
        """
        self._bubble_text = "" if text is None else str(text)
        try:
            msec = int(msec)
        except (TypeError, ValueError):
            msec = 2500
        self._bubble_timer.start(max(300, msec))
        self.update()

    def react(self, kind: str) -> None:
        """React to a detector/game event by playing a short pose scene.

        kind in {'phone','distract','gaze','posture'} -> SURPRISED -> SULK -> IDLE;
        kind in {'praise','love','levelup','combo'}    -> LOVE -> PETTED -> IDLE;
        'idle' sits calmly; anything unknown defaults to the SURPRISED->SULK scene.
        """
        scene = REACTIONS.get(kind, NEGATIVE_SCENE)
        self._play_scene(list(scene))

    def set_mood(self, mood: str) -> None:
        """Bias the idle/resting pose (happy/neutral/sad/sleeping).

        Cheap and optional: happy/neutral rest in SIT, sad rests in SULK,
        sleeping rests in SLEEP. Unknown moods fall back to a calm IDLE.
        """
        self._mood = mood
        self._idle_state = MOOD_IDLE_STATE.get(mood, PetState.IDLE)
        # If we're currently resting (no scene queued), adopt the new pose now.
        if not self._scene_queue:
            self._enter_state(self._idle_state)

    def set_reduce_motion(self, on: bool) -> None:
        """Accessibility: hold a static pose instead of animating frames/drifting.

        Reaction scenes (e.g. react/petting pose changes) still apply, but the
        per-frame animation and idle wandering are suppressed for motion-sensitive
        users. Hearts particles are also skipped while reduce-motion is on."""
        self._reduce_motion = bool(on)
        self.update()

    def set_palette(self, fur_key: str) -> None:
        """Apply the Pet-Room skin: tint the sprite toward COLORS[fur_key].

        Uses a low-strength QGraphicsColorizeEffect so the cat is recognizably
        recolored without losing its shading. Never crashes on an unknown key.
        """
        self._palette_override = fur_key
        color = QColor(COLORS.get(fur_key, COLORS["accent"]))
        if not color.isValid():
            color = QColor(COLORS["accent"])
        if self._colorize is None:
            self._colorize = QGraphicsColorizeEffect(self)
            self._colorize.setStrength(0.25)
            self.setGraphicsEffect(self._colorize)
        self._colorize.setColor(color)
        self.update()

    # ------------------------------------------------------------------ #
    # MOOD / STATE MACHINE                                                #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _next_drift_ms() -> int:
        """How long to sit idle before drifting to GROOM/SLEEP (ms)."""
        return random.randint(6000, 12000)

    def _play_scene(self, scenes: list) -> None:
        """Queue a list of (PetState, hold_ms) scenes and start the first one."""
        if not scenes:
            self._enter_state(self._idle_state)
            return
        state, hold = scenes[0]
        self._scene_queue = scenes[1:]
        self._enter_state(state)
        # _scene_hold_ms counts down in _tick; 0/None => stick until reacted to.
        self._scene_hold_ms = max(0, int(hold))
        self._scene_elapsed_ms = 0

    def _enter_state(self, state: PetState) -> None:
        """Switch the visible pose and reset the animation frame."""
        self._state = state
        self._frame_index = 0
        self.update()

    def _tick(self) -> None:
        """~8 fps heartbeat: advance the frame and drive the mood machine."""
        # 1) Advance the animation frame for the current pose (skip if reduce-motion).
        frames = self._frames_for(self._state)
        if frames and not self._reduce_motion:
            fps = STATE_FPS.get(self._state, 4)
            # frames advanced per tick = fps * tick_seconds (rounded up to >=1
            # for moving poses so they animate; rounding keeps it ~fps).
            self._frame_index = (self._frame_index + 1) % max(1, len(frames))
            # Slow poses (low fps) should not flip every tick: gate by fps.
            # We approximate by only advancing every Nth tick for slow states.
            self._anim_gate = getattr(self, "_anim_gate", 0) + 1
            ticks_per_frame = max(1, round((1000.0 / self._anim_ms) / max(1, fps)))
            if self._anim_gate % ticks_per_frame != 0:
                # Undo the advance we did above to respect the slower fps.
                self._frame_index = (self._frame_index - 1) % max(1, len(frames))

        # 2) Drive scene timing / idle drift.
        if self._scene_queue or getattr(self, "_scene_hold_ms", 0) > 0:
            self._scene_elapsed_ms = getattr(self, "_scene_elapsed_ms", 0) + self._anim_ms
            if self._scene_elapsed_ms >= getattr(self, "_scene_hold_ms", 0):
                # Advance to the next queued scene, or rest.
                if self._scene_queue:
                    state, hold = self._scene_queue[0]
                    self._scene_queue = self._scene_queue[1:]
                    self._enter_state(state)
                    self._scene_hold_ms = max(0, int(hold))
                    self._scene_elapsed_ms = 0
                else:
                    self._scene_hold_ms = 0
                    self._idle_elapsed_ms = 0
                    self._drift_at_ms = self._next_drift_ms()
                    self._enter_state(self._idle_state)
        else:
            # Resting: occasionally drift to GROOM/SLEEP for gentle life.
            self._idle_elapsed_ms += self._anim_ms
            if (not self._reduce_motion
                    and self._idle_state in (PetState.IDLE,)
                    and self._state == self._idle_state
                    and self._idle_elapsed_ms >= self._drift_at_ms):
                self._idle_elapsed_ms = 0
                self._drift_at_ms = self._next_drift_ms()
                drift = random.choice([PetState.GROOM, PetState.SLEEP,
                                       PetState.STRETCH, PetState.IDLE])
                if drift is PetState.IDLE:
                    self._enter_state(self._idle_state)
                else:
                    # A brief drift scene, then back to resting.
                    self._play_scene([(drift, random.randint(2500, 5000))])

        self.update()

    # ------------------------------------------------------------------ #
    # FRAME ACCESS (sprite sheet + cache, with procedural fallback)       #
    # ------------------------------------------------------------------ #
    def _frames_for(self, state: PetState):
        """Return the (cached) raw frame pixmaps for a state, or None.

        None signals the procedural fallback path (no valid sprite sheet)."""
        if self._sheet is None or not self._sheet.is_valid():
            return None
        key = (state, self._facing)
        cached = self._frame_cache.get(key)
        if cached is None:
            try:
                cached = self._sheet.frames(state, self._facing)
            except Exception:
                cached = None
            self._frame_cache[key] = cached or []
            cached = self._frame_cache[key]
        return cached or None

    def _current_frame(self) -> QPixmap | None:
        """The current raw sprite frame (unscaled), or None for the fallback."""
        frames = self._frames_for(self._state)
        if not frames:
            return None
        idx = self._frame_index % len(frames)
        pix = frames[idx]
        if pix is None or pix.isNull():
            return None
        return pix

    def current_pixmap(self) -> QPixmap:
        """Public helper (used by tests/validation): the current frame, scaled to
        fit the widget. Always returns a non-null pixmap (sprite or fallback)."""
        return self._scaled_current(self.width(), self.height())

    def _scaled_current(self, avail_w: int, avail_h: int) -> QPixmap:
        """The current frame scaled (aspect-preserving, smooth) to fit a box.

        Reserves the top ~30% of the widget for the speech bubble, mirroring the
        old layout's footprint. Falls back to a drawn cat if no sprite frame."""
        box_scale = STATE_BOX_SCALE.get(self._state, 1.0)
        box_w = max(1, int(avail_w * box_scale))
        box_h = max(1, int(avail_h * 0.7 * box_scale))
        raw = self._current_frame()
        if raw is None:
            return self._fallback_pixmap(box_w, box_h)
        ckey = (self._state, self._facing, self._frame_index % 999, box_w, box_h)
        cached = self._scaled_cache.get(ckey)
        if cached is not None:
            return cached
        scaled = raw.scaled(
            box_w, box_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Bound the cache so it can't grow without limit during long runs.
        if len(self._scaled_cache) > 256:
            self._scaled_cache.clear()
        self._scaled_cache[ckey] = scaled
        return scaled

    # ------------------------------------------------------------------ #
    # INTERNAL TIMERS / HELPERS                                           #
    # ------------------------------------------------------------------ #
    def _hide_bubble(self) -> None:
        self._bubble_text = ""
        self.update()

    def _update_hearts(self) -> None:
        """Move hearts upward and fade them out (petting particles)."""
        alive = []
        for h in self._hearts:
            h["y"] -= 2.5
            h["life"] -= 1
            if h["life"] > 0:
                alive.append(h)
        self._hearts = alive
        if not self._hearts:
            self._hearts_timer.stop()
        self.update()

    def _stop_timers(self) -> None:
        """Stop every QTimer (called on hide/close/destroy)."""
        for name in ("_anim_timer", "_bubble_timer", "_hearts_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    # The underlying C++ object may already be gone on destroy.
                    pass

    # ------------------------------------------------------------------ #
    # QWidget lifecycle: pause animation when not visible                 #
    # ------------------------------------------------------------------ #
    def hideEvent(self, event):
        # Pause the heartbeat while hidden — no point animating off-screen.
        self._anim_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._anim_timer.isActive():
            self._anim_timer.start(self._anim_ms)
        super().showEvent(event)

    def closeEvent(self, event):
        self._stop_timers()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # INTERACTION: click = pet                                            #
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Don't pet yet — wait to see if this becomes a drag-OUT (grab the cat
            # and pull it onto the desktop) or stays a click (pet).
            self._press_global = event.globalPosition().toPoint()
            self._maybe_drag = True
            self._dragging_out = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_maybe_drag", False) and (event.buttons() & Qt.MouseButton.LeftButton):
            g = event.globalPosition().toPoint()
            if not self._dragging_out:
                if (g - self._press_global).manhattanLength() >= 8:
                    self._dragging_out = True
                    self.dragged_out.emit(g)   # host shows the desktop cat under the cursor
            else:
                self.drag_out_move.emit(g)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and getattr(self, "_maybe_drag", False):
            self._maybe_drag = False
            if self._dragging_out:
                self._dragging_out = False
                self.drag_out_drop.emit(event.globalPosition().toPoint())
            else:
                self._pet_me()                 # no real movement -> it was a click
        super().mouseReleaseEvent(event)

    def _pet_me(self) -> None:
        """Petting: PETTED pose + a few floating hearts + the petted signal."""
        # 1) Show the happy petting scene briefly, then rest.
        self._play_scene([(PetState.PETTED, 1200), (PetState.LOVE, 900)])
        # 2) Spawn hearts above the cat (skipped under reduce-motion).
        if not self._reduce_motion:
            cx = self.width() / 2.0
            for _ in range(5):
                self._hearts.append({
                    "x": cx + random.uniform(-24, 24),
                    "y": self.height() * 0.45 + random.uniform(-10, 10),
                    "life": random.randint(14, 24),
                })
            if not self._hearts_timer.isActive():
                self._hearts_timer.start(40)
        # 3) A happy line and the outward signal.
        self.say(random.choice(["Purr!", "Thank you!", "Mrr~", "<3"]), msec=1800)
        self.petted.emit()

    # ------------------------------------------------------------------ #
    # SIZING                                                              #
    # ------------------------------------------------------------------ #
    def sizeHint(self) -> QSize:
        # Similar footprint to the old hand-drawn widget.
        return QSize(200, 240)

    def minimumSizeHint(self) -> QSize:
        return QSize(160, 200)

    # ------------------------------------------------------------------ #
    # RENDERING                                                           #
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w, h = self.width(), self.height()
        pix = self._scaled_current(w, h)

        # Bottom-center the cat within the lower ~70% band of the widget.
        if pix is not None and not pix.isNull():
            px = (w - pix.width()) / 2.0
            py = h - pix.height() - h * 0.04
            painter.drawPixmap(int(px), int(py), pix)
            cat_top = py
        else:
            cat_top = h * 0.35

        # Hearts (on top of the cat).
        for hh in self._hearts:
            alpha = int(255 * min(1.0, hh["life"] / 18.0))
            painter.fillRect(
                QRectF(hh["x"], hh["y"], 9.0, 9.0),
                qcolor_rgba("danger", alpha),
            )

        # Speech bubble above the cat.
        if self._bubble_text:
            self._draw_bubble(painter, w, cat_top)

    def _draw_bubble(self, painter: QPainter, w: float, cat_top: float) -> None:
        """Draw a rounded speech bubble with a small downward tail.

        Long text is elided to the available width, so the bubble never grows
        past the widget or crashes on huge strings."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(FONTS["pixel"], 8)
        painter.setFont(font)
        fm = QFontMetrics(font)

        max_text_w = max(40, int(w - 24))
        text = fm.elidedText(self._bubble_text, Qt.TextElideMode.ElideRight,
                             max_text_w)
        text_w = min(fm.horizontalAdvance(text), max_text_w)
        pad = 10
        bub_w = text_w + pad * 2
        bub_h = fm.height() + pad * 2
        bx = (w - bub_w) / 2.0
        by = max(4.0, cat_top - bub_h - 12.0)

        # Bubble backdrop (a soft "glassy" card).
        painter.setPen(QColor(COLORS["accent"]))
        painter.setBrush(QColor(COLORS["elevated"]))
        rect = QRectF(bx, by, bub_w, bub_h)
        painter.drawRoundedRect(rect, 10, 10)

        # Bubble tail (a downward triangle).
        tail_cx = w / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(tail_cx - 7, by + bub_h - 1)
        path.lineTo(tail_cx + 7, by + bub_h - 1)
        path.lineTo(tail_cx, by + bub_h + 9)
        path.closeSubpath()
        painter.fillPath(path, QColor(COLORS["elevated"]))

        # Text.
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    # ------------------------------------------------------------------ #
    # PROCEDURAL FALLBACK CAT (only if the sprite sheet is unavailable)   #
    # ------------------------------------------------------------------ #
    def _fallback_pixmap(self, box_w: int, box_h: int) -> QPixmap:
        """A simple drawn cat so the widget never crashes without art.

        Cached by (state, frame, box) so we don't redraw every paint."""
        size = max(40, min(box_w, box_h))
        ckey = ("fallback", self._state, self._frame_index % 4, size)
        cached = self._scaled_cache.get(ckey)
        if cached is not None:
            return cached

        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = size / 2.0
        body_w = size * 0.5
        body_h = size * 0.42
        ground = size * 0.92
        p.setPen(QPen(FALLBACK_OUTLINE, max(2.0, size * 0.02)))

        if self._state is PetState.SLEEP:
            p.setBrush(QBrush(FALLBACK_FUR))
            p.drawEllipse(int(cx - body_w * 0.7), int(ground - body_h * 0.9),
                          int(body_w * 1.4), int(body_h * 0.9))
            p.setBrush(QBrush(FALLBACK_FUR_DARK))
            p.drawEllipse(int(cx - body_w * 0.3), int(ground - body_h * 0.7),
                          int(body_w * 0.5), int(body_h * 0.45))
        elif self._state in (PetState.SURPRISED, PetState.SULK):
            p.setBrush(QBrush(FALLBACK_FUR))
            arch_y = ground - body_h * 1.2
            p.drawChord(int(cx - body_w * 0.8), int(arch_y),
                        int(body_w * 1.6), int(body_h * 1.6), 0, 180 * 16)
            p.setBrush(QBrush(FALLBACK_EYE))
            p.drawEllipse(int(cx - size * 0.12), int(arch_y + body_h * 0.3),
                          int(size * 0.05), int(size * 0.05))
            p.drawEllipse(int(cx + size * 0.07), int(arch_y + body_h * 0.3),
                          int(size * 0.05), int(size * 0.05))
            if self._state is PetState.SURPRISED:
                p.drawText(int(cx - size * 0.02), int(arch_y - size * 0.04), "!")
        else:
            # Sitting cat (IDLE / GROOM / LOVE / PETTED / STRETCH / PLAY / MEOW).
            p.setBrush(QBrush(FALLBACK_FUR))
            body_top = ground - body_h
            p.drawEllipse(int(cx - body_w / 2), int(body_top),
                          int(body_w), int(body_h))
            head_r = size * 0.22
            head_cy = body_top - head_r * 0.6
            p.drawEllipse(int(cx - head_r), int(head_cy - head_r),
                          int(head_r * 2), int(head_r * 2))
            p.setBrush(QBrush(FALLBACK_FUR_DARK))
            ear = head_r * 0.7
            for sgn in (-1, 1):
                ex = cx + sgn * head_r * 0.6
                ey = head_cy - head_r * 0.7
                p.drawConvexPolygon(
                    _qpt(ex - ear * 0.5, ey + ear * 0.3),
                    _qpt(ex + ear * 0.5, ey + ear * 0.3),
                    _qpt(ex, ey - ear * 0.6),
                )
            p.setBrush(QBrush(FALLBACK_EYE))
            for sgn in (-1, 1):
                ex = cx + sgn * head_r * 0.4
                p.drawEllipse(int(ex - size * 0.025), int(head_cy - size * 0.02),
                              int(size * 0.05), int(size * 0.06))

        p.end()
        if len(self._scaled_cache) > 256:
            self._scaled_cache.clear()
        self._scaled_cache[ckey] = pix
        return pix


# --------------------------------------------------------------------------- #
# Small helper kept module-level to avoid importing QPoint at the top for one  #
# use; QPointF is fine for drawConvexPolygon.                                  #
# --------------------------------------------------------------------------- #
def _qpt(x: float, y: float):
    from PyQt6.QtCore import QPointF
    return QPointF(float(x), float(y))
