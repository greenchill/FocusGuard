# -*- coding: utf-8 -*-
"""
game_controller.py — the gamification brain of FocusGuard: it closes the focus loop.

Why a separate module: all of the "game" logic (Pomodoro session -> XP/combo ->
pet health -> levels -> unlocks -> 7-day stats/streak -> persistence) lives here,
in a single QObject, while the widgets stay purely visual.

Key idea — GameController is the AUTHORITATIVE CLOCK: a single 1 Hz QTimer
(session_tick) reads the wall clock via time.time(), advances the Session, accrues
focus seconds, awards XP/combo, heals/hurts the pet and pushes everything to the
dashboard, timer and stats. TimerWidget thus becomes a DISPLAY (its own countdown
is not used while the controller is active), and its Start/Pause/Stop buttons are
routed here.

Link to the detector: CameraController.distraction_alert(kind) -> on_distraction
(combo reset + damage + sound), while CameraController.is_focused() is read every
tick as "the person is focused right now". Pet reactions to a distraction are
already played by CameraController — here we only do the game bookkeeping + sound.

Nothing heavy is opened in __init__: ChimePlayer/PetState/Stats are lightweight,
the camera is not touched. The App is built offscreen without a camera.

English comments, English identifiers.
"""

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import vision.config as vision_config
from vision.session import Session
from vision.game import Combo, accessories_for, apply_preset
from vision.pet import PetState, XP_PER_LEVEL
from vision.stats import Stats
from vision.sound import ChimePlayer
from vision.phrases import say_for
from noise_player import BrownNoisePlayer
import firewall


# How often (in ticks) to push the "heavy" stats (week/goal) — once every 5 s.
_STATS_REFRESH_EVERY = 5
# Map of the internal detection kind -> sound event.
_DISTRACTION_SOUND = {"phone": "phone", "gaze": "away", "posture": "posture"}

# Mapping of accessory ids from vision.game.ACCESSORY_LEVELS to the real card ids
# in widget_petroom.ITEMS. The core hands out bowtie/collar/crown/hat by level, but
# the Pet-Room catalog only knows default/emerald/sunset/violet/ruby/frost/crown/glasses/scarf,
# so without this map petroom.set_unlocked(...) was a silent no-op for everything but
# 'crown'. Here each game id reveals a specific locked catalog card.
_ACCESSORY_TO_ROOM_ITEM = {
    "bowtie": "scarf",    # L3
    "collar": "glasses",  # L5
    "crown": "crown",     # L8
    "hat": "violet",      # L12
}


class GameController(QObject):
    """Owns Session, Combo, PetState, Stats, ChimePlayer and runs the game at 1 Hz."""

    # The host (MainWindow) listens to these to move the pet to the desktop when a
    # focus session starts and bring it home when the session ends.
    session_started = pyqtSignal()
    session_ended = pyqtSignal()

    def __init__(self, dashboard, timer_widget, stats_widget, petroom,
                 camera_controller, get_floating_pet=None, cfg=None, parent=None):
        super().__init__(parent)
        self.dashboard = dashboard
        self.timer_widget = timer_widget
        self.stats_widget = stats_widget
        self.petroom = petroom
        self.camera = camera_controller
        # Callback returning the floating cat (PetWindow) or None — lazily,
        # since the cat is created by a hotkey after the constructor runs.
        self._get_floating_pet = get_floating_pet or (lambda: None)

        # Config: use the one passed in (shared with the camera), otherwise load our own.
        self.cfg = cfg if cfg is not None else vision_config.load_config()

        # Whether the webcam focus-tracking is in use. When False the Pomodoro runs as a
        # pure timer: every focus second counts (no camera to verify focus), so XP/combo/
        # levels still progress. Kept in sync with the Settings "Use camera" toggle.
        self.camera_enabled = bool(self.cfg.get("use_camera", True))

        # --- Lightweight game objects (no device I/O) ---------------------- #
        scfg = self.cfg.get("session", {})
        self.daily_goal = int(scfg.get("daily_goal_minutes", 120))
        pet_cfg = self.cfg.get("pet", {})
        self.pet = PetState.load(
            name=pet_cfg.get("name", "Buddy"),
            species=pet_cfg.get("species", "cat"),
            survivability=int(pet_cfg.get("survivability", 5)),
            daily_goal_minutes=self.daily_goal,
        )
        self.stats = Stats.load()
        self.combo = Combo()
        self.chime = ChimePlayer(
            enabled=bool(self.cfg.get("sound_enabled", True)),
            volume=float(self.cfg.get("sound_volume", 0.35)),
        )
        # Looping brown noise during focus (off during breaks). Volume rides a touch
        # above the chime volume so it's present but not overpowering.
        self.noise = BrownNoisePlayer(
            volume=max(0.35, float(self.cfg.get("sound_volume", 0.35)) + 0.2))
        self.noise.set_enabled(bool(self.cfg.get("brown_noise", False)))

        self.session = None              # created in start_session()
        self._running = False
        self._paused = False
        # Focus-second accumulator: every full 60 s -> minutes -> XP.
        self._focus_second_acc = 0
        self._tick_count = 0

        # Authoritative clock: 1 Hz. Don't start until there's a session.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self.session_tick)

        # Connect the camera's distraction signal (if the controller emits it).
        if self.camera is not None and hasattr(self.camera, "distraction_alert"):
            self.camera.distraction_alert.connect(self.on_distraction)

        # Initial dashboard paint from the loaded state.
        self._push_gamification()
        self._refresh_unlocks()
        self._refresh_stats_widget()

    # ------------------------------------------------------------------ #
    #  Helper calls to the pet / sound                                    #
    # ------------------------------------------------------------------ #
    def _say(self, event, **kw):
        """Speak a phrase (from vision.phrases) via whichever cat is visible."""
        self._say_text(say_for(event, name=self.pet.name, **kw))

    def _say_text(self, text, msec=2500):
        """Route a phrase to the floating desktop cat if it's out (so phrases are
        visible during a session), otherwise to the in-app Pet card cat."""
        try:
            fp = self._get_floating_pet()
            if fp is not None and getattr(fp, "isVisible", lambda: False)():
                fp.say(text, msec)
                return
        except Exception:
            pass
        try:
            self.dashboard.pet.say(text, msec)
        except Exception:
            pass

    def _floating_engine(self):
        """Fetch the floating cat's engine if it exists and is visible; else None."""
        pet = None
        try:
            pet = self._get_floating_pet()
        except Exception:
            pet = None
        if pet is not None and getattr(pet, "isVisible", lambda: False)():
            return getattr(pet, "engine", None)
        return None

    def _happy_beat(self):
        """Happy animation on the floating cat (a nice moment on combo/level)."""
        engine = self._floating_engine()
        if engine is not None:
            try:
                engine.pet()
            except Exception:
                pass

    def _camera_suspend(self):
        """Release the camera device (pause). Best-effort, never raises into the FSM."""
        if self.camera is None:
            return
        fn = getattr(self.camera, "suspend", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _camera_resume(self):
        """Re-open the camera after a suspend (resume/stop). Best-effort, idempotent."""
        if self.camera is None:
            return
        fn = getattr(self.camera, "resume", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def set_camera_enabled(self, on: bool):
        """Sync the pure-timer/camera mode with the Settings 'Use camera' toggle."""
        self.camera_enabled = bool(on)

    def sync_detection_to_session(self):
        """Re-apply the active phase's detection-enabled state to the camera.

        Used when the camera is (re)enabled mid-session so a freshly opened worker
        immediately reflects the current focus/break state instead of staying stale
        until the next phase transition."""
        if self._running and self.session is not None:
            self._set_detection_from_session()

    def _set_detection_from_session(self):
        """Enable/disable camera detection based on the session phase (focus/break)."""
        if self.camera is None or self.session is None:
            return
        active = self.session.detection_active()
        for attr in ("set_detection_enabled", "set_detection_active"):
            fn = getattr(self.camera, attr, None)
            if callable(fn):
                try:
                    fn(active)
                except Exception:
                    pass
                break

    # ------------------------------------------------------------------ #
    #  Site blocking (hosts file) tied to session phases                  #
    # ------------------------------------------------------------------ #
    # These run only on phase transitions (rare), are wrapped in try/except, and
    # degrade gracefully without admin (a status line, never a crash). The hosts file
    # write itself is atomic and refuses without admin (see firewall.py).
    def _fw(self):
        """The firewall section of cfg (created if missing)."""
        return self.cfg.setdefault("firewall", {})

    def _block_sites(self):
        """Block during focus, if enabled. Surface (not raise) any failure."""
        fw = self._fw()
        if not fw.get("enabled", False):
            return
        if not fw.get("block_during_focus", True):
            return
        try:
            ok, msg = firewall.block(do_flush=fw.get("flush_dns", True))
            if not ok:
                # No admin / empty list / write error — tell the user, don't crash.
                self._status(f"Site block: {msg}")
        except Exception as exc:
            self._status(f"Site block error: {exc}")

    def _unblock_sites(self):
        """Remove the block (idempotent). Safe to call on break/stop/done/shutdown."""
        fw = self._fw()
        try:
            if firewall.is_blocked() and firewall.is_admin():
                firewall.unblock(do_flush=fw.get("flush_dns", True))
        except Exception as exc:
            self._status(f"Site unblock error: {exc}")

    def _status(self, message):
        """Surface a short message via whichever cat is visible (best effort)."""
        self._say_text(message, 4000)

    # ------------------------------------------------------------------ #
    #  Session lifecycle                                                  #
    # ------------------------------------------------------------------ #
    def start_session(self):
        """Build Session from cfg['session'], start the 1 Hz tick, enable detection."""
        # A fresh session always runs with the camera in its normal (un-suspended) state.
        # Clears any stale pause left by the snooze button or a previously paused session,
        # so the camera can never be stuck off at the start of a new session.
        self._camera_resume()
        scfg = self.cfg.get("session", {})
        self.session = Session(scfg)
        self.daily_goal = int(scfg.get("daily_goal_minutes", self.daily_goal))
        self._running = True
        self._paused = False
        self._focus_second_acc = 0
        self._tick_count = 0

        # Mode label + initial timer display.
        mode_label = {"pomodoro": "Pomodoro", "timer": "Timer", "off": "Free"}.get(
            self.session.mode, "Pomodoro")
        now = time.time()
        self._push_timer_display(now, mode_label)

        self._set_detection_from_session()
        self.chime.play("focus")
        self._say("focus_start", level=self.pet.level)
        # Focus phase begins -> start the looping brown noise (if enabled).
        if self.session.is_focus():
            self.noise.play()
        # Focus phase begins -> block distracting sites (if enabled + admin).
        self._block_sites()

        self._clock.start()
        # Tell the host the session is live -> send the pet onto the desktop.
        self.session_started.emit()

    def pause_session(self):
        """Pause: the clock freezes, the session phase is "frozen" by shifting the start."""
        if not self._running or self._paused:
            return
        self._paused = True
        self._clock.stop()
        # Pausing the Pomodoro physically turns the camera off (LED dark); resume
        # re-opens it. No-op when the camera is disabled in Settings.
        self._camera_suspend()
        # Pause the brown noise too (keeps its position so it continues on resume).
        self.noise.pause()
        # Remember how much of the current phase elapsed so resume doesn't "skip ahead".
        if self.session is not None and self.session.duration is not None:
            self._paused_elapsed = time.time() - self.session.state_started
        else:
            self._paused_elapsed = 0.0
        # Reflect 'paused' on the desktop time pill instead of a frozen live count.
        try:
            fp = self._get_floating_pet()
            if fp is not None and getattr(fp, "isVisible", lambda: False)():
                remaining = (self.session.remaining(time.time())
                             if self.session is not None else None)
                fp.set_time(remaining, "paused")
        except Exception:
            pass

    def resume_session(self):
        """Unpause: shift state_started so the phase continues from where it left off."""
        if not self._running or not self._paused:
            return
        self._paused = False
        # Re-open the camera that pause_session() released (no-op if disabled/never off).
        self._camera_resume()
        # Resume the brown noise if we un-pause back into a focus phase.
        if self.session is not None and self.session.is_focus():
            self.noise.play()
        if self.session is not None and self.session.duration is not None:
            self.session.state_started = time.time() - getattr(self, "_paused_elapsed", 0.0)
        self._clock.start()
        # Refresh the desktop pill back to the live countdown.
        if self.session is not None:
            mode_label = {"pomodoro": "Pomodoro", "timer": "Timer", "off": "Free"}.get(
                self.session.mode, "Pomodoro")
            self._push_timer_display(time.time(), mode_label)

    def toggle_pause(self):
        """Handy pause toggle (for the timer's Pause button)."""
        if self._paused:
            self.resume_session()
        else:
            self.pause_session()

    def is_session_active(self):
        """True when a focus/break session is currently running (used by confirm-on-quit)."""
        return bool(self._running and self.session is not None)

    def is_paused(self):
        """True when an active session is currently paused (clock frozen)."""
        return bool(self._running and self._paused)

    def stop_session(self):
        """Stop the session, save state, reset the timer display."""
        self._clock.stop()
        self._running = False
        self._paused = False
        self.session = None
        self._focus_second_acc = 0
        # Session over -> stop the brown noise (reset to the start for next time).
        self.noise.stop()
        # If we were stopped while paused the camera is suspended — re-open it so the
        # idle preview comes back (never leave the camera off after a session ends).
        self._camera_resume()
        # No active session -> there must be no live block (covers session_done too,
        # which routes through here). Idempotent.
        self._unblock_sites()
        # Mute detection (no session -> nothing to actively watch in the game).
        if self.camera is not None:
            for attr in ("set_detection_enabled", "set_detection_active"):
                fn = getattr(self.camera, attr, None)
                if callable(fn):
                    try:
                        fn(False)
                    except Exception:
                        pass
                    break
        # Reset the timer display to idle, then restore the ring to the configured
        # focus length (otherwise the idle ring would read 00:00 after a session).
        try:
            self.timer_widget.set_session_display(None, "idle", "Pomodoro", 0.0)
            focus_min = int(self.cfg.get("session", {}).get("focus_minutes", 50))
            self.timer_widget.set_duration(focus_min * 60)
        except Exception:
            pass
        self.save()
        # Session is over -> bring the pet back home (into the app card).
        self.session_ended.emit()

    # ------------------------------------------------------------------ #
    #  Authoritative 1 Hz tick                                            #
    # ------------------------------------------------------------------ #
    def session_tick(self):
        """One game step (once per second). Reads the wall clock via time.time()."""
        if not self._running or self.session is None or self._paused:
            return
        now = time.time()
        self._tick_count += 1

        # 1) Phase transitions (focus<->break, done).
        event = self.session.update(now)
        if event == "break_start":
            # Pause the brown noise FIRST so the gentle break chime is clearly audible.
            self.noise.pause()
            self.chime.play("break")
            self._say("break_start")
            self._set_detection_from_session()
            # Break begins -> unblock sites so the user can relax (if configured).
            if self._fw().get("unblock_during_break", True):
                self._unblock_sites()
        elif event == "focus_start":
            self.chime.play("focus")
            self._say("focus_start", level=self.pet.level)
            # Defensive: a fresh focus phase always runs with the camera live.
            self._camera_resume()
            # Back to focus -> resume the brown noise from where it left off.
            self.noise.play()
            self._set_detection_from_session()
            # Back to focus -> re-apply the block.
            self._block_sites()
        elif event == "session_done":
            self._on_session_done()
            return  # session stopped inside _on_session_done

        # 2) Timer display (mm:ss + phase + ring).
        mode_label = {"pomodoro": "Pomodoro", "timer": "Timer", "off": "Free"}.get(
            self.session.mode, "Pomodoro")
        self._push_timer_display(now, mode_label)

        # 3) Focus accrual — only during the focus phase, when the camera sees
        #    focus and no calibration is in progress.
        if self.session.is_focus() and self._is_focused_now():
            self._accrue_focus_second()

        # 4) Regularly refresh the dashboard and (less often) the stats.
        self._push_gamification()
        if self._tick_count % _STATS_REFRESH_EVERY == 0:
            self._refresh_stats_widget()

    def _is_focused_now(self):
        """Whether to accrue a focus second this tick.

        Pure-timer mode (camera disabled in Settings): always True — there is no camera
        to verify focus, so the Pomodoro rewards the elapsed focus time like a normal
        timer. Camera mode: defer to CameraController.is_focused() (face present, no
        violation, fresh metrics, not calibrating)."""
        if not self.camera_enabled:
            return True
        if self.camera is None:
            return False
        fn = getattr(self.camera, "is_focused", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                return False
        return False

    def _accrue_focus_second(self):
        """One "clean" focus second: combo + health + stats + XP per minute."""
        # Combo grows with uninterrupted focus — but ONLY in camera mode, where a real
        # distraction can break it (on_distraction halves the streak). In pure-timer mode
        # there is no camera to ever reset it, so an escalating multiplier would inflate XP
        # without bound; keep the multiplier at x1 (XP == elapsed focus minutes).
        if self.camera_enabled:
            new_mult = self.combo.on_focus_tick(1.0)
            if new_mult is not None:
                self.chime.play("combo")
                self.dashboard.set_combo(new_mult)
                self._say("combo", mult=new_mult)
                self._happy_beat()

        # Pet health and stats.
        self.pet.on_focus_tick(1.0)
        self.stats.add_focus_seconds(1)

        # Every full 60 focus seconds -> minutes -> XP = minutes * multiplier.
        self._focus_second_acc += 1
        if self._focus_second_acc >= 60:
            self._focus_second_acc -= 60
            gained_minutes = 1 * self.combo.multiplier
            level_up = self.pet.add_focus_xp(gained_minutes)
            if level_up:
                self._on_level_up()

    def _on_level_up(self):
        """The pet leveled up: sound, phrase, badge and unlock refresh, save."""
        self.chime.play("reward")
        self.dashboard.set_level(self.pet.level)
        self._say("levelup", level=self.pet.level)
        self._happy_beat()
        self._refresh_unlocks()
        self.save()

    def _on_session_done(self):
        """Session finished: reward, badges, and a full stop with save."""
        self.chime.play("reward")
        self._say("session_done")
        # Visual praise scene on the embedded cat. The TimerWidget.finished->react
        # wiring is dead in controller mode (finished only fires for the standalone
        # countdown), so we trigger the praise pose here.
        try:
            self.dashboard.pet.react("praise")
        except Exception:
            pass
        self.stats.add_session_done()
        try:
            new_badges = self.stats.evaluate_badges(self.daily_goal)
        except Exception:
            new_badges = []
        for badge in new_badges:
            self._say("badge", badge=badge)
        self._refresh_stats_widget()
        self._push_gamification()
        self.stop_session()  # save() happens inside

    # ------------------------------------------------------------------ #
    #  Distraction (from CameraController.distraction_alert)              #
    # ------------------------------------------------------------------ #
    def on_distraction(self, kind):
        """Soft combo penalty + pet damage + stats + sound. The cat's reactions are the camera's job."""
        # SOFT penalty (combo grace): a brief slip should not nuke a long streak.
        # Instead of zeroing the combo, halve the accrued clean time (configurable).
        # The multiplier only drops if the halved time falls below a Combo threshold,
        # which happens automatically from the COMBO_STEPS in vision/game.py.
        soft_factor = float(self.cfg.get("combo_soft_factor", 0.5))
        self.combo.clean_seconds = max(0.0, self.combo.clean_seconds * soft_factor)
        self.dashboard.set_combo(self.combo.multiplier)

        # Health and stats (unchanged).
        self.pet.on_distraction()
        self.stats.add_distraction()
        if kind == "phone":
            self.stats.add_phone_event()

        # Sound by type: gaze -> 'away'.
        self.chime.play(_DISTRACTION_SOUND.get(kind, "away"))

        self._push_gamification()

    # ------------------------------------------------------------------ #
    #  Idle (no active session)                                           #
    # ------------------------------------------------------------------ #
    def on_idle_tick(self, seconds=1.0):
        """Gentle health decay without a session + a rare sad phrase.

        Call optionally (sparingly): the controller does NOT tick on its own
        when there's no session, so it won't burn health in the background
        without the user's knowledge.
        """
        if self._running:
            return
        self.pet.on_idle(seconds)
        self._push_gamification()

    def idle_say(self):
        """Occasionally remind the user with a sad phrase (no side effects)."""
        if not self._running:
            self._say("idle_sad")

    # ------------------------------------------------------------------ #
    #  Push state to the widgets                                          #
    # ------------------------------------------------------------------ #
    def _push_timer_display(self, now, mode_label):
        """Give TimerWidget the remaining time, phase, mode and ring fraction."""
        remaining = self.session.remaining(now) if self.session else None
        phase = self.session.state if self.session else "idle"
        # Ring fill fraction = elapsed/total (1.0 = time's up).
        duration = self.session.duration if self.session else None
        if duration and remaining is not None and duration > 0:
            fraction = 1.0 - (remaining / duration)
        else:
            fraction = 0.0
        try:
            self.timer_widget.set_session_display(remaining, phase, mode_label, fraction)
        except Exception:
            pass
        # Mirror the remaining time onto the desktop cat's time pill (when it's out
        # on the desktop) so the countdown is visible right next to the pet.
        try:
            fp = self._get_floating_pet()
            if fp is not None and getattr(fp, "isVisible", lambda: False)():
                fp.set_time(remaining, phase)
        except Exception:
            pass

    def _push_gamification(self):
        """Update XP/Level/Streak/Combo on the dashboard from the current state."""
        d = self.dashboard
        try:
            d.set_xp(self.pet.xp, XP_PER_LEVEL)
            d.set_level(self.pet.level)
            d.set_combo(self.combo.multiplier)
            d.set_streak(self.stats.streak(self.daily_goal))
        except Exception:
            pass

    def _refresh_stats_widget(self):
        """Redraw the weekly chart and the daily goal."""
        try:
            week = [m for _date, m in self.stats.last_n_days(7)]
            self.stats_widget.set_week_data(week)
            self.stats_widget.set_daily_goal(self.stats.focus_minutes(), self.daily_goal)
        except Exception:
            pass

    def _refresh_unlocks(self):
        """Unlock Pet-Room accessories based on the pet's current level.

        Game ids (bowtie/collar/crown/hat) are mapped to the real catalog card ids
        via _ACCESSORY_TO_ROOM_ITEM — otherwise set_unlocked() would be a silent no-op
        for everything but 'crown'. Unknown ids are passed through as-is (in case they match)."""
        if self.petroom is None:
            return
        try:
            for item_id in accessories_for(self.pet.level):
                room_id = _ACCESSORY_TO_ROOM_ITEM.get(item_id, item_id)
                self.petroom.set_unlocked(room_id, True)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Settings and persistence                                          #
    # ------------------------------------------------------------------ #
    def apply_preset(self, preset):
        """Apply a sensitivity preset to cfg and save config.json."""
        try:
            apply_preset(self.cfg, preset)
        except Exception:
            pass
        self.save_config()

    def set_daily_goal(self, minutes):
        """Change the daily goal: cfg + widget recompute + config save."""
        self.daily_goal = max(1, int(minutes))
        self.cfg.setdefault("session", {})["daily_goal_minutes"] = self.daily_goal
        self.pet.daily_goal_minutes = self.daily_goal
        self._refresh_stats_widget()
        self._push_gamification()
        self.save_config()

    def set_volume(self, volume_0_100):
        """Volume 0..100 -> ChimePlayer (0..1) + brown noise + cfg."""
        vol = max(0, min(100, int(volume_0_100))) / 100.0
        self.chime.volume = vol
        self.chime._cache.clear()  # re-synthesize tones for the new volume
        self.noise.set_volume(max(0.35, vol + 0.2))
        self.cfg["sound_volume"] = vol
        self.save_config()

    def set_brown_noise_enabled(self, on):
        """Enable/disable the focus brown noise (Settings + dashboard toggle).

        Persists the preference and, if turning it on mid-focus-phase, starts it now;
        turning it off stops playback immediately."""
        on = bool(on)
        self.cfg["brown_noise"] = on
        self.noise.set_enabled(on)
        if on and self._running and not self._paused \
                and self.session is not None and self.session.is_focus():
            self.noise.play()
        self.save_config()

    def save_config(self):
        """Save config.json (via vision.config)."""
        try:
            vision_config.save_config(self.cfg)
        except Exception:
            pass

    def save(self):
        """Save pet state and stats (on level-up/done/stop/quit)."""
        try:
            self.pet.save()
        except Exception:
            pass
        try:
            self.stats.save()
        except Exception:
            pass

    def shutdown(self):
        """Stop the clock and save everything (called on application exit)."""
        self._clock.stop()
        self._running = False
        self.noise.stop()
        # Final safety: never leave the user's sites blocked after exit (idempotent
        # with MainWindow._unblock_on_exit and app.aboutToQuit).
        self._unblock_sites()
        self.save()
