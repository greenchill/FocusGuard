# -*- coding: utf-8 -*-
"""
====================================================================================
  Desktop Pet ("shimeji"-style pixel cat) — a desktop pet kitten
  Single-file PyQt6 application.
====================================================================================

WHAT THIS IS
------------
A tiny pixel kitten lives on top of all windows on your desktop. It:
  * wanders left/right on its own along the bottom edge of the screen (above the taskbar);
  * falls under gravity if it ends up in the air;
  * can be grabbed with the LEFT mouse button and dragged — when released it falls;
  * the RIGHT button opens a menu with a "Shoo" item (quit).

The architecture is based on studied repositories (Konqi-Pet, openclaw-tamagotchi,
ALearningCurve/desktop-pet, DyberPet, and the canonical Shijima behavior model):
  * a single QWidget pet;
  * a simple finite state machine (Enum: IDLE / WALK / FALL / DRAG);
  * one fast game timer (~16 ms) — the only one that changes position/physics;
  * one slow behavior timer (~2–5 s) — only "decides" what to do next.

------------------------------------------------------------------------------------
PyQt6  vs  PyQt5  — what needs to change (only 2–3 lines):
------------------------------------------------------------------------------------
  1) Module imports. In PyQt6 the classes live in QtWidgets/QtGui/QtCore the same way,
     but in PyQt5 the TOP-level enum names are "flat":
         PyQt6:  Qt.WindowType.FramelessWindowHint, Qt.WidgetAttribute.WA_TranslucentBackground,
                 Qt.MouseButton.LeftButton, Qt.TransformationMode.FastTransformation
         PyQt5:  Qt.FramelessWindowHint,            Qt.WA_TranslucentBackground,
                 Qt.LeftButton,                     Qt.FastTransformation
  2) Mouse coordinates in events:
         PyQt6:  event.globalPosition().toPoint()   (returns QPointF -> .toPoint())
         PyQt5:  event.globalPos()                  (already a QPoint)
  3) Starting the event loop:
         PyQt6:  app.exec()
         PyQt5:  app.exec_()
  (Also QMenu.exec() in PyQt5 is exec_(); and High-DPI in PyQt6/Qt6 is on by default,
   so in PyQt5 you additionally set AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps.)
------------------------------------------------------------------------------------
"""

from __future__ import annotations

# Standard library
import sys
import enum
import random
from pathlib import Path

# PyQt6 — split into submodules, as is customary in Qt
from PyQt6.QtCore import Qt, QTimer, QSize, QPoint, QElapsedTimer
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QMovie, QTransform, QGuiApplication, QAction
)
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QMenu


# ====================================================================================
#  CONFIGURATION / CONSTANTS BLOCK
#  ALL settings live here. No "scattered magic numbers" throughout the code.
# ====================================================================================

# --- Scale and frames --------------------------------------------------------------
SCALE = 3                 # How many times to enlarge the pixel art. Integer -> pixels stay crisp.
FPS = 60                  # Game-loop rate (physics + position). 60 frames/s.
GAME_TICK_MS = max(8, 1000 // FPS)   # Fast-timer interval in ms (>=8, i.e. no faster than ~120 fps).
ANIM_FPS = 8              # Frame-swap rate for PNG-sequence animation (slower than physics).
ANIM_TICK_MS = max(16, 1000 // ANIM_FPS)

# --- Physics -----------------------------------------------------------------------
# Gravity is integrated with Euler's method: each tick vy += GRAVITY; y += vy.
GRAVITY = 1.4             # Increment of vertical speed per tick (in "screen pixels/tick^2").
TERMINAL_VELOCITY = 28.0  # Fall-speed cap, so the cat does not "tunnel" through the floor in one frame.
WALK_SPEED = 3            # Horizontal walk speed (pixels per tick).

# --- Behavior (random "AI") --------------------------------------------------------
BEHAVIOUR_MIN_MS = 2000   # Minimum interval until the next "decision" (ms).
BEHAVIOUR_MAX_MS = 5000   # Maximum interval.
WALK_MIN_TICKS = 60       # Minimum walk duration (in game ticks).
WALK_MAX_TICKS = 220      # Maximum walk duration.
P_WALK = 0.6              # Probability of choosing "walk" over "stand still" at each decision.

# --- Dragging ----------------------------------------------------------------------
CLICK_DRAG_THRESHOLD_PX = 4   # Shift (in px) after which a press counts as a drag, not a click.

# --- Assets ------------------------------------------------------------------------
# The assets folder sits NEXT TO this file. Path(__file__).resolve().parent works
# correctly even if the path contains spaces or Hebrew (as for the current user).
ASSETS_DIRNAME = "assets"

# Base "logical" size of the programmatically drawn fallback cat (before scaling).
FALLBACK_W = 24
FALLBACK_H = 18

# Names of the GIF files we look for per state.
# If walk_right is missing it is mirrored from walk_left; if fall/drag are missing they fall back to idle.
GIF_NAMES = {
    "idle":       "idle.gif",
    "walk_left":  "walk_left.gif",
    "walk_right": "walk_right.gif",
    "fall":       "fall.gif",
    "drag":       "drag.gif",
}

# Names of the FOLDERS with PNG sequences (an alternative to GIF). Inside each folder
# are numbered frames. The supported naming format is described below in _load_png_sequence().
PNG_DIRS = {
    "idle":       "idle",
    "walk_left":  "walk_left",
    "walk_right": "walk_right",
    "fall":       "fall",
    "drag":       "drag",
}


# ====================================================================================
#  FINITE STATE MACHINE STATES
# ====================================================================================
class State(enum.Enum):
    """
    The cat's four behaviors. The state machine is deliberately small and clear.
    Transitions:
        IDLE  -> WALK   : on the behavior timer (randomly), if grounded and not grabbed.
        WALK  -> IDLE   : when the walk duration has elapsed.
        WALK  -> WALK   : at the screen edge we turn around (flip direction and animation).
        *     -> FALL   : as soon as there is no support under the cat (y above the floor) and it is not held.
        *     -> DRAG   : on left-button press with a shift larger than the threshold.
        DRAG  -> FALL   : on mouse release (the cat falls to the floor).
        FALL  -> IDLE   : on touching the floor (snap to floor + reset speed).
    """
    IDLE = enum.auto()
    WALK = enum.auto()
    FALL = enum.auto()
    DRAG = enum.auto()


# ====================================================================================
#  HELPER: FRAME SOURCE FOR A SINGLE STATE
#  Unifies the two animation approaches: QMovie (GIF) and a list of QPixmap (PNG frames).
#  The pet does not care where a frame came from — it simply requests current_pixmap().
# ====================================================================================
class FrameSource:
    """
    A wrapper around a single set of frames. It can operate in one of these modes:
      * "movie"  — holds a live QMovie (GIF) that cycles its own frames;
      * "frames" — a list of pre-loaded and pre-scaled QPixmap.
    This lets the state machine stay unaware of asset-loading details.
    """

    def __init__(self) -> None:
        self.mode: str | None = None          # "movie" | "frames" | None
        self.movie: QMovie | None = None      # for GIF mode
        self.frames: list[QPixmap] = []       # for PNG-frames mode
        self._index = 0                       # current frame in "frames" mode

    # ----- Factories ------------------------------------------------------------
    @classmethod
    def from_movie(cls, movie: QMovie) -> "FrameSource":
        src = cls()
        src.mode = "movie"
        src.movie = movie
        # CacheAll caches every decoded frame -> smooth looping and access to currentPixmap().
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        movie.start()
        return src

    @classmethod
    def from_frames(cls, frames: list[QPixmap]) -> "FrameSource":
        src = cls()
        src.mode = "frames"
        src.frames = frames
        return src

    # ----- Working with frames --------------------------------------------------
    def advance(self) -> None:
        """Move to the next frame (only for 'frames' mode; a GIF cycles on its own)."""
        if self.mode == "frames" and self.frames:
            self._index = (self._index + 1) % len(self.frames)

    def current_pixmap(self) -> QPixmap | None:
        """Return the current frame as a QPixmap (or None if the source is empty)."""
        if self.mode == "movie" and self.movie is not None:
            pm = self.movie.currentPixmap()
            return pm if not pm.isNull() else None
        if self.mode == "frames" and self.frames:
            return self.frames[self._index]
        return None

    def reset(self) -> None:
        """Reset the animation to the start (called when entering a state)."""
        self._index = 0
        if self.mode == "movie" and self.movie is not None:
            self.movie.jumpToFrame(0)


# ====================================================================================
#  PET CLASS
# ====================================================================================
class Pet(QWidget):
    """
    The pet window. It is simultaneously:
      * a transparent frameless window on top of all windows;
      * the carrier of the state machine and physics;
      * the mouse handler (dragging + context menu).
    """

    def __init__(self) -> None:
        super().__init__()

        # ----------------------------------------------------------------------
        #  1) WINDOW FLAGS — WHY EXACTLY THESE
        # ----------------------------------------------------------------------
        # FramelessWindowHint   — removes the frame and title bar: only the sprite is visible.
        # WindowStaysOnTopHint  — the window is always above the rest (the pet "lives" over everything).
        # Tool                  — does NOT create a taskbar button and stays out of Alt-Tab.
        # All flags are combined in ONE setWindowFlags call via bitwise OR:
        # repeated calls would overwrite the previous flags.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        # ----------------------------------------------------------------------
        #  2) TRANSPARENCY — WHY THESE ATTRIBUTES ARE NEEDED
        # ----------------------------------------------------------------------
        # WA_TranslucentBackground — the window's own background is fully transparent, only
        #   the sprite's opaque pixels are drawn (its own alpha). Without it there
        #   would be a gray/black square around the cat.
        # WA_NoSystemBackground    — suppresses the "ghost" DWM square on Windows behind
        #   a transparent frameless window (a known Windows issue).
        # WA_ShowWithoutActivating — clicking the cat does NOT steal focus from the user's
        #   active window (for example, it does not interrupt typing).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Extra safety via stylesheet — some themes draw a background for QLabel/QWidget.
        self.setStyleSheet("background: transparent;")

        # QLabel — the carrier of the current sprite frame. We make it a transparent child.
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.label.setStyleSheet("background: transparent;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ----------------------------------------------------------------------
        #  3) LOADING ANIMATIONS (GIF -> PNG frames -> programmatic fallback)
        # ----------------------------------------------------------------------
        # sources: dict[State -> dict[direction_key -> FrameSource]]
        # For WALK we keep two directions (walk_left / walk_right).
        self.assets_dir = Path(__file__).resolve().parent / ASSETS_DIRNAME
        self._ensure_assets_dir()
        self.sources: dict[str, FrameSource] = {}
        self._load_all_sources()

        # ----------------------------------------------------------------------
        #  4) INITIAL STATE AND PHYSICS VARIABLES
        # ----------------------------------------------------------------------
        self.state: State = State.IDLE
        self.facing: int = 1          # +1 = facing right, -1 = left.
        self.vx: float = 0.0          # horizontal speed (for the throw on release).
        self.vy: float = 0.0          # vertical speed (gravity).
        self.walk_ticks_left: int = 0 # how many ticks are left to walk (for the WALK state).

        # Dragging
        self._drag_active = False                 # whether we are actually dragging (past the threshold)
        self._press_pos: QPoint | None = None     # global press point (for the threshold)
        self._drag_offset: QPoint = QPoint(0, 0)  # cursor->window-corner offset (to avoid a jump)
        self._last_mouse_pos: QPoint | None = None  # for estimating the throw speed

        # To control "repaint only when the frame has actually changed".
        self._last_pixmap_id: int | None = None

        # ----------------------------------------------------------------------
        #  5) STARTING POSITION — on the screen floor under the cursor (or on the primary).
        # ----------------------------------------------------------------------
        # Apply the first frame right away, so the window gets a size before being shown.
        self._apply_current_frame()
        geo = self._current_screen_geometry()
        start_x = geo.x() + (geo.width() - self.width()) // 2
        # "Floor" = bottom of the available area (above the taskbar). We use the EXCLUSIVE
        # boundary y+height (not bottom(), which is 1px higher — see _ground_y).
        start_y = geo.y() + geo.height() - self.height()
        self.move(start_x, start_y)

        # ----------------------------------------------------------------------
        #  6) TIMERS
        # ----------------------------------------------------------------------
        # Fast game timer: the ONLY one that changes position/physics/frame.
        # This is the architecture's main rule: a single "writer" of coordinates -> no jitter.
        self._elapsed = QElapsedTimer()     # measure the real dt instead of trusting a fixed interval
        self._elapsed.start()
        self.game_timer = QTimer(self)
        self.game_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.game_timer.timeout.connect(self._game_tick)
        self.game_timer.start(GAME_TICK_MS)

        # Animation timer for PNG frames (GIFs cycle on their own via QMovie).
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._anim_tick)
        self.anim_timer.start(ANIM_TICK_MS)

        # Behavior timer: only "makes decisions" (intent), does NOT touch the position.
        self.behaviour_timer = QTimer(self)
        self.behaviour_timer.setSingleShot(True)
        self.behaviour_timer.timeout.connect(self._behaviour_decide)
        self._schedule_next_behaviour()

        # Show the window (flags/attributes are already set BEFORE show -> no flicker).
        self.show()

    # ================================================================================
    #  ASSET LOADING
    # ================================================================================
    def _ensure_assets_dir(self) -> None:
        """
        Create the assets folder if it does not exist, and hint to the user what to put there.
        The cat will still launch regardless (on the programmatic fallback).
        """
        if not self.assets_dir.exists():
            try:
                self.assets_dir.mkdir(parents=True, exist_ok=True)
                print(f"[desktop-pet] Created assets folder: {self.assets_dir}")
            except OSError as exc:
                print(f"[desktop-pet] Failed to create assets folder: {exc}")
            print(
                "[desktop-pet] Put either GIF files there:\n"
                "    idle.gif, walk_left.gif, walk_right.gif, fall.gif, drag.gif\n"
                "  or folders with numbered PNG frames:\n"
                "    idle/, walk_left/, walk_right/, fall/, drag/  (for example 0.png, 1.png, ...)\n"
                "[desktop-pet] While there are no files yet, the built-in pixel cat is drawn."
            )

    def _load_all_sources(self) -> None:
        """
        Load frame sources for each "key" (idle / walk_left / walk_right /
        fall / drag). The preference order for EACH key:
            1) GIF file (QMovie),
            2) folder with PNG frames,
            3) None (filled in with a sensible fallback below).
        After loading we apply the degradation rules:
            walk_right missing -> mirror walk_left;
            fall/drag missing  -> use idle;
            idle missing       -> programmatic fallback.
        """
        for key in ("idle", "walk_left", "walk_right", "fall", "drag"):
            src = self._load_one_source(key)
            if src is not None:
                self.sources[key] = src

        # --- Degradation: walk_right from walk_left by mirroring -------------------
        # IMPORTANT: the mirror may turn out EMPTY (for example, a streaming GIF of unknown
        # length with an empty current frame at the moment of mirroring). An empty but non-None
        # source would "silently freeze" the walk. So we accept the mirror ONLY if it
        # actually yields a frame; otherwise we leave the key unfilled -> idle picks it up below.
        if "walk_right" not in self.sources and "walk_left" in self.sources:
            mirror = self._mirror_source(self.sources["walk_left"])
            if mirror.current_pixmap() is not None:
                self.sources["walk_right"] = mirror
        if "walk_left" not in self.sources and "walk_right" in self.sources:
            mirror = self._mirror_source(self.sources["walk_right"])
            if mirror.current_pixmap() is not None:
                self.sources["walk_left"] = mirror

        # --- Degradation: fall / drag from idle -----------------------------------
        for key in ("fall", "drag", "walk_left", "walk_right"):
            if key not in self.sources and "idle" in self.sources:
                # walk_* from idle is also acceptable (if only idle exists) — better than empty.
                self.sources[key] = self.sources["idle"]

        # --- Full fallback: nothing loaded at all ---------------------------------
        if "idle" not in self.sources:
            print("[desktop-pet] No assets found — the built-in pixel cat is enabled.")
            self.sources = self._build_fallback_sources()

    def _load_one_source(self, key: str) -> FrameSource | None:
        """Try to load one source: first the GIF, then the PNG folder."""
        # 1) GIF
        gif_path = self.assets_dir / GIF_NAMES[key]
        if gif_path.is_file():
            # Pass the ABSOLUTE path as a string (important for paths with spaces/Hebrew).
            movie = QMovie(str(gif_path))
            if movie.isValid():
                # Scale the GIF to SCALE based on its first frame's size.
                movie.jumpToFrame(0)
                base = movie.currentPixmap()
                # isValid() does not guarantee the first frame actually decodes
                # (there are "broken-but-valid" GIFs). If the first frame is empty, do NOT accept
                # this source: return None so degradation to idle/fallback kicks in,
                # otherwise the state would "hang" on an empty frame with no recovery.
                if base.isNull():
                    return None
                movie.setScaledSize(QSize(base.width() * SCALE, base.height() * SCALE))
                return FrameSource.from_movie(movie)

        # 2) PNG sequence
        png_dir = self.assets_dir / PNG_DIRS[key]
        if png_dir.is_dir():
            frames = self._load_png_sequence(png_dir)
            if frames:
                return FrameSource.from_frames(frames)

        return None

    def _load_png_sequence(self, folder: Path) -> list[QPixmap]:
        """
        Load a numbered PNG sequence from a folder.

        SUPPORTED NAMING FORMAT (intentionally simple, but functional):
          - take all *.png in the folder;
          - sort by the number extracted from the file name
            (understands '0.png', '1.png', 'frame_0.png', 'walk0010.png', '003.png', etc.);
          - files without digits go to the end in alphabetical order.
        Each frame is scaled with nearest neighbor (FastTransformation) -> crisp pixels.
        """
        png_files = sorted(folder.glob("*.png"), key=self._frame_sort_key)
        frames: list[QPixmap] = []
        for f in png_files:
            pm = QPixmap(str(f))
            if pm.isNull():
                continue  # broken/unreadable file — skip it, do not crash
            pm = pm.scaled(
                pm.width() * SCALE,
                pm.height() * SCALE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,  # nearest neighbor = crisp pixel art
            )
            frames.append(pm)
        return frames

    @staticmethod
    def _frame_sort_key(path: Path):
        """Frame sort key: (has_number, number_or_0, name)."""
        digits = "".join(ch for ch in path.stem if ch.isdigit())
        if digits:
            return (0, int(digits), path.name)
        return (1, 0, path.name)

    def _mirror_source(self, src: FrameSource) -> FrameSource:
        """
        Create a horizontally MIRRORED copy of a source. Used to
        derive walk_right from walk_left without separate assets (saves sprites).
        A GIF is turned into a list of mirrored frames; a frame list is mirrored too.
        Mirroring is done once and cached in the new FrameSource.
        """
        mirror = QTransform().scale(-1, 1)  # reflection across the X axis
        frames: list[QPixmap] = []

        if src.mode == "movie" and src.movie is not None:
            movie = src.movie
            count = movie.frameCount()
            if count <= 0:
                # Length unknown (streaming GIF) — mirror at least the current frame.
                pm = movie.currentPixmap()
                if not pm.isNull():
                    frames.append(pm.transformed(mirror))
            else:
                for i in range(count):
                    movie.jumpToFrame(i)
                    pm = movie.currentPixmap()
                    if not pm.isNull():
                        frames.append(pm.transformed(mirror))
                movie.jumpToFrame(0)
        elif src.mode == "frames":
            frames = [pm.transformed(mirror) for pm in src.frames]

        if not frames:
            # Just in case — an empty source returns None from current_pixmap().
            return FrameSource.from_frames([])
        return FrameSource.from_frames(frames)

    # ================================================================================
    #  PROGRAMMATIC FALLBACK — a pixel cat made of rectangles
    # ================================================================================
    def _build_fallback_sources(self) -> dict[str, FrameSource]:
        """
        Draw the cat with QPainter when there are no assets at all.
        We make two frames per state (a slight "breathing"/blink) so the cat
        looks alive. walk_right is derived by mirroring walk_left.
        Return a ready-made dict of sources for all keys.
        """
        idle_frames = [self._paint_cat(blink=False), self._paint_cat(blink=True)]
        walk_l = [self._paint_cat(step=0, facing=-1), self._paint_cat(step=1, facing=-1)]
        fall_frames = [self._paint_cat(falling=True)]
        drag_frames = [self._paint_cat(dragged=True)]

        idle_src = FrameSource.from_frames(idle_frames)
        walk_left_src = FrameSource.from_frames(walk_l)
        walk_right_src = self._mirror_source(walk_left_src)
        fall_src = FrameSource.from_frames(fall_frames)
        drag_src = FrameSource.from_frames(drag_frames)

        return {
            "idle": idle_src,
            "walk_left": walk_left_src,
            "walk_right": walk_right_src,
            "fall": fall_src,
            "drag": drag_src,
        }

    def _paint_cat(
        self,
        blink: bool = False,
        step: int = 0,
        facing: int = 1,
        falling: bool = False,
        dragged: bool = False,
    ) -> QPixmap:
        """
        Draw a single cat frame from colored rectangles on a TRANSPARENT canvas.
        We draw in "logical" pixels (FALLBACK_W x FALLBACK_H), then enlarge it
        SCALE times with nearest neighbor — yielding neat pixel art.
        The parameters change the pose (blink, leg step, falling, "hanging" while dragged).
        """
        # Transparent canvas of the logical size.
        img = QImage(FALLBACK_W, FALLBACK_H, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))  # fully transparent background

        p = QPainter(img)
        p.setPen(Qt.PenStyle.NoPen)

        body = QColor(120, 120, 130)      # gray body
        dark = QColor(70, 70, 80)         # dark details (ears/paws)
        pink = QColor(230, 150, 170)      # nose/inner ear
        eye = QColor(30, 30, 35)          # eye

        def rect(x, y, w, h, color):
            p.fillRect(int(x), int(y), int(w), int(h), color)

        # --- Body ---
        rect(4, 8, 14, 7, body)           # torso
        # --- Tail (sways a little while walking/falling) ---
        tail_up = 1 if (step == 1 or falling) else 0
        rect(17, 6 - tail_up, 3, 3, body)

        # --- Head ---
        rect(2, 4, 7, 7, body)
        # Ears
        rect(2, 2, 2, 3, dark)
        rect(6, 2, 2, 3, dark)
        rect(3, 3, 1, 1, pink)            # pink spot in the ear

        # --- Eye / blink ---
        if blink:
            rect(4, 7, 2, 1, eye)         # closed eye — a thin line
        else:
            rect(4, 6, 2, 2, eye)         # open eye
        rect(2, 8, 1, 1, pink)            # nose

        # --- Paws ---
        if dragged:
            # "Hanging": paws dangle downward.
            rect(5, 15, 2, 3, dark)
            rect(13, 15, 2, 3, dark)
        elif falling:
            # While falling the paws are splayed out.
            rect(3, 14, 2, 2, dark)
            rect(15, 14, 2, 2, dark)
        else:
            # Leg step: alternate the front/back paw position.
            if step == 0:
                rect(5, 15, 2, 2, dark)
                rect(13, 15, 2, 2, dark)
            else:
                rect(6, 15, 2, 2, dark)
                rect(12, 15, 2, 2, dark)

        p.end()

        # To QImage -> QPixmap, scale with nearest neighbor.
        pm = QPixmap.fromImage(img)
        pm = pm.scaled(
            FALLBACK_W * SCALE,
            FALLBACK_H * SCALE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        # facing==-1 is rendered via mirroring at the source level (_mirror_source),
        # so here the base frame always faces left for walk_left.
        return pm

    # ================================================================================
    #  WORKING WITH THE SCREEN (multi-monitor + taskbar)
    # ================================================================================
    def _current_screen_geometry(self):
        """
        Return the availableGeometry of the screen the cat is currently on.

        WHY availableGeometry, not geometry:
          availableGeometry EXCLUDES the taskbar/dock -> the cat "stands" ON the bar
          instead of hiding under it. geometry() would include the bar.

        Multi-monitor: take the screen under the window's center (screenAt). If the center
        is outside all screens (for example, mid-drag past an edge) — fall back to the primary.
        """
        center = self.frameGeometry().center()
        screen = QGuiApplication.screenAt(center)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _ground_y(self) -> int:
        """The "floor" Y coordinate: the cat's top when standing at the bottom of the available area."""
        geo = self._current_screen_geometry()
        # IMPORTANT: QRect.bottom() == y + height - 1 (the last pixel INSIDE the rectangle,
        # not "beyond" it). So the exclusive bottom boundary = y + height (== bottom()+1).
        # Without the +1 the cat would hang 1px above the taskbar (a constant gap).
        return geo.y() + geo.height() - self.height()

    # ================================================================================
    #  STATE MACHINE: ENTERING A STATE
    # ================================================================================
    def _enter(self, new_state: State) -> None:
        """
        Transition to a new state. Idempotent: re-entering the same state
        does not reset timers (otherwise the cat would "stick" and never finish the animation).
        """
        if new_state is self.state:
            return
        self.state = new_state

        if new_state is State.WALK:
            # On entering the walk we pick a duration; the direction is set separately.
            self.walk_ticks_left = random.randint(WALK_MIN_TICKS, WALK_MAX_TICKS)
        elif new_state is State.IDLE:
            self.vx = 0.0
            self.vy = 0.0
        elif new_state is State.FALL:
            # Do not aggressively zero vy — gravity gives a small initial downward "push".
            pass
        elif new_state is State.DRAG:
            self.vx = 0.0
            self.vy = 0.0

        # Reset the active source's animation to the start for a fresh look.
        src = self._active_source()
        if src is not None:
            src.reset()

    def _active_source(self) -> FrameSource | None:
        """Pick the frame source for the current state and facing direction."""
        if self.state is State.WALK:
            key = "walk_right" if self.facing >= 0 else "walk_left"
        elif self.state is State.FALL:
            key = "fall"
        elif self.state is State.DRAG:
            key = "drag"
        else:
            key = "idle"
        # We consider a source usable only if it actually yields a frame. A non-None but
        # EMPTY source (for example, a failed mirror) would otherwise "freeze" the sprite —
        # so in that case we fall back to idle (which is guaranteed to exist after the
        # full fallback in _load_all_sources).
        src = self.sources.get(key)
        if src is None or src.current_pixmap() is None:
            src = self.sources.get("idle")
        return src

    # ================================================================================
    #  BEHAVIOR ("AI") — slow timer, DECISIONS only, no movement
    # ================================================================================
    def _schedule_next_behaviour(self) -> None:
        """Schedule the next 'decision' after a random interval."""
        delay = random.randint(BEHAVIOUR_MIN_MS, BEHAVIOUR_MAX_MS)
        self.behaviour_timer.start(delay)

    def _behaviour_decide(self) -> None:
        """
        Decide what to do next. IMPORTANT: this timer does NOT move the cat and does NOT change
        coordinates — it only sets the "intent" (idle/walk + direction). The actual
        movement is done by the game timer. This way the two timers do not fight over the position.

        A decision is made only if the cat is GROUNDED and is NOT being dragged.
        """
        grounded = (self.state in (State.IDLE, State.WALK))
        if grounded and not self._drag_active:
            if random.random() < P_WALK:
                # Walk in a random direction.
                self.facing = random.choice((-1, 1))
                self._enter(State.WALK)
            else:
                self._enter(State.IDLE)
        # Reschedule the next decision.
        self._schedule_next_behaviour()

    # ================================================================================
    #  GAME LOOP — the only one that changes position/physics/frame
    # ================================================================================
    def _game_tick(self) -> None:
        """
        One step of the game loop (~16 ms). ALL movement happens here.
        Steps:
          1) Measure the real dt (in case of "hangs"), normalize it to the ideal frame.
          2) If dragging with the mouse — skip physics (the mouse sets the position).
          3) Otherwise: check for support -> fall if needed; handle walking;
             apply gravity; snap to the floor.
          4) Update the frame and fit the window size to the sprite.
        """
        # --- 1) dt normalization. Ideal frame = GAME_TICK_MS. Clamp the spike. ---
        elapsed_ms = self._elapsed.restart()
        dt = elapsed_ms / GAME_TICK_MS if GAME_TICK_MS else 1.0
        dt = max(0.1, min(dt, 3.0))   # do not let a huge dt after a "freeze" fly off screen

        if not self._drag_active:
            self._physics_step(dt)

        # --- 4) Frame + window size ---
        self._apply_current_frame()

    def _physics_step(self, dt: float) -> None:
        """Physics and state transitions (called only when the cat is NOT being dragged)."""
        ground_y = self._ground_y()
        x = self.x()
        y = self.y()

        # ----- Loss of support: if standing/walking but hanging above the floor -> fall -----
        if self.state in (State.IDLE, State.WALK) and y < ground_y - 1:
            self._enter(State.FALL)

        # ----- WALKING -----
        if self.state is State.WALK:
            geo = self._current_screen_geometry()
            x += int(WALK_SPEED * self.facing * dt)

            # Turn around at the screen edges: hit the edge, flip direction and animation.
            # right_limit uses the EXCLUSIVE boundary geo.x()+geo.width() (== right()+1),
            # otherwise the cat would fall 1px short of the right edge.
            left_limit = geo.x()
            right_limit = geo.x() + geo.width() - self.width()
            if x <= left_limit:
                x = left_limit
                self.facing = 1
                # Force a source switch for the new direction.
                src = self._active_source()
                if src is not None:
                    src.reset()
            elif x >= right_limit:
                x = right_limit
                self.facing = -1
                src = self._active_source()
                if src is not None:
                    src.reset()

            # Tick the walk-duration counter.
            self.walk_ticks_left -= 1
            if self.walk_ticks_left <= 0:
                self._enter(State.IDLE)

        # ----- GRAVITY / FALLING -----
        if self.state is State.FALL:
            # Euler integration: each tick accumulate speed, cap it at terminal.
            self.vy = min(self.vy + GRAVITY * dt, TERMINAL_VELOCITY)
            # IMPORTANT: clamp the DISPLACEMENT per frame, not just the speed. Otherwise on a
            # hang (dt up to 3.0) a step could reach TERMINAL_VELOCITY*3 = 84px per tick
            # and the cat would "teleport" downward in a jump. Clamping dy <= TERMINAL_VELOCITY
            # guarantees it never travels more than terminal speed in a single frame.
            dy = min(self.vy * dt, TERMINAL_VELOCITY)
            y += int(dy)
            # Horizontal "throw" (if the cat was tossed) — decays over time.
            x += int(self.vx * dt)
            self.vx *= 0.92  # air resistance, to make the arc pleasant

            # Landing: hard "snap" to the floor and switch to IDLE.
            if y >= ground_y:
                y = ground_y
                self.vy = 0.0
                self.vx = 0.0
                self._enter(State.IDLE)

        # ----- IDLE: make sure we stand exactly on the floor -----
        if self.state is State.IDLE:
            if y < ground_y - 1:
                # No support under us -> fall.
                self._enter(State.FALL)
            else:
                y = ground_y

        # ----- Horizontal clamp within the screen -----
        # The same off-by-one fix: the right exclusive boundary = geo.x()+geo.width()-width.
        geo = self._current_screen_geometry()
        x = max(geo.x(), min(x, geo.x() + geo.width() - self.width()))

        self.move(x, y)

    def _anim_tick(self) -> None:
        """
        Slow animation timer: advances the frame of PNG sources.
        (GIF sources cycle frames on their own via QMovie, so they do not need advancing.)
        """
        src = self._active_source()
        if src is not None and src.mode == "frames":
            src.advance()
        # The repaint happens in the game tick via _apply_current_frame().

    def _apply_current_frame(self) -> None:
        """
        Take the active source's current frame, set it on the QLabel, and fit the
        WINDOW size to the frame size.

        WHY resize the window to the frame every time:
          the window's transparent area must exactly match the sprite. Otherwise a "dead"
          transparent border would remain around the cat, catching clicks and
          preventing clicks on the windows beneath the cat.
        We repaint the QLabel only if the frame actually changed (fewer needless repaints).
        """
        src = self._active_source()
        if src is None:
            return
        pm = src.current_pixmap()
        if pm is None or pm.isNull():
            return

        # Fit the window and QLabel to the frame size.
        if self.size() != pm.size():
            # IMPORTANT (anchor at the BOTTOM edge): resize() resizes around the TOP-LEFT
            # corner, keeping top in place. If different states have sprites of
            # DIFFERENT heights (idle/fall/drag/real GIF/PNG), then after a frame change the
            # cat's bottom would "drift" away from the taskbar by (old_height - new_height) pixels
            # — the cat would sink under the bar or float above it. So we pin the BOTTOM:
            # we remember the bottom edge before resize and set top so the bottom matches.
            bottom = self.y() + self.height()
            self.resize(pm.size())
            self.label.resize(pm.size())
            self.label.move(0, 0)
            new_y = bottom - self.height()
            # On the ground the floor is computed from the NEW height — re-pin to the floor so the bottom
            # lands exactly on the taskbar. In the air (FALL/DRAG) we keep the bottom edge
            # so as not to break falling/dragging.
            if self.state in (State.IDLE, State.WALK):
                new_y = self._ground_y()
            self.move(self.x(), new_y)

        # Set the pixmap only if it changed (by object id/content).
        pid = id(pm)
        if pid != self._last_pixmap_id:
            self.label.setPixmap(pm)
            # WINDOW MASK BY ALPHA: the cat sprite is NOT rectangular, but the window is.
            # Without a mask the transparent corners of the bounding box still belong to the window:
            # clicks on them do NOT pass through to the window beneath the cat, and a right-click on
            # an empty corner would open the "Shoo" menu. pm.mask() builds a 1-bit mask from the alpha ->
            # the hit test follows the cat's silhouette exactly. Recomputed on every frame/size change.
            self.setMask(pm.mask())
            self._last_pixmap_id = pid

    # ================================================================================
    #  MOUSE: DRAGGING + CONTEXT MENU
    # ================================================================================
    def mousePressEvent(self, event) -> None:
        """
        Mouse press.
          Left button  — potential start of a drag. We remember the press point and
                 the cursor->window-corner offset so the window does NOT jump under the cursor.
          Right button — context menu (handled separately in contextMenuEvent).
        """
        if event.button() == Qt.MouseButton.LeftButton:
            # globalPosition() returns a QPointF -> .toPoint() is required (a PyQt6 quirk).
            gp = event.globalPosition().toPoint()
            self._press_pos = gp
            self._last_mouse_pos = gp
            # Offset = cursor - top-left corner of the window. Save the grab point.
            self._drag_offset = gp - self.frameGeometry().topLeft()
            self._drag_active = False  # becomes True only after the threshold is exceeded
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """
        Mouse movement with the left button held. While the shift is below the threshold it is still a "click".
        Past the threshold we treat it as a drag: switch to DRAG and move the window so
        the grab point stays under the cursor (without a jump).
        """
        if self._press_pos is None:
            super().mouseMoveEvent(event)
            return
        # We must check buttons() (plural) — which buttons are held RIGHT NOW.
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return

        gp = event.globalPosition().toPoint()

        # Click/drag threshold: a small jitter must not trigger a fall.
        if not self._drag_active:
            delta = gp - self._press_pos
            if abs(delta.x()) + abs(delta.y()) >= CLICK_DRAG_THRESHOLD_PX:
                self._drag_active = True
                self._enter(State.DRAG)

        if self._drag_active:
            # Estimate the speed for the throw on release.
            if self._last_mouse_pos is not None:
                self.vx = float(gp.x() - self._last_mouse_pos.x())
                self.vy = float(gp.y() - self._last_mouse_pos.y())
            self._last_mouse_pos = gp
            # Move the window: cursor minus the saved offset = window corner.
            self.move(gp - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        """
        Left-button release. If it was a drag — the cat falls (FALL) with the current
        speed (resulting in a throw). If it was just a click — no harm done,
        we return to normal life through the game loop.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_active:
                self._drag_active = False
                self._enter(State.FALL)   # released in the air -> fall to the floor
            self._press_pos = None
            self._last_mouse_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        """
        RIGHT click -> context menu with a single "Shoo" item
        that quits the application. exec() (no underscore — this is PyQt6) blocks
        and shows the menu at the global cursor point.
        """
        menu = QMenu(self)
        bye = QAction("Shoo", self)
        bye.triggered.connect(QApplication.quit)
        menu.addAction(bye)
        # event.globalPos() on contextMenuEvent in PyQt6 still returns a QPoint,
        # but for consistency we could take the cursor position. We use globalPos().
        menu.exec(event.globalPos())

    # ================================================================================
    #  CLEAN SHUTDOWN
    # ================================================================================
    def teardown(self) -> None:
        """
        Carefully stop ALL timers and QMovies. Idempotent (safe to call twice).
        Why: the high-frequency game timer (16 ms) and the animations must not "fire"
        on an already-being-destroyed widget during exit/destruction of the C++ object (a known
        destruction-order risk in PyQt). Called from closeEvent and from app.aboutToQuit.
        """
        for timer in (self.game_timer, self.anim_timer, self.behaviour_timer):
            if timer is not None:
                timer.stop()
        for src in self.sources.values():
            if src.movie is not None:
                src.movie.stop()

    def closeEvent(self, event) -> None:
        """On window close, stop timers/animations before the widget is destroyed."""
        self.teardown()
        super().closeEvent(event)


# ====================================================================================
#  ENTRY POINT
# ====================================================================================
def main() -> int:
    """
    Create the QApplication, the pet, and start the event loop.
    setQuitOnLastWindowClosed(True) — the application exits when the cat's window is closed
    (and we trigger the close from the "Shoo" menu item via QApplication.quit()).
    """
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    pet = Pet()
    # Keep a reference to the pet so it is not collected by the garbage collector.
    app._pet = pet  # type: ignore[attr-defined]

    # The exit path through "Shoo" calls QApplication.quit(): on its own it does NOT
    # trigger the window's closeEvent, so the timers could keep ticking while the event
    # loop unwinds. We connect the same teardown to aboutToQuit -> the timers and
    # QMovies are guaranteed stopped before the objects are destroyed, with no "dangling" timers.
    app.aboutToQuit.connect(pet.teardown)

    # In PyQt6 it is app.exec() (in PyQt5 it was app.exec_()).
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
